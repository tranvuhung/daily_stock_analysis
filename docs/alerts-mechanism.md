# Cơ chế hoạt động của Cảnh báo (Alert Center)

Tài liệu kỹ thuật mô tả kiến trúc và luồng hoạt động của Alert Center: mô hình dữ liệu, loại rule, worker định kỳ, engine realtime (MQTT TVSI), thông báo, API, Web và cấu hình.

- Hướng dẫn thao tác cho người dùng Web: [alerts-user-guide.md](alerts-user-guide.md).
- Chi tiết ranh giới và hợp đồng dữ liệu theo từng giai đoạn: [alerts.md](alerts.md).

Đường dẫn trong tài liệu tính từ gốc repo. Tài liệu cố ý không ghi số dòng vì số dòng dễ lệch khi code thay đổi.

## 1. Tổng quan luồng
```
Web/API tạo rule ─► alert_rules (DB)
                        │
       ┌────────────────┴─────────────────┐
 Worker định kỳ (N phút)          Engine realtime (tick MQTT TVSI)
 AlertWorker.run_once             RealtimeAlertEngine._drain
   bỏ rule engine RT đang giữ       chỉ rule giá VN, trong phiên
   bỏ rule giá VN ngoài phiên       bỏ tick khi đang cooldown
   _evaluate_rule (mọi loại)        evaluate_price_quote
       └────────────────┬─────────────────┘
          AlertWorker.process_result (dùng chung)
   ghi alert_triggers → cooldown DB → gửi kênh (NotificationService, route "alert")
   → ghi alert_notifications → upsert cooldown → sự kiện SSE alert_triggered
                        │
     Web: SSE /alerts/stream → toast, badge, cột Giá live, tải lại lịch sử
```

## 2. Mô hình dữ liệu (`src/storage.py`, repo `src/repositories/alert_repo.py`)

**`alert_rules`**
- Cột: `name`, `target_scope`, `target`, `alert_type`, `parameters` (JSON), `severity` (info/warning/critical), `enabled`, `source` (luôn là `api`), `cooldown_policy`, `notification_policy`.

**`alert_triggers`**
- Mỗi lần đánh giá có trạng thái `triggered`, `skipped`, `degraded` hoặc `failed` sinh một dòng.
- Cột: `observed_value`, `threshold`, `reason`, `data_source` (`realtime_quote`, `tvsi_mqtt`, ...), `data_timestamp`, `diagnostics`.
  - Với dòng `triggered`, `diagnostics` chứa phase summary, context pack và decision signal.
- Chống trùng bằng `create_trigger_if_absent`: so khớp rule, target, `data_timestamp` và `data_source`.

**`alert_notifications`**
- Mỗi kênh gửi là một dòng: `channel`, `success`, `error_code`, `latency_ms`.
- Có các kênh giả để ghi lại lý do không gửi: `__cooldown__`, `__no_channel__`, `__noise_suppressed__`, `__dispatch__`.

**`alert_cooldowns`**
- Khoá duy nhất theo (rule_id, target, severity). Mặc định 24h, đổi được bằng `cooldown_policy.cooldown_seconds` (0 là tắt cooldown).
- **Chỉ được ghi khi có ít nhất một kênh thật gửi thành công.**

## 3. Loại rule và phạm vi (`src/services/alert_service.py`)

| Nhóm | alert_type | Tham số | Dữ liệu dùng |
|---|---|---|---|
| Giá | `price_cross` | direction above/below, price>0, trigger_mode level/cross | quote realtime (tick MQTT → SSI) |
| Giá | `price_change_percent` | direction up/down, change_pct>0 | change_pct của quote |
| Giá | `price_limit_touch` | side ceiling/floor/both, near_pct 0–20 | trần/sàn trong tick hoặc SSI |
| Khối lượng | `volume_spike` | multiplier>0 | 20 nến ngày (khối lượng > trung bình × hệ số) |
| Kỹ thuật (`src/services/alert_indicators.py`) | `ma_price_cross`, `rsi_threshold`, `macd_cross`, `kdj_cross`, `cci_threshold` | chu kỳ 2–250, ngưỡng | nến ngày ≤365; bỏ nến hôm nay chưa đóng (VN đóng lúc 15:00) |
| Danh mục (`src/services/portfolio_alerts.py`) | `portfolio_stop_loss` (near/breach), `portfolio_concentration`, `portfolio_drawdown`, `portfolio_price_stale` | ngưỡng lấy từ `PORTFOLIO_RISK_*_ALERT_PCT` | `PortfolioRiskService` |
| Thị trường (`src/services/market_light_alerts.py`) | `market_light_status` (red/yellow), `market_light_score_drop` (min_drop) | — | snapshot Market Light (không dùng LLM) |

**Phạm vi (scope)**
- `single_symbol`, `watchlist` và `portfolio_holdings` dùng các loại theo mã.
  - `watchlist` lấy danh sách từ `STOCK_LIST`; `portfolio_holdings` lấy các vị thế đang nắm giữ.
  - Mỗi rule mở rộng tối đa 100 mã (`expand_symbol_targets`). Vượt quá thì kết quả là `degraded`, danh sách rỗng thì `skipped`.
- `portfolio_account` dùng các loại danh mục.
- `market` dùng các loại thị trường.
  - Region phải nằm trong `SUPPORTED_MARKETS`; không thoả thì không cho tạo hoặc bật rule, còn khi chạy sẽ trả `skipped`.

**Kết quả đánh giá**
- `status`: triggered / not_triggered / evaluation_error.
- `record_status`:
  - `triggered`
  - `None`: không ghi
  - `skipped`: thiếu quote hoặc thị trường tắt
  - `degraded`: thiếu dữ liệu
  - `failed`: lỗi

**Semantic key** có dạng `scope:target:type:params`, dùng để chống trùng rule và loại rule legacy trùng với rule DB.

## 4. Runtime

**Khởi động**
- Cần `AGENT_EVENT_MONITOR_ENABLED=true`; chu kỳ là `AGENT_EVENT_MONITOR_INTERVAL_MINUTES` (mặc định 5, tối thiểu 1).
- `main.py --schedule` đăng ký background task `agent_event_monitor` và gọi `start_realtime_alerts`.
- Chạy `--serve` thì `api/app.py` lifespan khởi động realtime. Worker định kỳ do `RuntimeSchedulerService` quản lý.
  - Chạy `--serve` mà không bật `SCHEDULE_ENABLED` thì runtime scheduler chạy ở chế độ **chỉ background task**: có worker cảnh báo, không có phân tích hằng ngày. Đổi `AGENT_EVENT_MONITOR_*` trong Cài đặt cần khởi động lại dịch vụ, vì reconcile scheduler sẽ dừng phiên phân tích đang chạy.

**Worker định kỳ (`src/services/alert_worker.py`)**
- Nạp rule DB (mở rộng watchlist/holdings) cùng rule legacy từ `AGENT_EVENT_ALERT_RULES_JSON`.
- Bỏ qua:
  - rule mà engine realtime đang giữ (`claims`);
  - rule giá VN ngoài phiên 9:00–11:30 và 13:00–14:45 (+2 phút nhận giá ATC).
- Còn lại thì `_evaluate_rule`, rồi `process_result`.

**`process_result`**
1. Gắn decision signal cho mã Việt Nam (thị trường `vn`).
2. Ghi trigger, kèm `analysis_visibility`: phase summary và context pack của lần phân tích gần nhất trong 30 ngày.
3. Kiểm tra cooldown DB. Nếu đọc DB lỗi thì dùng fingerprint trong bộ nhớ thay thế.
4. Phát sự kiện `alert_triggered`, gửi thông báo, ghi các lần gửi, rồi upsert cooldown.

Rule legacy dùng TTL 24h trong bộ nhớ, không có cooldown DB.

**Tin nhắn thông báo**
- Tiêu đề `alert_label('title') | mã`, tiếp theo là lý do, trích đoạn phase/context pack và trích đoạn decision signal.
- Ngôn ngữ theo `REPORT_LANGUAGE` (`src/services/alert_labels.py`): mặc định vi; `en`/`ko` → en; `zh` → zh.
- Gửi qua `NotificationService.send_with_results(route_type="alert")`, lọc kênh theo `NOTIFICATION_ALERT_CHANNELS` (để trống là mọi kênh). Có lớp lọc nhiễu (noise control).

**Realtime**
- `TvsiMqttFeed` (`data_provider/tvsi_mqtt_feed.py`, MQTT/TLS 443) chỉ subscribe `topic/<MÃ>` của các mã có rule, không dùng wildcard.
- Tick mới nhất lưu trong `LatestTickStore` (`data_provider/realtime_feed.py`). Tick còn mới cũng được `get_realtime_quote` dùng cho VN.
- `RealtimeAlertEngine` (`src/services/realtime_alert_engine.py`):
  - Gộp tick theo mã.
  - Phát `price_update` tối đa 1 lần/giây cho mỗi mã.
  - Đánh giá rule giá VN trong phiên.
  - Bỏ qua tick khi rule đang cooldown (cache `_suppressed_until`); kích hoạt mà gửi lỗi thì 60 giây sau mới thử lại.
  - Nạp lại rule mỗi 30 giây và ngay sau khi CRUD rule.
- Feed mất kết nối thì `claims` trả false, worker định kỳ quay về dùng SSI.
- `src/services/event_bus.py` nằm trong process (queue 500), chỉ đúng khi chạy một process uvicorn.

## 5. API (`api/v1/endpoints/alerts.py`, prefix `/api/v1/alerts`)
- **Rule:**
  - `POST/GET /rules`, `GET/PATCH/DELETE /rules/{id}`
  - `POST /rules/{id}/enable|disable`
  - `POST /rules/{id}/test`: chạy thử, không gửi thông báo.
- **Lịch sử:** `GET /triggers` và `GET /notifications`, lọc và phân trang.
- **Tiện ích:** `GET /market-regions`, `GET /live-quotes`, `GET /stream` (SSE: connected, feed_status, alert_triggered, price_update, heartbeat).
- **Xác thực:** middleware cookie chung cho `/api/v1/*`.

## 6. Web (`apps/dsa-web/src`)

**Trang `/alerts` (`pages/AlertsPage.tsx`)**
- `AlertRuleForm` (bên trái):
  - Các trường thay đổi theo scope và type.
  - Ô mã có autocomplete (`StockAutocomplete`), kiểm tra hợp lệ phía client.
- `AlertRuleList` (bên phải):
  - Cột Giá live, trạng thái feed.
  - Các nút sửa (nạp rule vào form, gửi `PATCH /rules/{id}`), chạy thử, bật/tắt, xoá.
- Bên dưới: `AlertTriggerHistory` và bảng Notification attempts.

**Toàn cục**
- `Shell` gọi `useAlertStream` (EventSource dùng chung, backoff 3–30 giây) và hiển thị `AlertToasts`.
- `SidebarNav` hiện badge số chưa đọc.
- State nằm trong `stores/alertStore.ts` (zustand).

**Bot/agent/LLM**
- Không có lệnh bot hay agent tool nào tạo rule.
- LLM chỉ xuất hiện gián tiếp: context pack lấy từ lần phân tích trước (nếu có).

## 7. Cấu hình chính

| Key | Mặc định |
|---|---|
| `AGENT_EVENT_MONITOR_ENABLED` | false |
| `AGENT_EVENT_MONITOR_INTERVAL_MINUTES` | 5 |
| `AGENT_EVENT_ALERT_RULES_JSON` | "" |
| `NOTIFICATION_ALERT_CHANNELS` | [] (mọi kênh) |
| `REALTIME_FEED_ENABLED` | false |
| `REALTIME_FEED_STALE_SECONDS` | 30 |
| `TVSI_MQTT_*` | host "", 443, tls, tcp, `topic/{symbol}` |
| `PORTFOLIO_RISK_{CONCENTRATION,DRAWDOWN,STOP_LOSS}_ALERT_PCT` | 35 / 15 / 10 |
| `SUPPORTED_MARKETS` | giới hạn region cho rule thị trường |
| `REPORT_LANGUAGE` | ngôn ngữ tin cảnh báo |

## 8. Điểm hở đã biết (chưa xử lý)
1. **Một số nhãn trong `AlertsPage`/`AlertTriggerHistory` vẫn hardcode tiếng Anh** (trạng thái trigger, bảng Notification attempts, kết quả chạy thử). Dropdown loại rule, phạm vi và mức độ đã dùng đúng nhãn theo ngôn ngữ.
2. **Sửa rule trên Web chưa chỉnh được `cooldown_policy` / `notification_policy`.** Sửa ngưỡng, loại hoặc đối tượng sẽ xoá cooldown của rule.
3. **Rule legacy** (`AGENT_EVENT_ALERT_RULES_JSON`) không có cooldown DB và không có chế độ cross; engine realtime cũng bỏ qua rule legacy.
4. **Decision signal không áp dụng cho mã VN**, nên tin cảnh báo mã VN không có đoạn tín hiệu quyết định.
5. **Đổi `AGENT_EVENT_MONITOR_ENABLED` / `AGENT_EVENT_MONITOR_INTERVAL_MINUTES` trong Cài đặt cần khởi động lại dịch vụ.** Không tự reconcile để tránh dừng phiên phân tích đang chạy.
6. **Ngày lễ VN chưa được loại trừ** trong phiên và phase.
7. **Worker định kỳ vẫn ghi một trigger ở mỗi chu kỳ khi rule đang cooldown** với các rule không phải giá VN (hành vi cũ).
