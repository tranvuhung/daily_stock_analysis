<#
.SYNOPSIS
  Cài đặt và vận hành daily_stock_analysis (FastAPI + Web UI + scheduler + alert realtime)
  trên Windows, cho phép truy cập qua IP mạng LAN.
  Set up and run daily_stock_analysis as a LAN service on Windows.

.DESCRIPTION
  Toàn bộ dịch vụ chạy trong MỘT tiến trình `main.py --serve-only --schedule`:
  api/app.py (app_lifespan) khởi động runtime scheduler, làm mới stock index và
  worker alert realtime (MQTT TVSI) ngay trong tiến trình API.

  Script chỉ ĐỌC .env, không bao giờ ghi vào file đó (trừ lần `setup` đầu tiên
  khi chưa có .env thì sao chép từ .env.example).

  Lệnh:
    setup           Tạo .venv và cài dependency (chạy lại khi requirements.txt đổi)
    firewall        Mở cổng WEBUI_PORT cho profile mạng Private (cần Administrator)
    start           Chạy dịch vụ ở foreground (Ctrl+C để dừng); -Background để chạy nền
    stop            Dừng dịch vụ (và dừng scheduled task nếu đang chạy)
    status          Health, PID, URL truy cập qua LAN, trạng thái scheduled task
    logs            Theo dõi log ứng dụng mới nhất
    install-task    Đăng ký Task Scheduler tự chạy khi đăng nhập (-AtStartup: khi bật máy)
    uninstall-task  Gỡ scheduled task

  Xem docs/windows-lan-deploy.md.

.EXAMPLE
  .\scripts\run-server.ps1 setup
  .\scripts\run-server.ps1 start -Background
  .\scripts\run-server.ps1 status
#>
[CmdletBinding()]
param(
  [Parameter(Position = 0)]
  [ValidateSet('setup', 'firewall', 'start', 'stop', 'status', 'logs', 'install-task', 'uninstall-task', 'help')]
  [string]$Command = 'help',

  # start: chạy nền, log console ghi vào logs\server.out.log / logs\server.err.log
  [switch]$Background,
  # start: không đẩy thông báo ra kênh nào (dùng khi chạy thử)
  [switch]$NoNotify,
  # install-task: chạy khi bật máy dưới tài khoản SYSTEM thay vì khi người dùng đăng nhập
  [switch]$AtStartup
)

$ErrorActionPreference = 'Stop'
# Tắt thanh tiến trình của Invoke-WebRequest (làm chậm health check trên Windows PowerShell 5.1).
$ProgressPreference = 'SilentlyContinue'
# Log của ứng dụng có tiếng Trung và tiếng Việt; console mặc định của Windows sẽ lỗi mã hoá.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $RepoRoot

$EnvFile = Join-Path $RepoRoot '.env'
$VenvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$LogDir = Join-Path $RepoRoot 'logs'
$DataDir = Join-Path $RepoRoot 'data'
$PidFile = Join-Path $DataDir 'server.pid'
$OutLog = Join-Path $LogDir 'server.out.log'
$ErrLog = Join-Path $LogDir 'server.err.log'
$TaskName = 'DailyStockAnalysis'
$HealthTimeoutSec = 90

function Info([string]$Message) { Write-Host "==> $Message" }
function Warn([string]$Message) { Write-Host "[!] $Message" -ForegroundColor Yellow }
function Die([string]$Message) { Write-Host "[x] $Message" -ForegroundColor Red; exit 1 }

# --- Đọc .env (chỉ đọc) ---------------------------------------------------------
function Get-EnvValue([string]$Key, [string]$Default = '') {
  if (-not (Test-Path $EnvFile)) { return $Default }
  $value = $null
  foreach ($line in Get-Content -Path $EnvFile -Encoding UTF8) {
    if ($line -match "^\s*$([regex]::Escape($Key))\s*=(.*)$") {
      # Lấy dòng cuối cùng khớp, giống cách python-dotenv ghi đè.
      $value = $Matches[1].Trim()
    }
  }
  if ($null -eq $value) { return $Default }
  if ($value -match '^"(.*)"$' -or $value -match "^'(.*)'$") { $value = $Matches[1] }
  if ($value -eq '') { return $Default }
  return $value
}

function Get-BindHost { return (Get-EnvValue 'WEBUI_HOST' '127.0.0.1') }
function Get-BindPort { return [int](Get-EnvValue 'WEBUI_PORT' '8000') }

# Host để tự gọi health check: bind 0.0.0.0 thì gọi qua loopback.
function Get-ProbeHost([string]$BindHost) {
  if ($BindHost -in @('0.0.0.0', '::', '*', '')) { return '127.0.0.1' }
  return $BindHost
}

function Test-Health([string]$BindHost, [int]$Port) {
  $url = "http://$(Get-ProbeHost $BindHost):$Port/api/health"
  try {
    $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3
    return ($resp.StatusCode -eq 200)
  } catch {
    return $false
  }
}

function Get-ListenerPids([int]$Port) {
  try {
    return @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
      Select-Object -ExpandProperty OwningProcess -Unique)
  } catch {
    return @()
  }
}

function Get-LanAddresses {
  try {
    return @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
      Where-Object {
        $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and
        $_.AddressState -eq 'Preferred'
      } |
      Select-Object -ExpandProperty IPAddress)
  } catch {
    return @()
  }
}

function Test-IsAdmin {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  return (New-Object Security.Principal.WindowsPrincipal $identity).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Assert-Venv {
  if (-not (Test-Path $VenvPython)) { Die "Chưa có .venv. Chạy trước: .\scripts\run-server.ps1 setup" }
}

# --- setup ----------------------------------------------------------------------
# Chỉ nhận Python 3.10–3.12 (black trong pyproject.toml nhắm py310–py312, Docker dùng 3.11).
# Bản mới hơn (3.13/3.14) thiếu wheel dựng sẵn cho một số dependency đang pin, ví dụ
# tiktoken<0.12, khiến pip phải build từ source và đòi Rust compiler.
$SupportedMinors = @(10, 11, 12)

# Trả về "3.x" của một lệnh python, hoặc $null nếu không chạy được.
function Get-PythonVersion([string]$Exe, [string[]]$ExeArgs = @()) {
  if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { return $null }
  try {
    $ver = & $Exe @($ExeArgs + @('-c', 'import sys; print(''%d.%d'' % sys.version_info[:2])')) 2>$null
  } catch { return $null }
  if ($LASTEXITCODE -ne 0 -or -not $ver) { return $null }
  return "$ver".Trim()
}

function Test-SupportedVersion([string]$Version) {
  if (-not $Version) { return $false }
  $parts = $Version.Split('.')
  return ([int]$parts[0] -eq 3 -and [int]$parts[1] -in $SupportedMinors)
}

# Tìm Python được hỗ trợ, ưu tiên 3.11. Trả về cả danh sách bản đã thấy để báo lỗi rõ ràng.
function Find-BasePython {
  $candidates = @(
    @{ Exe = 'py'; Args = @('-3.11') },
    @{ Exe = 'py'; Args = @('-3.12') },
    @{ Exe = 'py'; Args = @('-3.10') },
    @{ Exe = 'python'; Args = @() },
    @{ Exe = 'py'; Args = @('-3') }
  )
  $seen = @()
  foreach ($c in $candidates) {
    $ver = Get-PythonVersion $c.Exe $c.Args
    if (-not $ver) { continue }
    $seen += $ver
    if (Test-SupportedVersion $ver) {
      return @{ Exe = $c.Exe; Args = $c.Args; Version = $ver; Seen = $seen }
    }
  }
  return @{ Exe = $null; Seen = ($seen | Select-Object -Unique) }
}

function Invoke-Setup {
  if (Test-Path $VenvPython) {
    # .venv tạo từ lần chạy trước có thể dùng bản Python không hỗ trợ (ví dụ 3.14).
    $venvVer = Get-PythonVersion $VenvPython
    if (-not (Test-SupportedVersion $venvVer)) {
      Die (".venv hiện có dùng Python $venvVer — chỉ hỗ trợ 3.10–3.12 (khuyến nghị 3.11). " +
        "Cài Python 3.11 rồi xoá .venv và chạy lại setup: Remove-Item -Recurse -Force .venv")
    }
  }
  if (-not (Test-Path $VenvPython)) {
    $base = Find-BasePython
    if (-not $base.Exe) {
      $found = if ($base.Seen) { "Tìm thấy: $($base.Seen -join ', ') — " } else { '' }
      Die ("Không tìm thấy Python 3.10–3.12. ${found}bản 3.13+ thiếu wheel cho tiktoken<0.12 (đòi Rust). " +
        'Cài Python 3.11 64-bit từ https://www.python.org/downloads/windows/ (có thể cài song song bản khác) rồi chạy lại.')
    }
    if ($base.Version -ne '3.11') { Warn "Dùng Python $($base.Version) (repo dùng 3.11) — có thể gặp sai khác về dependency." }
    Info "Tạo .venv bằng Python $($base.Version)"
    & $base.Exe @($base.Args + @('-m', 'venv', (Join-Path $RepoRoot '.venv')))
    if ($LASTEXITCODE -ne 0) { Die 'Tạo .venv thất bại.' }
  } else {
    Info "Dùng .venv có sẵn: $VenvPython"
  }

  Info 'Nâng cấp pip'
  & $VenvPython -m pip install -U pip
  if ($LASTEXITCODE -ne 0) { Die 'pip install -U pip thất bại.' }

  Info 'Cài requirements.txt (lần đầu có thể mất vài phút)'
  & $VenvPython -m pip install -r (Join-Path $RepoRoot 'requirements.txt')
  if ($LASTEXITCODE -ne 0) { Die 'Cài requirements.txt thất bại — xem gói lỗi ở trên và mục Troubleshooting trong docs/windows-lan-deploy.md.' }

  if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $RepoRoot '.env.example') $EnvFile
    Warn 'Chưa có .env — đã sao chép từ .env.example. Mở .env điền LLM key, kênh thông báo, TVSI_MQTT_* rồi mới start.'
  }

  if ((Get-EnvValue 'VNSTOCK_ENABLED' 'false') -eq 'true') {
    Info 'VNSTOCK_ENABLED=true — cài thêm requirements-vn-optional.txt'
    & $VenvPython -m pip install -r (Join-Path $RepoRoot 'requirements-vn-optional.txt')
    if ($LASTEXITCODE -ne 0) { Die 'Cài requirements-vn-optional.txt thất bại.' }
  }

  New-Item -ItemType Directory -Force -Path $LogDir, $DataDir | Out-Null
  Info 'Setup xong. Bước tiếp: sửa .env, chạy "firewall" (Administrator), rồi "start".'
}

# --- firewall -------------------------------------------------------------------
function Invoke-Firewall {
  if (-not (Test-IsAdmin)) { Die 'Cần mở PowerShell bằng "Run as Administrator" để tạo firewall rule.' }
  $port = Get-BindPort
  $ruleName = "DailyStockAnalysis Web UI (TCP $port)"
  Get-NetFirewallRule -DisplayName 'DailyStockAnalysis Web UI*' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
  # Chỉ mở cho profile Private: không lộ dịch vụ khi máy nối Wi-Fi công cộng.
  New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort $port -Profile Private | Out-Null
  Info "Đã tạo firewall rule: $ruleName (profile Private)"

  $public = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue | Where-Object { $_.NetworkCategory -eq 'Public' })
  foreach ($p in $public) {
    Warn "Mạng '$($p.Name)' đang ở profile Public — rule trên KHÔNG áp dụng. Nếu là mạng nhà/công ty tin cậy:"
    Warn "  Set-NetConnectionProfile -InterfaceIndex $($p.InterfaceIndex) -NetworkCategory Private"
  }
}

# --- start ----------------------------------------------------------------------
# Cảnh báo khi STOCK_LIST không có mã nào thuộc thị trường đang bật (bẫy có thật:
# lịch chạy hằng ngày sẽ lọc sạch và không làm gì). Cùng logic với scripts/run-app.sh.
function Test-StockListPreflight {
  $stockList = Get-EnvValue 'STOCK_LIST'
  if (-not $stockList) { Warn 'STOCK_LIST rỗng trong .env — lịch chạy hằng ngày sẽ không có mã nào để phân tích.'; return }
  $code = @'
import sys
sys.path.insert(0, '.')
codes = [c.strip() for c in sys.argv[1].split(',') if c.strip()]
try:
    from data_provider.base import _market_tag
    from src.core.markets import enabled_markets
except Exception as exc:
    print(f'[!] Không kiểm tra được STOCK_LIST ({exc})')
    raise SystemExit(0)
enabled = enabled_markets()
matched = [c for c in codes if _market_tag(c) in enabled]
if matched:
    print(f'==> STOCK_LIST: {len(matched)}/{len(codes)} mã thuộc thị trường đang bật [{",".join(enabled)}]')
else:
    print(f'[!] KHÔNG mã nào trong STOCK_LIST thuộc thị trường đang bật [{",".join(enabled)}]: {", ".join(codes)}')
    print('    Sửa STOCK_LIST hoặc SUPPORTED_MARKETS trong .env (script không tự sửa file này).')
'@
  # Ghi ra file tạm: Windows PowerShell 5.1 làm hỏng dấu ngoặc kép khi truyền chuỗi vào tiến trình ngoài.
  $tmp = Join-Path $env:TEMP "dsa-preflight-$PID.py"
  Set-Content -Path $tmp -Value $code -Encoding UTF8
  $prevSupported = $env:SUPPORTED_MARKETS
  try {
    $env:SUPPORTED_MARKETS = Get-EnvValue 'SUPPORTED_MARKETS'
    & $VenvPython $tmp $stockList
  } finally {
    $env:SUPPORTED_MARKETS = $prevSupported
    Remove-Item $tmp -ErrorAction SilentlyContinue
  }
}

function Invoke-Preflight([string]$BindHost, [int]$Port) {
  if (-not (Test-Path $EnvFile)) { Die 'Thiếu .env ở thư mục gốc. Chạy setup hoặc sao chép từ .env.example.' }

  if (-not (Test-Path (Join-Path $RepoRoot 'static\index.html'))) {
    Warn 'Thiếu static\index.html — Web UI sẽ hiện trang "chưa build". Build trên Mac bằng scripts/package-windows.sh rồi chép lại.'
  }
  if ((Get-EnvValue 'WEBUI_AUTO_BUILD' 'true') -ne 'false' -and -not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Warn 'WEBUI_AUTO_BUILD chưa đặt false và máy không có npm — nên đặt WEBUI_AUTO_BUILD=false vì static\ đã build sẵn.'
  }

  if ($BindHost -in @('0.0.0.0', '::', '*') -and (Get-EnvValue 'ADMIN_AUTH_ENABLED' 'false') -ne 'true') {
    Warn 'WEBUI_HOST mở ra mạng LAN nhưng ADMIN_AUTH_ENABLED chưa bật — ai cùng mạng cũng vào được. Nên đặt ADMIN_AUTH_ENABLED=true.'
  }
  if ($BindHost -in @('127.0.0.1', 'localhost')) {
    Warn "WEBUI_HOST=$BindHost — chỉ truy cập được trên chính máy này. Đặt WEBUI_HOST=0.0.0.0 để mở ra LAN."
  }

  # Lịch SCHEDULE_TIME chạy theo giờ máy.
  $offset = (Get-TimeZone).BaseUtcOffset
  if ($offset -ne [TimeSpan]::FromHours(7)) {
    Warn "Múi giờ máy là UTC$($offset.ToString('hh\:mm')) — lịch $(Get-EnvValue 'SCHEDULE_TIME' '18:00') sẽ chạy theo giờ này, không phải giờ Việt Nam."
  }

  if ((Get-EnvValue 'REALTIME_FEED_ENABLED' 'false') -eq 'true' -and (Get-EnvValue 'AGENT_EVENT_MONITOR_ENABLED' 'false') -ne 'true') {
    Warn 'REALTIME_FEED_ENABLED=true nhưng AGENT_EVENT_MONITOR_ENABLED chưa bật — feed MQTT TVSI sẽ không khởi động.'
  }

  Test-StockListPreflight

  $pids = Get-ListenerPids $Port
  if ($pids.Count -gt 0) {
    Die "Cổng $Port đang bị chiếm (PID: $($pids -join ', ')). Dừng bằng: .\scripts\run-server.ps1 stop — hoặc đổi WEBUI_PORT."
  }
}

function Invoke-Start {
  Assert-Venv
  $bindHost = Get-BindHost
  $port = Get-BindPort
  Invoke-Preflight $bindHost $port
  New-Item -ItemType Directory -Force -Path $LogDir, $DataDir | Out-Null

  $env:PYTHONUTF8 = '1'
  $env:PYTHONIOENCODING = 'utf-8'

  # --serve-only: không chạy phân tích một lượt lúc khởi động.
  # --schedule: bật runtime scheduler trong tiến trình API (main.py), kể cả khi .env để SCHEDULE_ENABLED=false.
  $pyArgs = @('main.py', '--serve-only', '--schedule', '--host', $bindHost, '--port', "$port")
  if ($NoNotify) { $pyArgs += '--no-notify' }

  Info "Khởi động: python $($pyArgs -join ' ')"
  Info "DB: $(Get-EnvValue 'DATABASE_PATH' './data/stock_analysis.db') — lịch: $(Get-EnvValue 'SCHEDULE_TIMES' (Get-EnvValue 'SCHEDULE_TIME' '18:00'))"

  if (-not $Background) {
    # Foreground: Task Scheduler cũng dùng chế độ này để theo dõi được vòng đời tiến trình.
    & $VenvPython @pyArgs
    exit $LASTEXITCODE
  }

  $proc = Start-Process -FilePath $VenvPython -ArgumentList $pyArgs -WorkingDirectory $RepoRoot `
    -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog -WindowStyle Hidden -PassThru
  Set-Content -Path $PidFile -Value $proc.Id -Encoding ASCII

  for ($i = 1; $i -le $HealthTimeoutSec; $i++) {
    if (Test-Health $bindHost $port) {
      Info "Dịch vụ sẵn sàng sau ${i}s (PID $($proc.Id))"
      Show-AccessUrls $bindHost $port
      Info 'Dừng bằng: .\scripts\run-server.ps1 stop'
      return
    }
    if ($proc.HasExited) { break }
    Start-Sleep -Seconds 1
  }
  Warn 'Dịch vụ không phản hồi /api/health. 40 dòng cuối của log:'
  if (Test-Path $ErrLog) { Get-Content $ErrLog -Tail 40 }
  if (Test-Path $OutLog) { Get-Content $OutLog -Tail 40 }
  Remove-Item $PidFile -ErrorAction SilentlyContinue
  Die 'Khởi động dịch vụ thất bại.'
}

function Show-AccessUrls([string]$BindHost, [int]$Port) {
  Write-Host ''
  Write-Host "    Trên máy này : http://127.0.0.1:$Port/"
  if ($BindHost -in @('0.0.0.0', '::', '*')) {
    foreach ($ip in Get-LanAddresses) { Write-Host "    Qua LAN      : http://${ip}:$Port/" }
  }
  Write-Host "    API doc      : http://127.0.0.1:$Port/docs"
  Write-Host "    Log ứng dụng : $LogDir"
  Write-Host ''
}

# --- stop / status / logs -------------------------------------------------------
function Invoke-Stop {
  $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if ($task -and $task.State -eq 'Running') {
    Info "Dừng scheduled task $TaskName"
    Stop-ScheduledTask -TaskName $TaskName
  }

  $stopped = $false
  $targets = @()
  if (Test-Path $PidFile) { $targets += [int](Get-Content $PidFile -TotalCount 1) }
  $targets += Get-ListenerPids (Get-BindPort)
  foreach ($procId in ($targets | Select-Object -Unique)) {
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if (-not $proc) { continue }
    # Chỉ dừng tiến trình python để không giết nhầm chương trình khác đang giữ cổng.
    if ($proc.ProcessName -notmatch '^python') {
      Warn "PID $procId ($($proc.ProcessName)) đang giữ cổng nhưng không phải python — bỏ qua."
      continue
    }
    Stop-Process -Id $procId -Force
    Info "Đã dừng PID $procId"
    $stopped = $true
  }
  Remove-Item $PidFile -ErrorAction SilentlyContinue
  if (-not $stopped) { Info 'Không có tiến trình dịch vụ nào đang chạy.' }
}

function Invoke-Status {
  $bindHost = Get-BindHost
  $port = Get-BindPort
  $pids = Get-ListenerPids $port
  $healthy = Test-Health $bindHost $port
  Info "Cổng $port — PID: $(if ($pids.Count) { $pids -join ', ' } else { '(không có)' }) — health: $(if ($healthy) { 'OK' } else { 'không phản hồi' })"
  if ($healthy) { Show-AccessUrls $bindHost $port }
  $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if ($task) { Info "Scheduled task $TaskName`: $($task.State)" } else { Info 'Chưa đăng ký scheduled task (install-task).' }
}

function Invoke-Logs {
  $latest = Get-ChildItem -Path $LogDir -Filter '*.log' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $latest) { Die "Chưa có file log trong $LogDir." }
  Info "Theo dõi $($latest.FullName) (Ctrl+C để thoát)"
  Get-Content -Path $latest.FullName -Tail 50 -Wait -Encoding UTF8
}

# --- Task Scheduler -------------------------------------------------------------
function Invoke-InstallTask {
  Assert-Venv
  $psExe = (Get-Process -Id $PID).Path
  $scriptPath = Join-Path $PSScriptRoot 'run-server.ps1'
  # Task chạy lệnh `start` ở foreground để Task Scheduler thấy tiến trình dừng/lỗi và tự khởi động lại.
  $action = New-ScheduledTaskAction -Execute $psExe `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`" start" `
    -WorkingDirectory $RepoRoot
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

  if ($AtStartup) {
    if (-not (Test-IsAdmin)) { Die '-AtStartup chạy dưới tài khoản SYSTEM, cần PowerShell "Run as Administrator".' }
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $when = 'khi bật máy (tài khoản SYSTEM)'
  } else {
    $user = "$env:USERDOMAIN\$env:USERNAME"
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $when = "khi $user đăng nhập"
  }

  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description 'daily_stock_analysis: FastAPI + Web UI + scheduler + alert realtime' -Force | Out-Null
  Info "Đã đăng ký scheduled task '$TaskName' chạy $when."
  Info "Chạy ngay: Start-ScheduledTask -TaskName $TaskName — kiểm tra: .\scripts\run-server.ps1 status"
}

function Invoke-UninstallTask {
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Info "Đã gỡ scheduled task '$TaskName'."
  } else {
    Info "Không có scheduled task '$TaskName'."
  }
}

switch ($Command) {
  'setup' { Invoke-Setup }
  'firewall' { Invoke-Firewall }
  'start' { Invoke-Start }
  'stop' { Invoke-Stop }
  'status' { Invoke-Status }
  'logs' { Invoke-Logs }
  'install-task' { Invoke-InstallTask }
  'uninstall-task' { Invoke-UninstallTask }
  default { Get-Help $PSCommandPath -Detailed }
}
