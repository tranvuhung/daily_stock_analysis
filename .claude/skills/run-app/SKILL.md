---
name: run-app
description: Khởi động và vận hành ứng dụng của repo này (dịch vụ FastAPI + Web UI, phân tích cổ phiếu, chỉ lấy dữ liệu), dùng để kiểm chứng thủ công xem thay đổi có thực sự hiệu lực khi chạy thật hay không. Dùng khi cần "chạy thử dự án", "khởi động dịch vụ", "xem Web UI", "chạy một lượt phân tích thật".
---

# Run App

Khởi động `daily_stock_analysis` trên máy cục bộ và vận hành đến mức nhìn thấy được kết quả. Các lệnh dưới đây đều đã chạy thực tế.

**Đừng** dùng `pytest` để thay cho quy trình này: repo có rất nhiều nhánh rẽ theo ngôn ngữ/thị trường chỉ lộ ra khi chạy thật
(đã từng xảy ra: autocomplete ở frontend gặp thị trường mới thì sập thành ô input trơn, sinh báo cáo bị `KeyError`,
LLM chép nguyên giá trị enum sai ngôn ngữ theo ví dụ trong prompt).

## Đường tắt: `scripts/run-app.sh`

Script có **hai chế độ khác hẳn nhau** — đừng lẫn:

| | `start` (dịch vụ thật) | `serve` (kiểm chứng) |
| --- | --- | --- |
| Cấu hình | `.env` thật của repo | `.env` tạm, ghi đè thị trường/mã/ngôn ngữ |
| CSDL | `data/stock_analysis.db` **thật** | DB tạm trong `$TMPDIR` |
| Cổng | `WEBUI_PORT` (mặc định 8000) | 8765 |
| Scheduler | **Có** (18:00 hằng ngày) | Không |
| Bot, market review | Theo `.env` | Tắt |

Tài liệu dưới đây mô tả **chế độ kiểm chứng** — đó là thứ dùng cho quy trình review.
Dùng `start` khi muốn chạy đúng dịch vụ như triển khai thật.

```bash
# --- Dịch vụ thật ---
./scripts/run-app.sh start --background # FastAPI + Web UI + scheduler + bot, dùng .env & DB thật
./scripts/run-app.sh start --run-now    # như trên, phân tích ngay khi khởi động (tốn quota LLM)
./scripts/run-app.sh status             # chế độ nào đang chạy, PID, cổng, health

# --- Môi trường kiểm chứng (dùng cho review) ---
./scripts/run-app.sh serve              # FastAPI + Web UI cô lập, chờ health rồi smoke test
./scripts/run-app.sh serve --background # như trên nhưng chạy nền
./scripts/run-app.sh dry-run            # chỉ lấy dữ liệu, không gọi LLM
./scripts/run-app.sh analyze HPG        # một lượt phân tích thật + kiểm tra lẫn ngôn ngữ
./scripts/run-app.sh smoke              # smoke test dịch vụ đang chạy sẵn

# --- Chung ---
./scripts/run-app.sh stop               # dừng cả hai chế độ
./scripts/run-app.sh build-web          # npm run lint && npm run build
```

`start` mặc định truyền `--no-run-immediately`: `.env` đang đặt `SCHEDULE_RUN_IMMEDIATELY=true`
nên nếu không chặn thì vừa khởi động là phân tích toàn bộ `STOCK_LIST`. Trước khi chạy, `start`
tiền kiểm và cảnh báo nếu `STOCK_LIST` không có mã Việt Nam hợp lệ nào (hệ thống chỉ hỗ trợ
thị trường `vn`).

Mọi giá trị đều override được: `RUN_PORT`, `RUN_STOCK_LIST`,
`RUN_REPORT_LANGUAGE`, `RUN_DIR`, `RUN_HEALTH_TIMEOUT`… (`./scripts/run-app.sh --help`).

## Điều kiện trước

```bash
cd <repo>
source .venv/bin/activate          # đã có sẵn; nếu chưa: python3.11 -m venv .venv && pip install -r requirements.txt
python -V                          # cần 3.11
```

## Cấu hình chạy (quan trọng)

Đừng sửa thẳng file `.env` ở thư mục gốc repo. Hãy sao một bản ra thư mục tạm rồi ghi đè các khoá cần thiết, và trỏ `ENV_FILE` vào đó:

```bash
SP="${TMPDIR:-/tmp}/dsa-run"; mkdir -p "$SP"
grep -vE '^(SUPPORTED_MARKETS|STOCK_LIST|REPORT_LANGUAGE|DATABASE_PATH|WEBUI_ENABLED|WEBUI_PORT|WEBUI_AUTO_BUILD|MARKET_REVIEW_ENABLED)=' .env > "$SP/run.env"
cat >> "$SP/run.env" <<EOF
SUPPORTED_MARKETS=vn
STOCK_LIST=HPG,FPT,VNM,VNINDEX,VN30
REPORT_LANGUAGE=vi
DATABASE_PATH=$SP/run.db
WEBUI_ENABLED=true
WEBUI_PORT=8765
WEBUI_AUTO_BUILD=false
MARKET_REVIEW_ENABLED=false
EOF
chmod 600 "$SP/run.env"
```

Những cái bẫy đã vấp, đừng vấp lại:

- **`STOCK_LIST` và `REPORT_LANGUAGE` ưu tiên đọc từ file `.env`, không phải biến môi trường của tiến trình**
  (`Config._resolve_report_language_env_value` / `refresh_stock_list`).
  Chạy `STOCK_LIST=... python main.py` sẽ không có tác dụng, bắt buộc phải ghi vào file mà `ENV_FILE` trỏ tới.
- **`WEBUI_ENABLED=true` khiến `--stocks` tiếp tục treo chạy dịch vụ sau khi phân tích xong**, lệnh sẽ không thoát.
  Khi chạy phân tích một lần thì đặt `WEBUI_ENABLED=false`.
- `WEBUI_AUTO_BUILD=false` bỏ qua bước tự build frontend; nếu đã sửa `apps/dsa-web` thì tự chạy `npm run build`.
- `DATABASE_PATH` trỏ vào thư mục tạm để tránh làm bẩn `data/stock_analysis.db` của người dùng.
- Phân tích cần kênh LLM (`GEMINI_API_KEY` / `OPENAI_API_KEY` / …). Nếu không có, trang chủ sẽ hiển thị
  "Base configuration incomplete", nhưng `--dry-run` vẫn dùng được.

## Chế độ 1: chỉ lấy dữ liệu, không gọi LLM (cách smoke test nhanh nhất)

```bash
ENV_FILE="$SP/run.env" DATABASE_PATH="$SP/run.db" python main.py --dry-run
```

Kỳ vọng: dòng tổng kết `成功: N, 失败: 0` (thành công N, thất bại 0), và các mã không thuộc
thị trường đang bật bị loại khỏi danh sách trước khi chạy.

Có **hai** tầng lọc khác nhau, đừng nhầm:

| Log | Vị trí | Khi nào thấy |
| --- | --- | --- |
| `今日休市股票已跳过: {...}` | `main.py` (`_compute_trading_day_filter`) | Đường CLI — tầng này chạy **trước**, nên đây mới là log bạn thấy khi chạy `--dry-run` |
| `跳过不属于已启用市场的标的` | `src/core/pipeline.py` | Backstop khi vào thẳng pipeline (đường API), thường **không** xuất hiện ở đường CLI |

Muốn kiểm chứng cổng chặn thị trường một cách trực tiếp thì dùng đường API ở Chế độ 3
(`/api/v1/stocks/AAPL/quote` -> 404), chắc chắn hơn là soi log.
Các chuỗi trên là log gốc tiếng Trung của chương trình, giữ nguyên để còn `grep` được.

## Chế độ 2: chạy một lượt phân tích thật (cần LLM key)

```bash
sed -i '' 's/^WEBUI_ENABLED=true/WEBUI_ENABLED=false/' "$SP/run.env"   # nếu không sẽ không thoát
ENV_FILE="$SP/run.env" DATABASE_PATH="$SP/run.db" \
  python main.py --stocks HPG --no-notify
```

Bắt buộc phải có `--no-notify`, nếu không sẽ đẩy thông báo thật tới các kênh đã cấu hình.
Báo cáo nằm ở `reports/report_YYYYMMDD.md` (thư mục này đã được gitignore).

Kiểm tra ngôn ngữ báo cáo không bị lẫn:

```bash
grep -nP '[\x{4e00}-\x{9fff}\x{3040}-\x{30ff}\x{ac00}-\x{d7af}]' reports/report_*.md \
  || echo "no CJK leak"
```

Phần văn bản tự do do LLM sinh thỉnh thoảng vẫn lẫn thuật ngữ tiếng Trung (bản chất không tất định). Còn văn bản cố định, nhãn và giá trị enum thì bắt buộc phải thuần tiếng Việt — nếu những chỗ đó ra tiếng Trung thì đó là bug code, không phải lỗi mô hình.

## Chế độ 3: khởi động dịch vụ + Web UI

```bash
lsof -ti:8765 -sTCP:LISTEN | xargs -r kill; sleep 2     # cổng chưa dọn sạch sẽ khởi động thất bại
ENV_FILE="$SP/run.env" nohup python main.py --serve-only > "$SP/server.log" 2>&1 &
for i in $(seq 1 40); do
  curl -sf http://127.0.0.1:8765/api/v1/health >/dev/null && break; sleep 1
done
```

Smoke test (từng lệnh đều đã chạy thực tế):

```bash
curl -s http://127.0.0.1:8765/api/v1/data/overview | python3 -m json.tool | head -40
curl -s http://127.0.0.1:8765/api/v1/stocks/HPG/quote      # cổ phiếu Việt Nam
curl -s http://127.0.0.1:8765/api/v1/stocks/VNINDEX/quote  # chỉ số Việt Nam
curl -s -o /dev/null -w '%{http_code}\n' \
  http://127.0.0.1:8765/api/v1/stocks/AAPL/quote           # mã không phải VN -> 404
```

Web UI: mở trình duyệt vào `http://127.0.0.1:8765/`.
**Bắt buộc phải thực sự xem ảnh chụp màn hình**, và đọc một lượt console của trình duyệt — các lỗi frontend như whitelist badge thị trường của autocomplete
chỉ in ra console, trên trang chỉ biểu hiện thành "không có gợi ý xổ xuống", không báo lỗi gì.

Dừng:

```bash
lsof -ti:8765 -sTCP:LISTEN | xargs -r kill
```

## Sửa frontend thì phải build lại

```bash
cd apps/dsa-web && npm run lint && npm run build   # sản phẩm build ghi vào <repo>/static/
```

`npm run lint` không kiểm tra kiểu, lỗi kiểu chỉ lộ ra khi chạy `npm run build`.
Trình duyệt có thể cache bundle cũ, khi kiểm chứng hãy thêm tham số truy vấn kiểu `http://127.0.0.1:8765/?v=2` để né cache.

`npm test` hiện có khoảng 150 ca lỗi đã tồn tại từ trước (di sản của đợt cải tổ i18n), không phải mục gác cổng CI,
đừng thấy nó đỏ mà tưởng mình làm hỏng — `web-gate` chỉ chạy `lint` + `build`.

## Gác cổng hồi quy

```bash
./scripts/ci_gate.sh          # backend; tests/test_intelligence_service.py có một ca lỗi đã tồn tại từ trước
cd apps/dsa-web && npm run lint && npm run build
```
