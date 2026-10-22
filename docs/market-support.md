# Phạm vi thị trường

Hệ thống **chỉ hỗ trợ thị trường chứng khoán Việt Nam** (HOSE / HNX / UPCoM). Toàn bộ hỗ trợ A-share, Hồng Kông, Mỹ, Nhật, Hàn và Đài Loan đã bị gỡ khỏi code, cấu hình, Web và tài liệu.

Nguồn sự thật duy nhất là `src/core/markets.py`: registry chỉ có một `MarketSpec` là `vn` (múi giờ `Asia/Ho_Chi_Minh`, tiền tệ `VND`).

## Tương thích cấu hình cũ

| Cấu hình / tham số | Hành vi hiện tại |
| --- | --- |
| `SUPPORTED_MARKETS` | Vẫn được đọc để tương thích. Mọi giá trị khác `vn` (`cn`, `us`, `all`, ...) chỉ ghi log cảnh báo rồi bị bỏ qua; tiến trình không dừng. |
| `MARKET_REVIEW_REGION` | Chỉ còn `vn`. Giá trị cũ (`cn`, `hk`, `us`, `jp`, `kr`, `both`, danh sách phân tách dấu phẩy) được chuẩn hoá về `vn`. |
| Tham số `market` / `region` trên API | Chỉ chấp nhận `vn`; giá trị region cũ được chuẩn hoá về `vn`. |
| Biến môi trường của nguồn dữ liệu / kênh thông báo đã gỡ | Bị bỏ qua (không lỗi). Ví dụ: `TUSHARE_TOKEN`, `TICKFLOW_*`, `LONGBRIDGE_*`, `FINNHUB_*`, `ALPHAVANTAGE_*`, `WECHAT_*`, `FEISHU_*`, `DINGTALK_*`, `PUSHPLUS_*`, `SERVERCHAN3_*`, `ASTRBOT_*`, `ENABLE_CHIP_DISTRIBUTION`. |

## Định dạng mã

Ba cách viết sau được quy về cùng một `canonical_id`, tránh `stock_daily` có dòng trùng:

- Mã trần: `HPG`, `FPT`, `VCB`, `X77` (3 ký tự chữ/số viết hoa).
- Hậu tố tường minh: `HPG.VN`.
- Tiền tố tường minh: `vnHPG` (tiền tố viết thường).
- Chứng chỉ quỹ ETF / quỹ đóng: `FUEVN100`, `E1VFVN30`, `FUCVREIT` (8 ký tự, tiền tố `FU` hoặc `E1VF`; cũng nhận `FUEVN100.VN`, `vnFUEVN100`). Dữ liệu ngày lấy từ VCI như cổ phiếu.

Chỉ số: `VNINDEX`, `VN30`, `VN100`, `HNXIndex`, `HNX30`, `UPCOMINDEX` (nhận thêm `UPCOM`). Không có mã chỉ số nào tên `HNXUPCOMINDEX`: đó chỉ là cách viết riêng của VCI (`HNXUpcomIndex`) và SSI (`HNXUPCOMINDEX`), được đổi trong adapter tương ứng. Người dùng nhập không phân biệt hoa/thường, nhưng tên gửi sang VCI **phân biệt hoa/thường** (`HNXINDEX` trả rỗng, `HNXIndex` mới có dữ liệu); bảng ánh xạ ở `data_provider/base.py:VN_INDEX_SYMBOLS`.

Mã không thuộc Việt Nam (ví dụ `600519`, `hk00700`, `AAPL`) bị từ chối ở đầu vào; endpoint lấy giá trả 404.

## Nguồn dữ liệu

| Tình huống | Nguồn | Ghi chú |
| --- | --- | --- |
| Nến ngày / nến ngày chỉ số | `FiinTradeFetcher` (ưu tiên -1) | Mặc định bật nhưng chỉ chạy khi có `FIINTRADE_ORIGIN` (origin trong whitelist của FiinTrade); tắt bằng `FIINTRADE_ENABLED=false`. Phủ cổ phiếu HOSE / HNX / UPCOM (tra `organCode` qua `Master/GetListOrganization`), ETF và chỉ số (gồm UPCoM-Index); mã không tra được hoặc FiinTrade lỗi thì tự chuyển sang VCI. Giá cùng cơ sở điều chỉnh với VCI. Lưu ý UPCoM-Index: từ 12/08/2025 close của FiinTrade khác VCI/SSI biểu đồ (lệch tới ~1,6 điểm) nhưng trùng bảng điện SSI; hệ thống lấy FiinTrade trước, lỗi mới sang VCI. |
| Nến ngày / nến ngày chỉ số | `VciFetcher` (ưu tiên 0) | `POST trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart`, không cần API key. **Mỗi lần chỉ hỏi một mã**, nên không có prefetch nến ngày hàng loạt. |
| Giá realtime + định giá | `FiinTradeFetcher` | Khi chạy (có origin) thì đứng đầu chuỗi giá realtime (token `fiintrade`). `PriceDepth`: giá, tham chiếu/trần/sàn, khối ngoại, thoả thuận, P/E, P/B, vốn hoá, EPS, ROE. `GetStockEvents`: cổ tức TTM, phát hành, giao dịch nội bộ. Cổ phiếu/ETF cả ba sàn; chỉ số rơi sang VCI. |
| Giá realtime | `SsiFetcher` (ưu tiên 1) | `GET iboard-query.ssi.com.vn/stock/exchange/{hose,hnx,upcom}`, **một request phủ cả sàn**; `prefetch_realtime_quotes` chỉ cần ba request cho toàn thị trường. |
| Nến ngày dự phòng | `SsiFetcher` | `GET iboard-api.ssi.com.vn/statistics/charts/history` (định dạng TradingView UDF). |
| Bổ sung tuỳ chọn | `VnstockFetcher` (ưu tiên 2) | Cần `VNSTOCK_ENABLED=true` và đã cài `requirements-vn-optional.txt`; mặc định tắt vì giấy phép vnstock là "Personal Use" và có telemetry. |
| Dự phòng thứ ba | `YfinanceFetcher` (ưu tiên 4) | Hậu tố `.VN`, không cần key. **Chỉ phủ HOSE** (HNX / UPCoM / chỉ số không có dữ liệu), và nến ngày **không ghi vào DB**. Xem [Độ ổn định nguồn dữ liệu](data-source-stability.md). |
| Giá realtime qua MQTT (tuỳ chọn) | TVSI MQTT | Tắt mặc định; cấu hình `TVSI_MQTT_*` trong `.env.example`. |

Các nguồn A-share / quốc tế đã bị xoá: akshare, efinance, tushare, pytdx, baostock, tencent, tickflow, longbridge, futu, finnhub, alphavantage.

## Điểm lại thị trường (`MARKET_REVIEW_REGION=vn`)

| Thành phần | Nguồn | Ghi chú |
| --- | --- | --- |
| Chỉ số chính | `FiinTradeFetcher.get_main_indices` khi có `FIINTRADE_ORIGIN`, nếu không thì `VciFetcher.get_main_indices(region="vn")` | VN-Index, VN30, HNX-Index, UPCoM-Index. FiinTrade lấy cả 4 trong một request (`GetLatestIndices`), giá trị giao dịch = khớp lệnh + thoả thuận (cùng ngữ nghĩa VCI). VCI dựng từ nến ngày mới nhất; một chỉ số lỗi thì chỉ bỏ qua chỉ số đó. |
| Độ rộng thị trường | `FiinTradeFetcher.get_region_market_stats("vn")` khi có origin, nếu không thì `SsiFetcher` | Số mã tăng/giảm/đứng giá, trần/sàn, giá trị khớp lệnh (tỷ đồng). FiinTrade cộng ba chỉ số sàn và có thêm giá trị thoả thuận, khối ngoại ròng 3 sàn. |
| Dòng tiền khối ngoại / tự doanh | `FiinTradeFetcher.get_market_money_flow("vn")` (chỉ khi có origin) | Sàn HOSE: ròng hôm nay / 1 tuần / 1 tháng / YTD kèm top 5 mã mua/bán ròng. Tự doanh công bố sau phiên, kỳ mới nhất có thể là phiên trước hoặc gộp hai phiên, nên prompt luôn ghi khoảng ngày. |
| Tin tức | `SearchService` | Truy vấn tiếng Việt theo `VN_PROFILE.news_queries`, ngữ cảnh `VNINDEX`. |

Giới hạn:

- Chỉ tính cổ phiếu/ETF đã có giá khớp; loại chứng quyền, phái sinh, trái phiếu và mã chưa giao dịch.
- Giá trị giao dịch ở độ rộng là giá trị **khớp lệnh**, không gồm thoả thuận (FiinTrade báo thoả thuận ở dòng riêng).
- Không có xếp hạng ngành. Dòng tiền khối ngoại / tự doanh chỉ có khi FiinTrade chạy; khi không có, prompt vẫn cấm model tự bịa số liệu dòng tiền.
- UPCoM-Index từ FiinTrade theo chuỗi riêng của FiinTrade: tham chiếu 125,22 so với 125,51 của VCI/SSI biểu đồ (21/10/2026). Nến ngày cũng lấy từ FiinTrade, nên điểm lại thị trường và nến ngày nhất quán với nhau.
- Market Light và cảnh báo thị trường (`target_scope=market`, `target=vn`) chỉ hỗ trợ `vn`. Xem [alerts.md](alerts.md).

## Ngôn ngữ báo cáo

Chỉ còn `vi` và `en` (`src/report_language.py`); `zh` / `ko` đã bị gỡ. Ngôn ngữ giao diện Web và ngôn ngữ báo cáo là hai hợp đồng độc lập.

## Tính năng đã gỡ (chỉ dành cho A-share hoặc thị trường khác)

| Tính năng | Ghi chú tương thích |
| --- | --- |
| Lọc cổ phiếu (screening engine) | Toàn bộ `/api/v1/screening/*` đã bị xoá. |
| Phân bố chip (chip distribution) | `ENABLE_CHIP_DISTRIBUTION` bị bỏ qua. |
| Cấu trúc thị trường / điểm nóng, xếp hạng ngành và concept | Trường `belong_boards`, `sector_rankings`, `concept_rankings`, `market_structure` đã bị xoá khỏi `ReportDetails`. |
| Dòng tiền / bảng ngành A-share | Khuyến nghị mua của mã Việt Nam không còn bị hạ bậc vì thiếu dữ liệu dòng tiền (vốn chỉ có ở A-share). |
| Tâm lý mạng xã hội cổ phiếu Mỹ | Đã xoá. |
| Dòng tiền ba nhóm tổ chức Đài Loan | Đã xoá. |
| Làm mới chỉ mục cổ phiếu từ xa | Đã xoá; chỉ mục tự động hoàn thành sinh bằng `python scripts/generate_vn_index.py` từ danh sách mã VCI. |
| Nhập danh mục từ Futu (`--portfolio futu`) | Tuỳ chọn CLI đã bị xoá. |

## Giới hạn đã biết

- **Đơn vị giá không đồng nhất giữa các nguồn**: nến ngày VCI và snapshot SSI dùng "đồng" (21300), còn API biểu đồ SSI và `vnstock` dùng "nghìn đồng" (21.3); hai nguồn sau được nhân 1000 khi chuẩn hoá. Sai đơn vị không ném lỗi mà làm lệch MA / giá cắt lỗ 1000 lần, nên `tests/test_vn_source_drift.py` kiểm tra độ khớp giá đóng cửa giữa các nguồn.
- **Yahoo chỉ là dự phòng thứ ba**: phủ HOSE nhưng không phủ HNX / UPCoM / chỉ số, và **mốc điều chỉnh giá khác VCI** (ví dụ MBB lệch hệ số cố định 1.043938 trước ngày giao dịch không hưởng quyền). Vì vậy nến ngày lấy từ Yahoo chỉ phục vụ lần phân tích hiện tại, không ghi vào `stock_daily`.
- **Không có lịch giao dịch sàn**: `exchange-calendars` không có lịch HOSE/HNX/UPCoM. `is_market_open('vn')` chỉ loại thứ Bảy / Chủ nhật; nghỉ lễ dài (Tết) vẫn coi là ngày giao dịch (fail-open). `market_phase` trả `unknown`.
- **Không có cơ bản / dòng tiền / ngành**: các khối này được hạ cấp `not_supported`.
- **Chứng quyền / phái sinh / trái phiếu chưa hỗ trợ**: mã 8-9 ký tự trong danh sách VCI (`CFPT2314`, `VN30F2512`) không tham gia phân tích (chứng chỉ quỹ `FU*` / `E1VF*` là ngoại lệ).

## Rollback

Việc gỡ đa thị trường là thay đổi phá vỡ (breaking change), không có công tắc bật lại. Muốn quay lại hành vi cũ thì revert các commit liên quan trên nhánh `refactor/vn-only-market`; không có migration dữ liệu hay thay đổi cấu trúc bảng.
