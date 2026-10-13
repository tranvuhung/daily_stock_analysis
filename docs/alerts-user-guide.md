# Hướng dẫn sử dụng Cảnh báo (Alerts) trên Web

Tài liệu này dành cho **người dùng Web UI**. Nó hướng dẫn bật tính năng cảnh báo, tạo và quản lý quy tắc, và cách đọc lịch sử kích hoạt. Hợp đồng API, schema, worker và rollback nằm ở tài liệu kỹ thuật [alerts.md](alerts.md). Kiến trúc và luồng tổng thể xem [alerts-mechanism.md](alerts-mechanism.md).

## 1. Cảnh báo hoạt động thế nào

```
Bạn tạo quy tắc ở trang /alerts
        ↓
Tiến trình nền (Event Monitor) chạy theo chu kỳ, mặc định 5 phút
        ↓
Đánh giá từng quy tắc đang bật bằng dữ liệu giá / chỉ báo / danh mục / đèn tín hiệu thị trường
        ↓
Nếu thoả điều kiện → ghi "lịch sử kích hoạt" → gửi thông báo qua kênh đã cấu hình
        ↓
Bắt đầu "thời gian chờ" (cooldown) 24 giờ để không báo lặp
```

Lưu ý:

- Mặc định cảnh báo là **kiểm tra theo chu kỳ**. Khi bật **giá realtime MQTT** (mục 10), cảnh báo giá của mã VN được đánh giá **ngay khi có tick mới**.
- Việc đánh giá **hoàn toàn theo quy tắc** và **không gọi LLM**.
- Tin nhắn cảnh báo có thể kèm tóm tắt khuyến nghị AI gần nhất của mã, lấy từ lần phân tích trước đó.

Ví dụ một tin cảnh báo khi `REPORT_LANGUAGE=vi`:

```
⚠️ Cảnh báo sự kiện | NLG

NLG change down 0.50%: current = -1.54%

- giai đoạn: intraday | thị trường: vn | nguồn kích hoạt: alert | nguồn tóm tắt: snapshot phân tích gần nhất
- chất lượng dữ liệu: usable
- hạn chế: quote: fallback
- hạn chế: technical: partial
```

- Nhãn trong tin nhắn đi theo **ngôn ngữ báo cáo** (`REPORT_LANGUAGE`): `vi` ra tiếng Việt, `en` ra tiếng Anh (khi đó tiêu đề là `Event Alert | …`).
- Dòng lý do, ví dụ `NLG change down 0.50%…`, luôn viết bằng tiếng Anh.

## 2. Bật tính năng (làm một lần)

Vào **Cài đặt** và kiểm tra các mục sau:

| Nhóm cài đặt | Khoá | Giá trị cần đặt | Ý nghĩa |
| --- | --- | --- | --- |
| Hệ thống | `SCHEDULE_ENABLED` (Schedule Enabled) | Tuỳ chọn | Chỉ cần cho **phân tích hằng ngày**. Chạy Web (`python main.py --serve`) thì tiến trình cảnh báo vẫn chạy khi lịch tắt. Chạy dòng lệnh không có Web thì dùng `python main.py --schedule`. |
| Agent | `AGENT_EVENT_MONITOR_ENABLED` (Event Monitor) | Bật | Công tắc chính của cảnh báo. Mặc định **tắt**. |
| Agent | `AGENT_EVENT_MONITOR_INTERVAL_MINUTES` | Ví dụ `5` | Chu kỳ kiểm tra, tính bằng phút. Tối thiểu 1. |
| Thông báo | Các kênh (Telegram, Email, Discord, Slack, Webhook…) | Cấu hình ít nhất một kênh | Nơi nhận cảnh báo. |
| Thông báo | `NOTIFICATION_ALERT_CHANNELS` | Để trống, hoặc ví dụ `telegram,email` | Chỉ gửi cảnh báo tới các kênh này. Để trống thì gửi tới mọi kênh đã cấu hình. |
| Nguồn dữ liệu | `SUPPORTED_MARKETS` (Enabled Markets) | Mặc định `vn`; ví dụ `vn,us` | Các thị trường được dùng. Cảnh báo phạm vi **Thị trường** chỉ chọn được các thị trường có trong danh sách này. |
| Hệ thống | `TRADING_DAY_CHECK_ENABLED` | Bật (khuyến nghị) | Bỏ qua cảnh báo thị trường vào ngày nghỉ (với VN là thứ 7 và CN). |
| Nguồn dữ liệu | `REALTIME_FEED_ENABLED` + `TVSI_MQTT_*` | Tuỳ chọn | Bật giá realtime qua MQTT của TVSI. Xem mục 10. |

> Nếu chưa bật `AGENT_EVENT_MONITOR_ENABLED`, bạn vẫn tạo được quy tắc và bấm **Kiểm thử** được. Nhưng sẽ **không có cảnh báo tự động** nào được gửi. Đổi công tắc này trong Cài đặt cần **khởi động lại dịch vụ**.

## 3. Bố cục trang Cảnh báo

Mở menu bên trái → **Cảnh báo** (`/alerts`). Trang gồm 4 khu vực:

1. **Tạo quy tắc cảnh báo** (bên trái): form tạo quy tắc mới.
2. **Quy tắc cảnh báo** (bên phải): danh sách quy tắc, bộ lọc và các nút thao tác. Kết quả **Kiểm thử** hiện ngay bên dưới.
3. **Lịch sử kích hoạt** (Trigger history): các lần quy tắc được đánh giá và ghi nhận.
4. **Notification attempts**: kết quả gửi thông báo theo từng kênh.

## 4. Tạo quy tắc

### 4.1 Các bước

1. **Tên quy tắc**: tuỳ chọn. Bỏ trống thì hệ thống tự đặt tên.
2. **Phạm vi đối tượng**: chọn một trong 5 phạm vi ở mục 4.2.
3. **Đối tượng**: ô nhập thay đổi theo phạm vi, ví dụ mã cổ phiếu, tài khoản hoặc khu vực thị trường.
4. **Loại quy tắc**: danh sách loại thay đổi theo phạm vi đã chọn.
5. Nhập **tham số** của loại quy tắc (mục 4.3).
6. **Mức nghiêm trọng**: `Thông tin` / `Cảnh báo` (mặc định) / `Nghiêm trọng`.
7. **Bật ngay sau khi tạo**: mặc định có tick.
8. Bấm **Tạo quy tắc**.

### 4.2 Phạm vi đối tượng

| Phạm vi | Đối tượng | Dùng khi |
| --- | --- | --- |
| **Một mã** | Nhập mã, ví dụ `HPG`, `FPT`, `VNM`, `VCB` | Theo dõi một cổ phiếu cụ thể. |
| **Danh mục theo dõi** | Cố định `default` | Áp dụng cho mọi mã trong danh sách cổ phiếu theo dõi (`STOCK_LIST`). |
| **Cổ phiếu đang nắm giữ** | `Tất cả tài khoản` hoặc một tài khoản | Áp dụng cho từng mã đang có vị thế trong danh mục. |
| **Tài khoản danh mục** | `Tất cả tài khoản` hoặc một tài khoản | Cảnh báo rủi ro ở cấp tài khoản, như cắt lỗ, tập trung hay sụt giảm. |
| **Thị trường** | **Khu vực thị trường**. Mặc định **Việt Nam** | Cảnh báo theo đèn tín hiệu của cả thị trường. |

Lưu ý cho phạm vi **Thị trường**:

- Dropdown chỉ hiện các thị trường đang bật trong `SUPPORTED_MARKETS`. Với mặc định `vn`, bạn chỉ thấy **Việt Nam**.
- Muốn cảnh báo cho Trung Quốc (A), Hồng Kông hoặc Mỹ, hãy thêm `cn` / `hk` / `us` vào `SUPPORTED_MARKETS`, ví dụ `vn,us`.
- Nếu không có thị trường nào hỗ trợ được bật, form hiện thông báo *"Chưa bật thị trường nào hỗ trợ cảnh báo…"* và không cho tạo quy tắc.

Ô **Mã** của phạm vi Một mã có **gợi ý tự động**:

- Gõ mã (`HPG`), tên có dấu (`Hòa Phát`) hoặc tên không dấu (`hoaphat`), rồi dùng phím ↑/↓ và Enter, hoặc bấm chuột, để chọn.
- Chọn một gợi ý sẽ điền mã vào ô. Nhấn Enter khi không chọn gợi ý nào sẽ tạo quy tắc như bình thường.
- Nếu không tải được danh sách mã, ô này trở thành ô nhập tay bình thường.

Lưu ý về mã VN: khi bật đồng thời `vn` và `us`, mã 3 ký tự như `HPG` có thể bị hiểu là mã Mỹ. Khi đó hãy nhập `HPG.VN`.

### 4.3 Loại quy tắc và tham số

**Nhóm giá và khối lượng.** Dùng cho phạm vi Một mã, Danh mục theo dõi và Cổ phiếu đang nắm giữ.

| Loại quy tắc | Tham số | Khi nào kích hoạt |
| --- | --- | --- |
| Giá cắt ngưỡng | Hướng `above`/`below`, Ngưỡng giá (> 0), **Chế độ kích hoạt** | **Theo mức** (mặc định): giá hiện tại **≥** ngưỡng (above) hoặc **≤** ngưỡng (below). **Cắt qua**: chỉ báo khi giá **vừa cắt** qua ngưỡng so với lần quan sát trước. |
| Biến động giá | Hướng `up`/`down`, Ngưỡng biến động % (> 0) | % thay đổi trong ngày ≥ X (up), hoặc ≤ −X (down). |
| Chạm trần/sàn | Loại giới hạn `Trần hoặc sàn` / `Giá trần` / `Giá sàn`, Khoảng cách % (0–20, mặc định 0) | Giá chạm trần/sàn, hoặc cách trần/sàn trong khoảng % đã đặt. Chỉ áp dụng cho mã VN. |
| Khối lượng đột biến | Hệ số khối lượng (> 0), ví dụ `2` | Khối lượng phiên gần nhất > trung bình 20 phiên × hệ số. |

> Ở chế độ **Theo mức**, "Giá cắt ngưỡng" và "Biến động giá" thoả điều kiện ở mọi lần kiểm tra khi giá còn nằm bên kia ngưỡng. Thời gian chờ 24 giờ (mục 6) giúp bạn không bị báo liên tục. Muốn chỉ nhận báo đúng lúc giá cắt qua, hãy chọn chế độ **Cắt qua**. Lần quan sát đầu tiên sau khi tạo quy tắc (hoặc sau khi khởi động lại dịch vụ) chỉ ghi mốc giá, chưa báo.

**Nhóm chỉ báo kỹ thuật.** Tính trên nến ngày. Quy tắc chỉ kích hoạt **tại thời điểm cắt**: phiên trước chưa vượt, phiên này vượt.

| Loại quy tắc | Tham số (mặc định) | Khi nào kích hoạt |
| --- | --- | --- |
| Giá cắt đường MA | Hướng, Chu kỳ MA (20; từ 2 đến 250) | Giá đóng cửa cắt lên hoặc cắt xuống đường MA. |
| Ngưỡng RSI | Hướng, Chu kỳ RSI (12), Ngưỡng RSI (0–100) | RSI cắt lên hoặc cắt xuống ngưỡng, ví dụ cắt xuống 30. |
| Giao cắt MACD | Giao cắt tăng/giảm, Chu kỳ nhanh 12 / chậm 26 / tín hiệu 9 | Đường DIF cắt đường DEA. Chu kỳ nhanh phải nhỏ hơn chu kỳ chậm. |
| Giao cắt KDJ | Giao cắt tăng/giảm, Chu kỳ KDJ 9, K 3, D 3 | Đường K cắt đường D. |
| Ngưỡng CCI | Hướng, Chu kỳ CCI (14), Ngưỡng CCI | CCI cắt lên hoặc cắt xuống ngưỡng, ví dụ cắt lên 100. |

> Nến của ngày hôm nay chỉ được tính khi phiên đã đóng: sau 15:00 giờ Việt Nam với mã VN, sau 16:00 giờ địa phương với các thị trường khác. Trước mốc đó, hệ thống dùng nến của phiên trước.

**Nhóm danh mục.** Chỉ dùng cho phạm vi **Tài khoản danh mục**.

| Loại quy tắc | Tham số | Khi nào kích hoạt |
| --- | --- | --- |
| Cắt lỗ danh mục | Chế độ `Gần ngưỡng cắt lỗ` / `Đã chạm cắt lỗ` | Có vị thế gần hoặc đã vượt ngưỡng lỗ so với giá vốn. Ngưỡng mặc định là 10%, đặt bằng `PORTFOLIO_RISK_STOP_LOSS_ALERT_PCT`. |
| Mức tập trung danh mục | Không có | Tỷ trọng mã lớn nhất ≥ `PORTFOLIO_RISK_CONCENTRATION_ALERT_PCT` (mặc định 35%). |
| Mức sụt giảm danh mục | Không có | Mức sụt giảm từ đỉnh ≥ `PORTFOLIO_RISK_DRAWDOWN_ALERT_PCT` (mặc định 15%). |
| Trạng thái giá danh mục | Không có | Có vị thế thiếu giá hoặc giá đã cũ. |

**Nhóm thị trường.** Chỉ dùng cho phạm vi **Thị trường**.

| Loại quy tắc | Tham số | Khi nào kích hoạt |
| --- | --- | --- |
| Trạng thái đèn tín hiệu thị trường | Trạng thái kích hoạt: `Đỏ` và/hoặc `Vàng` (chọn ít nhất 1) | Đèn tín hiệu hiện tại của thị trường nằm trong các trạng thái đã chọn. |
| Điểm đèn tín hiệu thị trường giảm | Ngưỡng điểm giảm (mặc định 10) | Điểm của phiên trước trừ điểm hiện tại ≥ ngưỡng. |

"Đèn tín hiệu" được chấm theo độ rộng thị trường, biến động chỉ số và số mã trần/sàn:

- ≥ 60 điểm: xanh.
- 40–59 điểm: vàng.
- < 40 điểm: đỏ.

Với Việt Nam, chỉ số lấy từ VCI (VN-Index, VN30, HNX, UPCoM), độ rộng lấy từ SSI.

> "Điểm đèn tín hiệu giảm" cần **điểm của phiên trước** để so sánh. Điểm này được lưu mỗi khi chạy **Điểm lại thị trường**. Nếu chưa từng chạy điểm lại thị trường VN, kết quả sẽ là `Skipped` với lý do *previous market light snapshot not found*.

### 4.4 Ví dụ nhanh

| Mục tiêu | Cách đặt |
| --- | --- |
| Báo khi HPG vượt 30.000 | Một mã `HPG` → Giá cắt ngưỡng → `above`, `30000` |
| Chỉ báo đúng lúc HPG vừa cắt lên 30.000 | Một mã `HPG` → Giá cắt ngưỡng → `above`, `30000`, chế độ **Cắt qua** |
| Báo khi SHS chạm trần hoặc cách trần 1% | Một mã `SHS` → Chạm trần/sàn → `Giá trần`, khoảng cách `1` |
| Báo khi FPT giảm quá 5% trong ngày | Một mã `FPT` → Biến động giá → `down`, `5` |
| Báo khi VNM có khối lượng gấp 3 lần trung bình | Một mã `VNM` → Khối lượng đột biến → `3` |
| Báo khi mã nào trong danh mục theo dõi có RSI(14) cắt xuống 30 | Danh mục theo dõi → Ngưỡng RSI → `below`, chu kỳ `14`, ngưỡng `30` |
| Báo khi thị trường VN chuyển sang đèn đỏ | Thị trường → Việt Nam → Trạng thái đèn tín hiệu → chỉ tick `Đỏ`, mức `Nghiêm trọng` |
| Báo khi có vị thế đã chạm cắt lỗ | Tài khoản danh mục → Tất cả tài khoản → Cắt lỗ danh mục → `Đã chạm cắt lỗ` |

## 5. Quản lý quy tắc

Trong bảng **Quy tắc cảnh báo**:

- **Bộ lọc**: lọc theo `Trạng thái` (đã bật/đã tắt) và `Loại quy tắc`. Mỗi trang có 20 quy tắc.
- Cột **Thời gian chờ**: `Đang trong thời gian chờ` nghĩa là quy tắc vừa gửi cảnh báo và đang tạm im lặng.
- **Kiểm thử**: đánh giá quy tắc ngay một lần với dữ liệu hiện tại. Thao tác này **không gửi thông báo** và **không ghi lịch sử**. Kết quả hiện ngay dưới bảng:
  - `triggered`: điều kiện đang thoả.
  - `not_triggered`: chưa thoả.
  - `evaluation_error`: lỗi khi đánh giá.

  Với phạm vi nhiều mã (danh mục theo dõi, đang nắm giữ), kết quả liệt kê tối đa 20 mã.
- **Tắt / Bật**: tạm dừng hoặc chạy lại quy tắc.
- **Xoá**: có hộp thoại xác nhận. Lịch sử kích hoạt cũ vẫn được giữ.
- **Sửa**: nạp quy tắc vào form bên trái (tiêu đề đổi thành *Sửa quy tắc cảnh báo*). Chỉnh rồi bấm **Lưu thay đổi**, hoặc **Huỷ sửa** để quay về form tạo mới.
  - Đổi **ngưỡng, loại quy tắc hoặc đối tượng** sẽ xoá thời gian chờ hiện tại, nên quy tắc mới có thể báo ngay.
  - Chỉ đổi tên, mức độ hoặc bật/tắt thì giữ nguyên thời gian chờ.
  - Lịch sử kích hoạt cũ vẫn giữ và vẫn gắn với quy tắc.

Quy tắc **Thị trường** cho một thị trường đã bị tắt trong `SUPPORTED_MARKETS` vẫn hiện trong danh sách:

- Bạn vẫn **tắt** hoặc **xoá** được quy tắc đó.
- Bấm **Bật** sẽ báo lỗi *"market alert target … is not enabled"*, cho tới khi bạn bật lại thị trường đó trong Cài đặt.

## 6. Thời gian chờ và chống trùng

- Sau khi một quy tắc gửi cảnh báo **thành công** tới ít nhất một kênh, quy tắc đó im lặng **24 giờ** cho cùng một đối tượng và cùng mức nghiêm trọng.
  - Web chưa có ô chỉnh thời gian chờ.
  - Nếu cần đổi, dùng API (`cooldown_policy.cooldown_seconds`, đặt `0` để tắt). Xem [alerts.md](alerts.md).
- Cùng một điểm dữ liệu (cùng mã, nguồn và thời điểm dữ liệu) chỉ được ghi một lần.
- Ngoài ra còn có cơ chế chống nhiễu chung của thông báo: giờ yên lặng (`NOTIFICATION_QUIET_HOURS`) và mức nghiêm trọng tối thiểu (`NOTIFICATION_MIN_SEVERITY`). Xem [notifications.md](notifications.md).

## 7. Đọc lịch sử

### 7.1 Lịch sử kích hoạt (Trigger history)

| Cột | Ý nghĩa |
| --- | --- |
| Status | `Triggered`: đã thoả điều kiện. `Skipped`: bỏ qua, ví dụ ngày nghỉ, thiếu giá, chưa có phiên trước, hoặc thị trường chưa bật. `Degraded`: dữ liệu thiếu hoặc lỗi, ví dụ không đủ nến. `Failed`: lỗi hệ thống. |
| Phase / quality | Giai đoạn thị trường lúc kích hoạt và chất lượng dữ liệu. |
| Target | Mã hoặc thị trường cụ thể. Với danh mục theo dõi, mỗi mã là một dòng. |
| Observed / Threshold | Giá trị quan sát được và ngưỡng so sánh. |
| Source / Data time | Nguồn dữ liệu và thời điểm của dữ liệu. |
| Reason | Mô tả lý do kích hoạt hoặc lý do bỏ qua. |

Trạng thái "không thoả điều kiện" **không được ghi** vào lịch sử. Vì vậy lịch sử trống không có nghĩa là hệ thống không chạy.

### 7.2 Notification attempts

Mỗi dòng là một lần gửi tới một kênh. Các "kênh" đặc biệt:

| Kênh hiển thị | Ý nghĩa |
| --- | --- |
| Business cooldown | Không gửi vì quy tắc đang trong thời gian chờ. |
| Noise suppression | Bị chặn bởi cơ chế chống nhiễu, ví dụ giờ yên lặng hoặc mức nghiêm trọng thấp. |
| No channel available | Chưa cấu hình kênh thông báo nào phù hợp. |
| Cooldown read failed | Không đọc được trạng thái chờ; hệ thống dùng cơ chế dự phòng trong bộ nhớ. |

> Danh sách lịch sử và notification tải **20 dòng mới nhất**. Khi Web nhận cảnh báo realtime (mục 10), hai bảng này tự tải lại; ngoài ra muốn xem dữ liệu mới thì tải lại trang.

## 8. Xử lý sự cố

| Hiện tượng | Nguyên nhân thường gặp | Cách xử lý |
| --- | --- | --- |
| Kiểm thử báo `triggered` nhưng không nhận được tin | Chưa bật `AGENT_EVENT_MONITOR_ENABLED`, hoặc bật rồi nhưng chưa khởi động lại dịch vụ | Bật (mục 2), khởi động lại và chờ một chu kỳ. |
| Không nhận tin dù lịch sử có `Triggered` | Kênh thông báo lỗi, bị chặn chống nhiễu, hoặc đang trong thời gian chờ | Xem bảng Notification attempts. Kiểm tra `NOTIFICATION_ALERT_CHANNELS` và các kênh. |
| Dropdown Thị trường chỉ có Việt Nam | `SUPPORTED_MARKETS=vn` (mặc định) | Thêm thị trường cần dùng, ví dụ `vn,us`. |
| Quy tắc thị trường luôn `Skipped` | Ngày nghỉ, thị trường chưa bật, hoặc chưa có phiên trước (với "điểm giảm") | Đọc cột Reason. Chạy **Điểm lại thị trường** ít nhất một lần. |
| Quy tắc chỉ báo luôn `Degraded` | Không đủ nến ngày cho chu kỳ đã chọn, hoặc nguồn dữ liệu lỗi | Giảm chu kỳ, hoặc kiểm tra nguồn dữ liệu trong Cài đặt → Nguồn dữ liệu. |
| Nhập mã VN bị báo sai định dạng hoặc hiểu nhầm là mã Mỹ | Đang bật cả `vn` và `us` | Nhập dạng `HPG.VN`. |
| Bị báo lặp mỗi ngày | "Giá cắt ngưỡng" so theo mức, và thời gian chờ chỉ là 24 giờ | Chọn chế độ **Cắt qua**, hoặc tắt quy tắc sau khi đã xử lý. |
| Dòng trạng thái ghi "Giá realtime: mất kết nối" | Không kết nối được broker TVSI (sai host/mật khẩu, ngoài mạng nội bộ TVSI/VPN) | Kiểm tra `TVSI_MQTT_*`. Trong lúc đó cảnh báo vẫn chạy theo chu kỳ bằng giá SSI. |
| Cột Giá live trống | Chưa bật giá realtime và nguồn SSI không trả giá, hoặc quy tắc không phải phạm vi Một mã | Bật mục 10, hoặc kiểm tra nguồn dữ liệu. |

## 9. Giới hạn hiện tại

- Giá realtime theo tick chỉ có cho **mã VN** qua MQTT TVSI. Các thị trường khác vẫn dùng giá snapshot theo chu kỳ. Chỉ báo kỹ thuật vẫn tính trên nến ngày (không có nến phút).
- Ngày lễ VN chưa được loại trừ khỏi phiên giao dịch.
- Chưa có cảnh báo theo tin tức hoặc tâm lý thị trường.
- Mức cắt lỗ và chốt lời do AI đề xuất trong báo cáo **không** tự động thành quy tắc. Bạn cần tạo quy tắc thủ công.
- Web chưa có ô chỉnh thời gian chờ hay chính sách thông báo.
- Một số nhãn trên trang (lịch sử kích hoạt, notification attempts) vẫn hiển thị tiếng Anh.

## 10. Cảnh báo realtime theo giá MQTT (mã Việt Nam)

Khi bật, hệ thống nhận giá khớp lệnh từng tick từ broker MQTT của TVSI. Cảnh báo giá (Giá cắt ngưỡng, Biến động giá, Chạm trần/sàn) của mã VN được đánh giá **ngay khi giá thay đổi**, không cần chờ chu kỳ 5 phút.

### 10.1 Bật tính năng

Trong **Cài đặt → Nguồn dữ liệu**, hoặc trong `.env`:

| Khoá | Giá trị |
| --- | --- |
| `REALTIME_FEED_ENABLED` | `true` |
| `TVSI_MQTT_HOST` | `realtime-mobile.tvsi.com.vn` |
| `TVSI_MQTT_PORT` / `TVSI_MQTT_TLS` / `TVSI_MQTT_TRANSPORT` | `443` / `true` / `tcp` (mặc định) |
| `TVSI_MQTT_USERNAME` / `TVSI_MQTT_PASSWORD` | Thông tin đăng nhập TVSI cấp |

- Cần bật cả `AGENT_EVENT_MONITOR_ENABLED`.
- Sau khi đổi các khoá này, **khởi động lại dịch vụ**.
- Broker TVSI nằm trong mạng nội bộ, nên máy chạy dịch vụ phải truy cập được host này (mạng TVSI hoặc VPN).
- Không đưa mật khẩu vào issue, nhật ký hay ảnh chụp màn hình.

### 10.2 Trên trang Cảnh báo

- **Dòng trạng thái** phía trên danh sách quy tắc:
  - *Giá realtime: đang kết nối (MQTT)*: kèm "ngoài phiên" nếu đang ngoài giờ giao dịch.
  - *mất kết nối, dùng giá SSI theo chu kỳ*.
  - *chưa bật*.
- Cột **Giá live** (quy tắc phạm vi Một mã): giá hiện tại, % thay đổi trong ngày, và khoảng cách từ giá tới ngưỡng của "Giá cắt ngưỡng". Cột cập nhật liên tục, tối đa 1 lần/giây mỗi mã.
- Khi một cảnh báo kích hoạt:
  - Góc dưới bên phải hiện **toast**, ở mọi trang.
  - Menu **Cảnh báo** có **badge** số cảnh báo mới; mở trang Cảnh báo thì badge được xoá.
  - Bảng lịch sử tự tải lại.
- Cảnh báo **vẫn được gửi qua các kênh đã cấu hình** (Telegram, Email…) như bình thường, và vẫn áp dụng thời gian chờ 24 giờ.

### 10.3 Lưu ý

- Chỉ đánh giá trong **phiên khớp lệnh**: 9:00–11:30 và 13:00–14:45, thứ 2–6 (thêm 2 phút sau 14:45 để nhận giá khớp ATC). Giá ngoài phiên vẫn hiện ở cột Giá live nhưng không kích hoạt cảnh báo. Quy tắc này áp dụng cho cả chế độ kiểm tra định kỳ.
- Khi quy tắc đang trong thời gian chờ (cooldown), các tick tiếp theo không ghi thêm vào lịch sử kích hoạt.
- Chỉ subscribe các mã **đang có quy tắc giá được bật**. Quy tắc mới tạo được áp dụng ngay.
- Nếu mất kết nối, hệ thống tự kết nối lại. Trong thời gian đó, cảnh báo giá chạy theo chu kỳ bằng giá SSI như trước.
- Nguồn dữ liệu của lần kích hoạt realtime trong lịch sử là `tvsi_mqtt`.

