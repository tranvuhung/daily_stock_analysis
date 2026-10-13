# Chạy dịch vụ trên máy Windows, truy cập qua mạng LAN

Hướng dẫn build trên Mac/Linux, chép sang một máy Windows cá nhân rồi chạy **toàn bộ** dịch vụ: FastAPI, Web UI, lịch phân tích hằng ngày và cảnh báo realtime (MQTT TVSI). Sau đó điện thoại hoặc laptop cùng mạng truy cập qua `http://<IP-LAN>:8000`.

Hướng dẫn này dùng Python native và không cần Docker. Nếu muốn dùng bản cài desktop (Electron), xem [desktop-package.md](desktop-package.md).

## Cách hoạt động

- Mọi thành phần chạy trong **một tiến trình**: `python main.py --serve-only --schedule`.
  - `api/app.py` (`app_lifespan`) khởi động runtime scheduler, làm mới stock index và worker alert realtime ngay trong tiến trình API.
  - `--serve-only` không chạy phân tích lúc khởi động.
  - `--schedule` bật lịch phân tích hằng ngày, kể cả khi `.env` để `SCHEDULE_ENABLED=false`.
- Web UI là file tĩnh trong `static/`, FastAPI phục vụ cùng origin.
  - Không cần cấu hình CORS và không cần `VITE_API_URL`.
  - Cookie đăng nhập chỉ bật `secure` khi dùng https, nên chạy qua `http://192.168.x.x` vẫn đăng nhập được.
- `static/` build một lần trên Mac và dùng được trên mọi nền tảng. Máy Windows **không cần Node.js**.
- `.venv` thì **không** chép sang được: Windows phải cài lại dependency.

| Script | Chạy ở đâu | Vai trò |
| --- | --- | --- |
| `scripts/package-windows.sh` | Mac/Linux | Build Web UI, đóng gói mã nguồn + `static/` thành `dist/dsa-windows-<version>.zip` |
| `scripts/run-server.ps1` | Windows | `setup` / `firewall` / `start` / `stop` / `status` / `logs` / `install-task` / `uninstall-task` |

## 1. Đóng gói trên Mac

```bash
./scripts/package-windows.sh                 # build Web UI + đóng gói mã nguồn
./scripts/package-windows.sh --with-data     # kèm DB hiện tại và file mật khẩu admin trong data/
./scripts/package-windows.sh --skip-build    # dùng static/ đã build sẵn
```

- Gói lấy mã nguồn theo `git archive HEAD`. Thay đổi chưa commit sẽ **không** nằm trong gói (script có cảnh báo).
- `--with-data` snapshot `data/stock_analysis.db` bằng sqlite backup API. Không chép thẳng file `.db`, vì DB chạy chế độ WAL nên phần dữ liệu còn trong `-wal` sẽ mất.
- Mặc định **không** kèm `.env` vì file chứa API key và mật khẩu. Tự chép `.env` qua kênh tin cậy (USB, thư mục chia sẻ nội bộ).
  - `--with-env` kèm luôn `.env` vào zip. Đừng chia sẻ file zip đó.

Script in ra SHA256 của gói để đối chiếu sau khi chép sang Windows.

## 2. Chuẩn bị máy Windows

1. Cài **Python 3.11** 64-bit từ <https://www.python.org/downloads/windows/> và tick **Add python.exe to PATH**.
   - Chỉ hỗ trợ **3.10–3.12**. Bản 3.13/3.14 thiếu wheel dựng sẵn cho `tiktoken<0.12` (repo đang pin), nên pip phải build từ source và báo `can't find Rust compiler`.
   - Máy đã có bản mới hơn thì cứ cài 3.11 song song. `setup` tự chọn qua `py -3.11`.
2. Đặt múi giờ `(UTC+07:00) Bangkok, Hanoi, Jakarta`. Lịch `SCHEDULE_TIME` chạy theo giờ máy.
3. Đặt IP tĩnh cho máy, hoặc tạo DHCP reservation trên router, để địa chỉ truy cập không đổi.
4. Cho phép chạy script PowerShell cho người dùng hiện tại (chỉ làm một lần):

   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
   ```

   Nếu file zip tải qua mạng thì Windows gắn cờ "downloaded". Chạy `Get-ChildItem -Recurse | Unblock-File` trong thư mục đã giải nén.

## 3. Cài đặt

Giải nén vào một thư mục cố định, ví dụ `C:\dsa`. Sau đó mở PowerShell trong thư mục đó:

```powershell
Get-FileHash .\dsa-windows-<version>.zip -Algorithm SHA256   # so với SHA256 in ra trên Mac
.\scripts\run-server.ps1 setup
```

`setup` làm các việc sau:
- Tạo `.venv` và `pip install -r requirements.txt`. Nếu `VNSTOCK_ENABLED=true` thì cài thêm `requirements-vn-optional.txt`.
- Nếu chưa có `.env` thì sao chép từ `.env.example`.

Chạy lại `setup` mỗi khi `requirements.txt` thay đổi.

## 4. Cấu hình `.env`

Các khoá tối thiểu để chạy qua LAN. Các khoá khác (LLM key, kênh thông báo…) giữ như `.env` trên Mac.

```dotenv
# Mở ra mạng LAN
WEBUI_HOST=0.0.0.0
WEBUI_PORT=8000
WEBUI_AUTO_BUILD=false          # static/ đã build sẵn, Windows không cần npm
ADMIN_AUTH_ENABLED=true         # bắt buộc khi mở ra LAN
TRUST_X_FORWARDED_FOR=false     # không có reverse proxy

# Lịch phân tích hằng ngày
SCHEDULE_TIME=18:00
SCHEDULE_RUN_IMMEDIATELY=false

# Thị trường và danh sách mã
SUPPORTED_MARKETS=vn
STOCK_LIST=HPG,FPT,VNM

# Cảnh báo định kỳ + realtime qua MQTT TVSI
AGENT_EVENT_MONITOR_ENABLED=true
REALTIME_FEED_ENABLED=true
TVSI_MQTT_HOST=...
TVSI_MQTT_PORT=443
TVSI_MQTT_TLS=true
TVSI_MQTT_TRANSPORT=tcp
TVSI_MQTT_USERNAME=...
TVSI_MQTT_PASSWORD=...
```

- `DATABASE_PATH` mặc định là `./data/stock_analysis.db`, một đường dẫn tương đối. `run-server.ps1` luôn chạy từ thư mục gốc nên DB nằm ở `C:\dsa\data\`.
- Feed realtime chỉ khởi động khi **cả** `REALTIME_FEED_ENABLED` và `AGENT_EVENT_MONITOR_ENABLED` đều bật. Broker cần kết nối **ra ngoài** qua TCP 443.
- Ý nghĩa đầy đủ của từng khoá xem `.env.example` và [alerts.md](alerts.md).

## 5. Mở firewall

Mở PowerShell bằng **Run as Administrator**:

```powershell
.\scripts\run-server.ps1 firewall
```

- Lệnh tạo inbound rule cho cổng `WEBUI_PORT`, **chỉ ở profile Private**.
- Nếu mạng đang ở profile Public, script in ra lệnh `Set-NetConnectionProfile` để chuyển. Chỉ chuyển khi đó là mạng nhà hoặc mạng công ty tin cậy.

## 6. Chạy

```powershell
.\scripts\run-server.ps1 start               # foreground, Ctrl+C để dừng
.\scripts\run-server.ps1 start -Background   # chạy nền, chờ health rồi in URL truy cập
.\scripts\run-server.ps1 status              # PID, health, URL LAN, trạng thái scheduled task
.\scripts\run-server.ps1 logs                # theo dõi log ứng dụng mới nhất
.\scripts\run-server.ps1 stop
```

Trước khi khởi động, `start` tiền kiểm và cảnh báo khi gặp các trường hợp sau:
- Thiếu `static\index.html`.
- `WEBUI_HOST` mở ra LAN nhưng chưa bật `ADMIN_AUTH_ENABLED`.
- Múi giờ máy không phải UTC+7.
- `REALTIME_FEED_ENABLED` bật nhưng thiếu `AGENT_EVENT_MONITOR_ENABLED`.
- `STOCK_LIST` không có mã nào thuộc thị trường đang bật.
- Cổng đã bị chiếm.

Script cũng tự đặt `PYTHONUTF8=1` để log tiếng Trung và tiếng Việt không lỗi mã hoá trên console Windows.

Thêm `-NoNotify` khi chạy thử để không đẩy thông báo thật.

Truy cập:
- Trên chính máy Windows: `http://127.0.0.1:8000/`.
- Từ thiết bị khác cùng mạng: `http://<IP-LAN>:8000/`. `status` in ra các IP LAN hiện có.
- Lần đầu truy cập khi `ADMIN_AUTH_ENABLED=true`, trang web yêu cầu đặt mật khẩu admin.

## 7. Tự chạy khi đăng nhập hoặc bật máy

```powershell
.\scripts\run-server.ps1 install-task             # chạy khi người dùng hiện tại đăng nhập
.\scripts\run-server.ps1 install-task -AtStartup  # chạy khi bật máy, tài khoản SYSTEM (cần Administrator)
Start-ScheduledTask -TaskName DailyStockAnalysis  # chạy ngay, không chờ đăng nhập lại
.\scripts\run-server.ps1 uninstall-task
```

- Task tên `DailyStockAnalysis`, chạy lệnh `start` ở foreground với cửa sổ ẩn.
- Task được đặt tự khởi động lại mỗi phút nếu tiến trình kết thúc lỗi, và không giới hạn thời gian chạy.
- `stop` dừng scheduled task trước rồi mới dừng tiến trình, để task không tự bật lại.
- Với `-AtStartup`, file log và DB được tạo dưới tài khoản SYSTEM. Nếu sau đó chạy tay bằng tài khoản thường mà gặp lỗi quyền ghi, cấp quyền Modify cho thư mục `data\` và `logs\`.

## 8. Cập nhật phiên bản mới

1. Trên Mac, chạy `git pull` rồi `./scripts/package-windows.sh`.
2. Trên Windows, chạy `.\scripts\run-server.ps1 stop`.
3. Giải nén gói mới **đè** lên thư mục cũ. Giữ nguyên `.env`, `data\`, `logs\` và `.venv\`: gói không chứa những thư mục này nên không bị ghi đè.
4. Nếu `requirements.txt` thay đổi, chạy `.\scripts\run-server.ps1 setup`.
5. Chạy `.\scripts\run-server.ps1 start -Background`, hoặc `Start-ScheduledTask -TaskName DailyStockAnalysis`.

Muốn rollback thì giải nén lại gói cũ theo cùng các bước trên.

## Kiểm tra sau khi chạy

- `status` báo `health: OK`.
- Từ máy khác, `curl http://<IP-LAN>:8000/api/v1/stocks/HPG/quote` trả về dữ liệu giá.
- Web UI đăng nhập được. Tìm mã VN phải có gợi ý autocomplete, và trang Cảnh báo phải mở được.
- Log (`.\scripts\run-server.ps1 logs`) có dòng khởi động runtime scheduler kèm giờ lịch, và dòng kết nối MQTT TVSI thành công.
- Muốn thử lịch ngay thì tạm đặt `SCHEDULE_TIME` vài phút sau giờ hiện tại, khởi động lại, rồi trả giá trị cũ.

## Troubleshooting

| Hiện tượng | Nguyên nhân / cách xử lý |
| --- | --- |
| Máy khác không vào được, trên máy Windows vẫn vào được | `WEBUI_HOST` vẫn là `127.0.0.1`; hoặc chưa chạy `firewall`; hoặc mạng đang ở profile Public |
| `cannot be loaded because running scripts is disabled` | Chạy `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, rồi `Unblock-File` các file đã tải về |
| `Cổng 8000 đang bị chiếm` | Chạy `stop`, hoặc đổi `WEBUI_PORT` rồi chạy lại `firewall` |
| Web UI hiện trang "chưa build" | Thiếu `static\`: đóng gói lại trên Mac (không dùng `--skip-build` khi chưa build) |
| `Building wheel for tiktoken ... can't find Rust compiler` | `.venv` được tạo bằng Python 3.13+ (log có `cpython-313`/`cpython-314`). Cài Python 3.11, chạy `Remove-Item -Recurse -Force .venv` rồi chạy lại `setup` |
| `pip install` lỗi một gói | Xem tên gói trong log. Các gói dễ vướng trên Windows: `lxml`/`newspaper3k`. Kiểm tra bản Python có phải 3.11 64-bit không |
| Log bị lỗi ký tự (`UnicodeEncodeError`) | Chạy qua `run-server.ps1` (script tự đặt `PYTHONUTF8=1`), không chạy `python main.py` trực tiếp |
| Không có giá realtime / cảnh báo tick | Kiểm tra `REALTIME_FEED_ENABLED` và `AGENT_EVENT_MONITOR_ENABLED`, thông tin `TVSI_MQTT_*`, và firewall hoặc proxy có chặn kết nối ra cổng 443 không. Có thể thăm dò broker bằng `scripts/probe_tvsi_mqtt.py`: script đọc biến môi trường, không đọc `.env`, cú pháp xem docstring đầu file |
| Quên mật khẩu admin | `.\.venv\Scripts\python.exe -m src.auth reset_password` |
| Lịch chạy sai giờ | Múi giờ Windows không phải UTC+7 |

## Giới hạn

- Dịch vụ chạy bằng HTTP thuần, chỉ nên dùng trong mạng nội bộ tin cậy. Nếu cần truy cập từ Internet, đặt reverse proxy có HTTPS phía trước và bật `TRUST_X_FORWARDED_FOR=true`. Việc này nằm ngoài phạm vi tài liệu này.
- `stop` kết thúc tiến trình bằng `Stop-Process -Force`, không có tắt êm. SQLite ở chế độ WAL chịu được việc này, nhưng tác vụ phân tích đang dở sẽ mất.
