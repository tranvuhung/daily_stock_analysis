---
name: "stock_analyzer"
description: "Phân tích cổ phiếu và thị trường. Gọi kỹ năng này khi người dùng muốn phân tích một hoặc nhiều mã cổ phiếu, hoặc muốn tổng kết/đánh giá lại thị trường chung."
---

# Bộ phân tích cổ phiếu

Kỹ năng này dựa trên logic của `src/services/analyzer_service.py`, cung cấp khả năng phân tích từng cổ phiếu và toàn bộ thị trường.

## Cấu trúc đầu ra (`AnalysisResult`)

Các hàm phân tích trả về một đối tượng `AnalysisResult` (hoặc danh sách các đối tượng này) với cấu trúc khá phong phú. Dưới đây là tóm tắt các thành phần chính, kèm ví dụ đầu ra thực tế:

Thuộc tính `dashboard` chứa phần phân tích cốt lõi, được chia thành bốn khối chính:
1.  **`core_conclusion`**: Kết luận một câu, loại tín hiệu và khuyến nghị tỷ trọng vị thế.
2.  **`data_perspective`**: Dữ liệu kỹ thuật, bao gồm trạng thái xu hướng, vị trí giá, phân tích khối lượng và cấu trúc phân bố cổ phiếu (chip structure).
3.  **`intelligence`**: Thông tin định tính như tin tức, cảnh báo rủi ro và các chất xúc tác tích cực.
4.  **`battle_plan`**: Chiến lược có thể hành động, gồm điểm vào/ra (mục tiêu mua/bán), chiến lược quản lý vị thế và danh sách kiểm soát rủi ro.

## Cấu hình (`Config`)

Mọi hàm phân tích đều có thể nhận một đối tượng `config` tùy chọn. Đối tượng này chứa toàn bộ cấu hình của ứng dụng, ví dụ như API key, thiết lập thông báo và các tham số phân tích.

Nếu không truyền đối tượng `config`, hàm sẽ tự động dùng thực thể singleton toàn cục được nạp từ file `.env`.

**Tham khảo:** [`Config`](src/config.py)

## Các hàm

### 1. Phân tích một cổ phiếu

**Mô tả:** Phân tích một mã cổ phiếu và trả về kết quả phân tích.

**Khi nào dùng:** Khi người dùng yêu cầu phân tích một mã cổ phiếu cụ thể.

**Đầu vào:**
- `stock_code` (str): Mã cổ phiếu cần phân tích.
- `config` (Config, tùy chọn): Đối tượng cấu hình. Mặc định là `None`.
- `full_report` (bool, tùy chọn): Có sinh báo cáo đầy đủ hay không. Mặc định là `False`.
- `notifier` (NotificationService, tùy chọn): Đối tượng dịch vụ thông báo. Mặc định là `None`.

**Đầu ra:** `Optional[AnalysisResult]`
Một đối tượng `AnalysisResult` chứa kết quả phân tích, hoặc `None` nếu phân tích thất bại.

**Ví dụ:**

```python
from src.services.analyzer_service import analyze_stock

# Phân tích một cổ phiếu
result = analyze_stock("600989")
if result:
    print(f"Cổ phiếu: {result.name} ({result.code})")
    print(f"Điểm tâm lý thị trường: {result.sentiment_score}")
    print(f"Khuyến nghị hành động: {result.operation_advice}")
```

**Tham khảo:** [`analyze_stock`](src/services/analyzer_service.py)

### 2. Phân tích nhiều cổ phiếu

**Mô tả:** Phân tích một danh sách cổ phiếu và trả về danh sách kết quả phân tích.

**Khi nào dùng:** Khi người dùng muốn phân tích nhiều mã cổ phiếu cùng lúc.

**Đầu vào:**
- `stock_codes` (List[str]): Danh sách mã cổ phiếu cần phân tích.
- `config` (Config, tùy chọn): Đối tượng cấu hình. Mặc định là `None`.
- `full_report` (bool, tùy chọn): Có sinh báo cáo đầy đủ cho từng cổ phiếu hay không. Mặc định là `False`.
- `notifier` (NotificationService, tùy chọn): Đối tượng dịch vụ thông báo. Mặc định là `None`.

**Đầu ra:** `List[AnalysisResult]`
Một danh sách các đối tượng `AnalysisResult`.

**Ví dụ:**

```python
from src.services.analyzer_service import analyze_stocks

# Phân tích nhiều cổ phiếu
results = analyze_stocks(["600989", "000001"])
for result in results:
    print(f"Cổ phiếu: {result.name}, Khuyến nghị hành động: {result.operation_advice}")
```

**Tham khảo:** [`analyze_stocks`](src/services/analyzer_service.py)

### 3. Tổng kết, đánh giá lại thị trường chung

**Mô tả:** Thực hiện đánh giá lại (review) toàn bộ thị trường và trả về một báo cáo.

**Khi nào dùng:** Khi người dùng yêu cầu tổng quan thị trường, bản tóm tắt hoặc bản đánh giá lại thị trường.

**Đầu vào:**
- `config` (Config, tùy chọn): Đối tượng cấu hình. Mặc định là `None`.
- `notifier` (NotificationService, tùy chọn): Đối tượng dịch vụ thông báo. Mặc định là `None`.

**Đầu ra:** `Optional[str]`
Một chuỗi chứa báo cáo đánh giá thị trường, hoặc `None` nếu thất bại.

**Ví dụ:**

```python
from src.services.analyzer_service import perform_market_review

# Thực hiện đánh giá lại thị trường chung
report = perform_market_review()
if report:
    print(report)
```

**Tham khảo:** [`perform_market_review`](src/services/analyzer_service.py)

## Mô hình và Bot bên ngoài

- Nếu dùng **xAI Grok** làm mô hình phân tích của DSA: cấu hình `XAI_API_KEY` + `LITELLM_MODEL=xai/<ID mô hình chính thức>` (kết nối trực tiếp qua LiteLLM, không qua kênh trung gian được quản lý), rồi kiểm chứng bằng `python scripts/check_env.py --llm`. Xem ví dụ tại [`docs/LLM_CONFIG_GUIDE.md`](docs/LLM_CONFIG_GUIDE.md).
- **[Grok Bot](https://x.ai/bot)** ra mắt ngày 2026-08-11 là một hướng tiếp cận khác: đây là một "AI teammate" có Skills / Routines / MCP / computer use, nên gọi `analyze_stock` hoặc REST API của repo này qua HTTP, thay vì cấu hình bản thân Bot thành một LiteLLM provider. Hướng dẫn tích hợp xem tại [`docs/grok-bot-integration.md`](docs/grok-bot-integration.md).
