#!/usr/bin/env bash
#
# run-app.sh — khởi động và vận hành daily_stock_analysis trên máy cục bộ.
#
# Đóng gói quy trình trong .claude/skills/run-app/SKILL.md thành lệnh chạy được.
# Không đụng vào .env của repo: mọi override được ghi ra một file env tạm rồi
# trỏ ENV_FILE vào đó (STOCK_LIST / REPORT_LANGUAGE chỉ có hiệu lực khi nằm
# trong file env, không đọc từ biến môi trường của tiến trình).
#
# Hai chế độ khác hẳn nhau, đừng lẫn:
#
#   start  — DỊCH VỤ THẬT. Đọc thẳng .env của repo (DB thật data/stock_analysis.db),
#            chạy `main.py --serve --schedule`: FastAPI + Web UI + runtime scheduler
#            + bot client. Tương đương hai container analyzer/server trong
#            docker/docker-compose.yml gộp lại thành một tiến trình
#            (main.py:1636-1650 cho scheduler chạy trong tiến trình API).
#
#   serve  — MÔI TRƯỜNG KIỂM CHỨNG cô lập: .env tạm, DB tạm, thị trường/danh sách mã
#            cố định, không có scheduler. Dùng cho quy trình review.
#
# Dùng: ./scripts/run-app.sh [start|serve|dry-run|analyze|smoke|status|stop|build-web] [tuỳ chọn]
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --- Cấu hình: mọi giá trị đều override được qua biến môi trường ---------------
_RUN_TMP_BASE="${TMPDIR:-/tmp}"
RUN_DIR="${RUN_DIR:-${_RUN_TMP_BASE%/}/dsa-run}"
RUN_DIR="${RUN_DIR%/}"
# Ghi lại việc người dùng có tự đặt RUN_PORT/RUN_HOST hay không, TRƯỚC khi gán mặc định:
# chế độ `start` lấy cổng từ .env, còn bộ kiểm chứng dùng 8765 cố định.
RUN_PORT_OVERRIDE="${RUN_PORT:-}"
RUN_HOST_OVERRIDE="${RUN_HOST:-}"
RUN_PORT="${RUN_PORT:-8765}"
RUN_HOST="${RUN_HOST:-127.0.0.1}"
RUN_MARKETS="${RUN_MARKETS:-vn}"
RUN_STOCK_LIST="${RUN_STOCK_LIST:-HPG,FPT,VNM,VNINDEX,VN30}"
RUN_REPORT_LANGUAGE="${RUN_REPORT_LANGUAGE:-vi}"
RUN_MARKET_REVIEW="${RUN_MARKET_REVIEW:-false}"
RUN_WEBUI_AUTO_BUILD="${RUN_WEBUI_AUTO_BUILD:-false}"
RUN_HEALTH_TIMEOUT="${RUN_HEALTH_TIMEOUT:-60}"

ENV_TMP="$RUN_DIR/run.env"
DB_TMP="$RUN_DIR/run.db"
LOG_FILE="$RUN_DIR/server.log"
PID_FILE="$RUN_DIR/server.pid"
BASE_URL="http://${RUN_HOST}:${RUN_PORT}"

# Dịch vụ thật dùng PID/log riêng để `stop` và `status` phân biệt được hai chế độ.
# Log console ghi vào logs/ của repo — đúng chỗ docker-compose.yml:26 mount ../logs,
# và logs/ đã nằm trong .gitignore.
SERVICE_PID_FILE="$RUN_DIR/service.pid"
SERVICE_LOG_DIR="$REPO_ROOT/logs"
SERVICE_LOG_FILE="$SERVICE_LOG_DIR/service-console.log"

# Các khoá bị gỡ khỏi bản sao .env rồi ghi đè bằng giá trị của script.
OVERRIDE_KEYS='SUPPORTED_MARKETS|STOCK_LIST|REPORT_LANGUAGE|DATABASE_PATH|WEBUI_ENABLED|WEBUI_PORT|WEBUI_AUTO_BUILD|MARKET_REVIEW_ENABLED'

info() { printf '==> %s\n' "$*"; }
warn() { printf '[!] %s\n' "$*" >&2; }
die()  { printf '[x] %s\n' "$*" >&2; exit 1; }

# --- Môi trường Python --------------------------------------------------------
ensure_python() {
  if [[ -z "${VIRTUAL_ENV:-}" && -f .venv/bin/activate ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
    info "Đã kích hoạt .venv"
  fi
  command -v python >/dev/null 2>&1 || die "Không tìm thấy python. Tạo venv: python3.11 -m venv .venv && pip install -r requirements.txt"
  local ver
  ver="$(python -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  [[ "$ver" == "3.11" ]] || warn "Python $ver (repo dùng 3.11) — có thể gặp sai khác về dependency."
}

# --- Sinh file env tạm --------------------------------------------------------
# $1: giá trị WEBUI_ENABLED (true khi chạy dịch vụ, false khi chạy một lượt).
write_env_file() {
  local webui_enabled="$1"
  [[ -f .env ]] || die "Thiếu .env ở thư mục gốc repo. Sao chép từ .env.example rồi điền key trước."
  mkdir -p "$RUN_DIR"
  # Tạo file rỗng với quyền 600 TRƯỚC khi chép nội dung: .env chứa secret thật.
  : > "$ENV_TMP"
  chmod 600 "$ENV_TMP"
  { grep -vE "^[[:space:]]*(${OVERRIDE_KEYS})=" .env || true; } >> "$ENV_TMP"
  cat >> "$ENV_TMP" <<EOF

# --- ghi đè bởi scripts/run-app.sh ---
SUPPORTED_MARKETS=${RUN_MARKETS}
STOCK_LIST=${RUN_STOCK_LIST}
REPORT_LANGUAGE=${RUN_REPORT_LANGUAGE}
DATABASE_PATH=${DB_TMP}
WEBUI_ENABLED=${webui_enabled}
WEBUI_PORT=${RUN_PORT}
WEBUI_AUTO_BUILD=${RUN_WEBUI_AUTO_BUILD}
MARKET_REVIEW_ENABLED=${RUN_MARKET_REVIEW}
EOF
  info "Cấu hình chạy: $ENV_TMP (thị trường=$RUN_MARKETS, ngôn ngữ=$RUN_REPORT_LANGUAGE, DB=$DB_TMP)"
}

warn_if_no_llm_key() {
  if ! grep -qE '^[[:space:]]*(GEMINI|OPENAI|ANTHROPIC|DEEPSEEK|QWEN|MOONSHOT|ZHIPU)_API_KEY=.+' "$ENV_TMP"; then
    warn "Không thấy LLM API key trong .env — Web UI sẽ báo 'Base configuration incomplete'; chế độ dry-run vẫn chạy."
  fi
}

# --- Đọc giá trị từ .env ------------------------------------------------------
# Chỉ đọc, không bao giờ ghi: .env là file riêng của người dùng và chứa secret.
env_value() {
  local key="$1" default="${2:-}" line val
  [[ -f .env ]] || { printf '%s' "$default"; return 0; }
  line="$(grep -E "^[[:space:]]*${key}=" .env 2>/dev/null | tail -1 || true)"
  [[ -z "$line" ]] && { printf '%s' "$default"; return 0; }
  val="${line#*=}"
  val="$(printf '%s' "$val" | sed -e 's/[[:space:]]*$//' -e 's/^[[:space:]]*//' \
                                  -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/")"
  [[ -z "$val" ]] && val="$default"
  printf '%s' "$val"
}

# --- Quản lý cổng / tiến trình ------------------------------------------------
port_pids() { lsof -ti:"$1" -sTCP:LISTEN 2>/dev/null || true; }

# Dùng ĐÚNG phép thử mà main.py:1275-1281 thực hiện: bind() trần.
#
# Vì sao cần: `lsof -sTCP:LISTEN` chỉ thấy socket đang LISTEN. Sau khi dừng dịch vụ, các
# kết nối PHÍA CLIENT tới cổng đó vẫn có thể còn ở trạng thái CLOSED/TIME_WAIT và làm
# bind() thất bại. Thực tế quan sát được: VS Code tự forward cổng và giữ lại vài socket
# như vậy, khiến khởi động lại ngay sau khi stop bị lỗi "port is not available".
port_bindable() {
  python - "$1" "$2" <<'PY' 2>/dev/null
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
probe = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
try:
    probe.bind((host, port))
except OSError:
    sys.exit(1)
finally:
    probe.close()
PY
}

# Chờ tới khi cổng thực sự bind được, thay cho `sleep` cố định.
wait_port_bindable() {
  local host="$1" port="$2" timeout="${3:-45}" i
  for ((i = 1; i <= timeout; i++)); do
    port_bindable "$host" "$port" && return 0
    (( i == 1 )) && info "Chờ cổng $port giải phóng (socket có thể đang ở TIME_WAIT)..."
    sleep 1
  done
  return 1
}

free_port() {
  local host="${1:-$RUN_HOST}" port="${2:-$RUN_PORT}" pids
  pids="$(port_pids "$port")"
  if [[ -n "$pids" ]]; then
    info "Cổng $port đang bị chiếm (PID: $(echo "$pids" | tr '\n' ' ')) — dừng tiến trình cũ"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    sleep 2
    pids="$(port_pids "$port")"
    if [[ -n "$pids" ]]; then
      # shellcheck disable=SC2086
      kill -9 $pids 2>/dev/null || true
      sleep 1
    fi
  fi
  if ! wait_port_bindable "$host" "$port" 45; then
    warn "Cổng $port vẫn không bind được sau 45s. Tiến trình đang giữ cổng:"
    lsof -i:"$port" >&2 2>/dev/null || true
    warn "Kết nối phía client ở trạng thái CLOSED (ví dụ do VS Code tự forward cổng) cũng chặn bind."
    die "Đổi cổng bằng RUN_PORT=<cổng khác> hoặc đóng tiến trình giữ cổng ở trên."
  fi
}

# $1 base_url, $2 pid_file, $3 log_file, $4 timeout (tuỳ chọn)
wait_for_health() {
  local base_url="${1:-$BASE_URL}" pid_file="${2:-$PID_FILE}" log_file="${3:-$LOG_FILE}"
  local timeout="${4:-$RUN_HEALTH_TIMEOUT}" i
  for ((i = 1; i <= timeout; i++)); do
    if curl -sf "${base_url}/api/v1/health" >/dev/null 2>&1; then
      info "Dịch vụ sẵn sàng sau ${i}s — ${base_url}"
      return 0
    fi
    # Tiến trình chết sớm thì đừng chờ hết timeout.
    if [[ -f "$pid_file" ]] && ! kill -0 "$(cat "$pid_file")" 2>/dev/null; then
      break
    fi
    sleep 1
  done
  warn "Dịch vụ không phản hồi /api/v1/health. 40 dòng cuối của log:"
  tail -40 "$log_file" >&2 || true
  return 1
}

# --- Smoke test ---------------------------------------------------------------
run_smoke() {
  local sample_stock sample_index disabled_code code
  sample_stock="${RUN_SMOKE_STOCK:-HPG}"
  sample_index="${RUN_SMOKE_INDEX:-VNINDEX}"
  disabled_code="${RUN_SMOKE_DISABLED_CODE:-600519}"

  info "Tổng quan nguồn dữ liệu"
  curl -s "${BASE_URL}/api/v1/data/overview" | python -m json.tool | head -40

  info "Cổ phiếu Việt Nam: $sample_stock"
  curl -s "${BASE_URL}/api/v1/stocks/${sample_stock}/quote" | head -c 600; echo

  info "Chỉ số Việt Nam: $sample_index"
  curl -s "${BASE_URL}/api/v1/stocks/${sample_index}/quote" | head -c 600; echo

  code="$(curl -s -o /dev/null -w '%{http_code}' "${BASE_URL}/api/v1/stocks/${disabled_code}/quote")"
  if [[ "$code" == "404" ]]; then
    info "Mã thuộc thị trường đã tắt ($disabled_code) -> HTTP $code (đúng kỳ vọng)"
  else
    warn "Mã thuộc thị trường đã tắt ($disabled_code) -> HTTP $code (kỳ vọng 404) — cổng chặn thị trường có thể đã hỏng."
  fi
}

# Kiểm tra báo cáo không lẫn ký tự CJK. Dùng python vì BSD grep trên macOS
# không có -P.
check_cjk_leak() {
  local target="$1"
  [[ -e "$target" ]] || { warn "Không tìm thấy báo cáo để kiểm tra: $target"; return 0; }
  python - "$target" <<'PY'
import re, sys, pathlib
pattern = re.compile(r'[一-鿿぀-ヿ가-힯]')
paths = [pathlib.Path(sys.argv[1])]
if paths[0].is_dir():
    paths = sorted(paths[0].glob('report_*.md'))
leaked = False
for path in paths:
    for lineno, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if pattern.search(line):
            leaked = True
            print(f'{path}:{lineno}: {line.strip()[:160]}')
if leaked:
    print('\n[!] Báo cáo còn ký tự CJK. Văn bản tự do do LLM sinh thì chấp nhận được;')
    print('    nhưng nhãn, văn bản cố định và giá trị enum ra tiếng Trung là bug code.')
else:
    print('no CJK leak')
PY
}

# --- Tiền kiểm cho dịch vụ thật ------------------------------------------------
# Cảnh báo khi STOCK_LIST không có mã nào thuộc thị trường đang bật. Đây là bẫy thật:
# .env mặc định để STOCK_LIST là mã Trung Quốc, còn SUPPORTED_MARKETS không đặt nên
# rơi về "vn" (src/core/markets.py) — lịch chạy hằng ngày sẽ lọc sạch và không làm gì.
preflight_stock_list() {
  local stock_list markets
  stock_list="$(env_value STOCK_LIST)"
  markets="$(env_value SUPPORTED_MARKETS)"
  if [[ -z "$stock_list" ]]; then
    warn "STOCK_LIST rỗng trong .env — lịch chạy hằng ngày sẽ không có mã nào để phân tích."
    return 0
  fi
  SUPPORTED_MARKETS="$markets" python - "$stock_list" "$markets" <<'PY' || true
import sys
sys.path.insert(0, ".")
codes = [c.strip() for c in sys.argv[1].split(",") if c.strip()]
declared = sys.argv[2].strip()
try:
    from data_provider.base import _market_tag
    from src.core.markets import enabled_markets
except Exception as exc:  # pragma: no cover - chỉ là tiền kiểm, không chặn khởi động
    print(f"[!] Không kiểm tra được STOCK_LIST ({exc})")
    raise SystemExit(0)
enabled = enabled_markets()
matched = [c for c in codes if _market_tag(c) in enabled]
label = declared or f"(không đặt -> mặc định {','.join(enabled)})"
if matched:
    print(f"==> STOCK_LIST: {len(matched)}/{len(codes)} mã thuộc thị trường đang bật [{label}]")
else:
    print(f"[!] KHÔNG mã nào trong STOCK_LIST thuộc thị trường đang bật [{label}].")
    print(f"    STOCK_LIST = {', '.join(codes)}")
    print("    Lịch chạy hằng ngày sẽ lọc sạch và không phân tích gì.")
    print("    Sửa STOCK_LIST hoặc SUPPORTED_MARKETS trong .env (script không tự sửa file này).")
PY
}

# In trạng thái kênh thông báo. Dịch vụ thật tôn trọng .env, nên phải nói rõ lần chạy
# này có khả năng đẩy tin thật hay không.
preflight_notify() {
  local feishu_chat feishu_hook dingtalk_hook telegram
  feishu_chat="$(env_value FEISHU_CHAT_ID)"
  feishu_hook="$(env_value FEISHU_WEBHOOK_URL)"
  dingtalk_hook="$(env_value DINGTALK_WEBHOOK_URL)"
  telegram="$(env_value TELEGRAM_BOT_TOKEN)"
  if [[ -n "$feishu_chat$feishu_hook$dingtalk_hook$telegram" ]]; then
    warn "Có kênh thông báo đã cấu hình — lượt phân tích sẽ ĐẨY TIN THẬT. Dùng --no-notify nếu không muốn."
  else
    info "Thông báo: chưa kênh nào đủ cấu hình để gửi — sẽ không đẩy tin đi đâu."
  fi
}

preflight_frontend() {
  if [[ "$(env_value WEBUI_AUTO_BUILD true)" != "false" && ! -f "$REPO_ROOT/static/index.html" ]]; then
    warn "WEBUI_AUTO_BUILD=true và chưa có static/index.html — lần khởi động đầu sẽ chạy npm ci + build (lâu)."
    warn "Muốn nhanh hơn: $0 build-web"
  fi
}

# --- Các lệnh -----------------------------------------------------------------
# Dịch vụ thật: .env thật, DB thật, có scheduler. Tương đương analyzer + server của
# docker/docker-compose.yml gộp thành một tiến trình.
cmd_start() {
  local background=0 run_now=0 no_notify=0 arg
  for arg in "$@"; do
    case "$arg" in
      --background) background=1 ;;
      --run-now)    run_now=1 ;;
      --no-notify)  no_notify=1 ;;
      *) die "Tuỳ chọn không hiểu: $arg (dùng: --background, --run-now, --no-notify)" ;;
    esac
  done

  [[ -f .env ]] || die "Thiếu .env ở thư mục gốc repo. Sao chép từ .env.example rồi điền key trước."
  ensure_python

  local host port base_url
  host="${RUN_HOST_OVERRIDE:-$(env_value WEBUI_HOST 127.0.0.1)}"
  port="${RUN_PORT_OVERRIDE:-$(env_value WEBUI_PORT 8000)}"
  base_url="http://${host}:${port}"

  info "Chế độ DỊCH VỤ THẬT — đọc thẳng .env, ghi vào DB thật $(env_value DATABASE_PATH ./data/stock_analysis.db)"
  preflight_stock_list
  preflight_notify
  preflight_frontend

  # WEBUI_ENABLED/SCHEDULE_ENABLED trong .env có thể là false; bật bằng cờ dòng lệnh
  # (main.py:1613-1623 và :1644-1646) thay vì ghi đè file cấu hình của người dùng.
  local args=(--serve --schedule --host "$host" --port "$port")
  if (( run_now )); then
    warn "--run-now: sẽ phân tích toàn bộ STOCK_LIST ngay khi khởi động (tốn quota LLM)."
  else
    # .env đặt SCHEDULE_RUN_IMMEDIATELY=true nên mặc định phải chặn, nếu không vừa
    # start là chạy phân tích đầy đủ.
    args+=(--no-run-immediately)
  fi
  (( no_notify )) && args+=(--no-notify)

  free_port "$host" "$port"
  mkdir -p "$RUN_DIR" "$SERVICE_LOG_DIR"

  info "Khởi động: python main.py ${args[*]}"
  info "Log console: $SERVICE_LOG_FILE (log ứng dụng: $SERVICE_LOG_DIR/stock_analysis_*.log)"
  nohup python main.py "${args[@]}" > "$SERVICE_LOG_FILE" 2>&1 &
  echo $! > "$SERVICE_PID_FILE"

  if ! wait_for_health "$base_url" "$SERVICE_PID_FILE" "$SERVICE_LOG_FILE"; then
    rm -f "$SERVICE_PID_FILE"
    die "Khởi động dịch vụ thất bại."
  fi

  cat <<EOF

    Web UI  : ${base_url}/
    API doc : ${base_url}/docs
    DB      : $(env_value DATABASE_PATH ./data/stock_analysis.db)
    Lịch    : $(env_value SCHEDULE_TIMES "$(env_value SCHEDULE_TIME 18:00)") hằng ngày
    Log     : ${SERVICE_LOG_FILE}

EOF

  if (( background )); then
    info "Chạy nền, PID $(cat "$SERVICE_PID_FILE"). Dừng bằng: $0 stop"
    return 0
  fi

  info "Nhấn Ctrl+C để dừng."
  trap 'echo; info "Đang dừng..."; cmd_stop; exit 0' INT TERM
  wait "$(cat "$SERVICE_PID_FILE")" || true
}

cmd_serve() {
  local background=0
  [[ "${1:-}" == "--background" ]] && background=1

  ensure_python
  write_env_file true
  warn_if_no_llm_key
  free_port "$RUN_HOST" "$RUN_PORT"

  info "Khởi động dịch vụ (log: $LOG_FILE)"
  ENV_FILE="$ENV_TMP" nohup python main.py --serve-only > "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"

  if ! wait_for_health; then
    rm -f "$PID_FILE"
    die "Khởi động thất bại."
  fi

  run_smoke

  cat <<EOF

    Web UI : ${BASE_URL}/
    API doc: ${BASE_URL}/docs
    Log    : ${LOG_FILE}

Sau khi sửa frontend, thêm tham số truy vấn (ví dụ ${BASE_URL}/?v=2) để né cache bundle cũ,
và mở console trình duyệt một lượt — lỗi frontend chỉ in ra console, trên trang chỉ biểu hiện
thành "không có gợi ý xổ xuống".
EOF

  if (( background )); then
    info "Chạy nền, PID $(cat "$PID_FILE"). Dừng bằng: $0 stop"
    return 0
  fi

  info "Nhấn Ctrl+C để dừng."
  trap 'echo; info "Đang dừng..."; cmd_stop; exit 0' INT TERM
  wait "$(cat "$PID_FILE")" || true
}

cmd_dry_run() {
  ensure_python
  write_env_file false
  info "Chỉ lấy dữ liệu, không gọi LLM"
  ENV_FILE="$ENV_TMP" python main.py --dry-run
}

cmd_analyze() {
  local stocks="${1:-${RUN_STOCK_LIST%%,*}}"
  ensure_python
  # WEBUI_ENABLED phải là false, nếu không lệnh sẽ treo chạy dịch vụ sau khi phân tích xong.
  write_env_file false
  warn_if_no_llm_key
  info "Phân tích thật: $stocks (--no-notify: không đẩy thông báo ra kênh nào)"
  ENV_FILE="$ENV_TMP" python main.py --stocks "$stocks" --no-notify
  info "Kiểm tra lẫn ngôn ngữ trong báo cáo"
  check_cjk_leak "${RUN_REPORT_DIR:-reports}"
}

cmd_smoke() {
  curl -sf "${BASE_URL}/api/v1/health" >/dev/null 2>&1 \
    || die "Dịch vụ chưa chạy ở ${BASE_URL}. Khởi động trước: $0 serve --background"
  run_smoke
}

# Dừng theo cổng; trả 0 nếu có dừng cái gì đó.
stop_on_port() {
  local port="$1" label="$2" pids
  pids="$(port_pids "$port")"
  [[ -z "$pids" ]] && return 1
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  sleep 1
  pids="$(port_pids "$port")"
  # shellcheck disable=SC2086
  [[ -n "$pids" ]] && kill -9 $pids 2>/dev/null || true
  info "Đã dừng $label trên cổng $port."
  return 0
}

cmd_stop() {
  local service_port stopped=0
  service_port="${RUN_PORT_OVERRIDE:-$(env_value WEBUI_PORT 8000)}"

  stop_on_port "$RUN_PORT" "môi trường kiểm chứng" && stopped=1
  # Chỉ thử cổng dịch vụ thật khi nó khác cổng kiểm chứng, tránh báo trùng.
  if [[ "$service_port" != "$RUN_PORT" ]]; then
    stop_on_port "$service_port" "dịch vụ thật" && stopped=1
  fi
  (( stopped )) || info "Không có tiến trình nào đang nghe cổng $RUN_PORT hoặc $service_port."
  rm -f "$PID_FILE" "$SERVICE_PID_FILE"
}

cmd_status() {
  local service_port service_host any=0
  service_host="${RUN_HOST_OVERRIDE:-$(env_value WEBUI_HOST 127.0.0.1)}"
  service_port="${RUN_PORT_OVERRIDE:-$(env_value WEBUI_PORT 8000)}"

  report_one() {
    local label="$1" host="$2" port="$3" pid_file="$4" pids health
    pids="$(port_pids "$port")"
    [[ -z "$pids" ]] && return 1
    health="$(curl -s -o /dev/null -w '%{http_code}' "http://${host}:${port}/api/v1/health" 2>/dev/null || echo '---')"
    printf '  %-22s cổng=%-6s PID=%-18s health=%s\n' "$label" "$port" "$(echo "$pids" | tr '\n' ' ')" "$health"
    [[ -f "$pid_file" ]] && printf '  %-22s pidfile=%s\n' "" "$pid_file"
    return 0
  }

  info "Trạng thái"
  report_one "dịch vụ thật" "$service_host" "$service_port" "$SERVICE_PID_FILE" && any=1
  if [[ "$service_port" != "$RUN_PORT" ]]; then
    report_one "môi trường kiểm chứng" "$RUN_HOST" "$RUN_PORT" "$PID_FILE" && any=1
  fi
  (( any )) || info "  không có tiến trình nào đang chạy"
}

cmd_build_web() {
  info "Build frontend (sản phẩm ghi vào $REPO_ROOT/static/)"
  # npm run lint không kiểm tra kiểu; lỗi kiểu chỉ lộ ra ở npm run build.
  ( cd apps/dsa-web && npm run lint && npm run build )
}

usage() {
  cat <<EOF
Dùng: $0 <lệnh> [tuỳ chọn]

DỊCH VỤ THẬT (dùng .env và DB thật của repo):
  start [tuỳ chọn]       FastAPI + Web UI + runtime scheduler + bot, đọc thẳng .env.
                         Tương đương analyzer + server của docker-compose gộp một tiến trình.
                         --background   chạy nền
                         --run-now      phân tích ngay khi khởi động (mặc định: không)
                         --no-notify    không đẩy thông báo
  status                 Chế độ nào đang chạy, PID, cổng, health
  stop                   Dừng cả dịch vụ thật lẫn môi trường kiểm chứng

MÔI TRƯỜNG KIỂM CHỨNG (.env tạm + DB tạm, KHÔNG có scheduler):
  serve [--background]   Khởi động FastAPI + Web UI cô lập, chờ health, chạy smoke test (mặc định)
  dry-run                Chỉ lấy dữ liệu, không gọi LLM — cách smoke test nhanh nhất
  analyze [MÃ]           Chạy một lượt phân tích thật (cần LLM key), rồi kiểm tra lẫn ngôn ngữ
  smoke                  Chạy smoke test trên dịch vụ đang chạy sẵn

CHUNG:
  build-web              npm run lint && npm run build trong apps/dsa-web

Biến môi trường (đều có mặc định):
  RUN_DIR=$RUN_DIR
  RUN_PORT=$RUN_PORT                RUN_HOST=$RUN_HOST
  RUN_MARKETS=$RUN_MARKETS
  RUN_STOCK_LIST=$RUN_STOCK_LIST
  RUN_REPORT_LANGUAGE=$RUN_REPORT_LANGUAGE
  RUN_MARKET_REVIEW=$RUN_MARKET_REVIEW      RUN_WEBUI_AUTO_BUILD=$RUN_WEBUI_AUTO_BUILD
  RUN_HEALTH_TIMEOUT=$RUN_HEALTH_TIMEOUT

Lưu ý: RUN_MARKETS / RUN_STOCK_LIST / RUN_REPORT_LANGUAGE chỉ áp dụng cho môi trường
kiểm chứng. Lệnh start không ghi đè gì — mọi thứ theo .env, cổng lấy từ WEBUI_PORT.

Ví dụ:
  $0 start --background             # chạy dịch vụ thật ở nền
  $0 start --run-now                # dịch vụ thật, phân tích ngay khi khởi động
  $0 status                         # xem đang chạy gì
  $0 serve                          # môi trường kiểm chứng, giữ ở foreground
  RUN_PORT=9000 $0 serve            # đổi cổng
  RUN_MARKETS=vn,cn $0 dry-run      # bật lại thị trường khác để rollback thử
  $0 analyze FPT                    # phân tích thật một mã
EOF
}

case "${1:-serve}" in
  start)     shift || true; cmd_start "$@" ;;
  status)    cmd_status ;;
  serve)     shift || true; cmd_serve "$@" ;;
  dry-run)   cmd_dry_run ;;
  analyze)   shift || true; cmd_analyze "$@" ;;
  smoke)     cmd_smoke ;;
  stop)      cmd_stop ;;
  build-web) cmd_build_web ;;
  -h|--help|help) usage ;;
  *)         usage >&2; exit 2 ;;
esac
