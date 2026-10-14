# Thư mục chiến lược giao dịch / Trading Strategies

Thư mục này chứa các **file chiến lược giao dịch viết bằng ngôn ngữ tự nhiên** (định dạng YAML). Khi khởi động, hệ thống tự nạp mọi file `.yaml` trong thư mục này.

Với người dùng và tài liệu, ta gọi các năng lực này là "chiến lược". Trong code, cấu hình và trường API, chúng được đặt tên thống nhất là `skill` — có thể hiểu là "gói năng lực chiến lược tái sử dụng được".

## Cách viết chiến lược tuỳ chỉnh (Strategy Skill)

Chỉ cần tạo một file `.yaml` mô tả chiến lược giao dịch bằng tiếng Việt hoặc tiếng Anh, **không cần viết code**.

### Mẫu tối giản

```yaml
name: my_strategy          # Định danh duy nhất (chữ Latin, nối bằng gạch dưới)
display_name: Chiến lược của tôi   # Tên hiển thị trên Web/Bot/báo cáo
description: Mô tả ngắn công dụng của chiến lược

instructions: |
  Mô tả chiến lược của bạn...
  Viết bằng ngôn ngữ tự nhiên: tiêu chí đánh giá, điều kiện vào lệnh, điều kiện thoát lệnh...
  Có thể nhắc tên công cụ (như get_daily_history, analyze_trend) để hướng dẫn AI dùng dữ liệu nào.
```

### Mẫu đầy đủ

```yaml
name: my_strategy
display_name: Chiến lược của tôi
# Tuỳ chọn: tên tiếng Anh đưa vào prompt LLM (không điền thì dùng display_name).
# display_name / aliases vẫn dùng để hiển thị và đối chiếu tên trên Web, Bot và báo cáo.
# Optional: English name used inside LLM prompts; display_name/aliases stay for UI matching.
display_name_en: My Strategy
description: Mô tả ngắn bối cảnh thị trường phù hợp với chiến lược

# Phân loại: trend (xu hướng), pattern (mô hình), reversal (đảo chiều), framework (khung phân tích)
category: trend

# Số thứ tự các nguyên tắc giao dịch cốt lõi liên quan (1-7), tuỳ chọn
core_rules: [1, 2]

# Danh sách công cụ chiến lược cần dùng, tuỳ chọn
# Công cụ có sẵn: get_daily_history, analyze_trend, get_realtime_quote,
#                 search_stock_news, get_stock_info
required_tools:
  - get_daily_history
  - analyze_trend

# Bí danh tuỳ chọn (dùng cho /ask và chọn chiến lược bằng ngôn ngữ tự nhiên)
aliases: ["chiến lược của tôi", "mô hình của tôi"]

# Metadata điều khiển hành vi mặc định (tuỳ chọn)
# default_active: có thuộc tập chiến lược kích hoạt mặc định không
# default_router: có thuộc tập fallback của router không
# default_priority: thứ tự hiển thị/ưu tiên mặc định, số càng nhỏ càng đứng trước
# market_regimes: nhãn trạng thái thị trường mà chiến lược ưu tiên
default_active: true
default_router: false
default_priority: 100
market_regimes: [trending_up]

# Hướng dẫn chi tiết (ngôn ngữ tự nhiên, hỗ trợ Markdown)
instructions: |
  **Tên chiến lược của tôi**

  Tiêu chí đánh giá:

  1. **Điều kiện 1**:
     - Dùng `analyze_trend` kiểm tra cách sắp xếp các đường MA.
     - Mô tả đặc điểm xu hướng bạn mong muốn...

  2. **Điều kiện 2**:
     - Mô tả yêu cầu về khối lượng...

  Điều chỉnh điểm:
  - Mức điều chỉnh sentiment_score đề xuất khi thoả điều kiện
  - Ghi tên chiến lược trong `buy_reason`
```

### Tham chiếu nguyên tắc giao dịch cốt lõi

| Số | Nguyên tắc |
|----|------------|
| 1 | Vào lệnh chặt chẽ: chỉ cân nhắc khi độ lệch so với MA < 5% |
| 2 | Giao dịch theo xu hướng: MA5 > MA10 > MA20 xếp tăng |
| 3 | Hiệu quả trước: khối lượng xác nhận xu hướng |
| 4 | Ưu tiên điểm mua: điều chỉnh về hỗ trợ của đường MA |
| 5 | Rà soát rủi ro: tin xấu là phủ quyết |
| 6 | Giá – khối lượng đồng thuận: khối lượng xác nhận biến động giá |
| 7 | Nới lỏng cho cổ phiếu xu hướng mạnh: cổ phiếu dẫn dắt có thể nới tiêu chí |

## Thư mục chiến lược tuỳ chỉnh

Ngoài thư mục này (chiến lược dựng sẵn), bạn có thể chỉ định thêm thư mục chiến lược tuỳ chỉnh qua biến môi trường:

```env
AGENT_SKILL_DIR=./my_skills
```

Hệ thống nạp cả chiến lược dựng sẵn lẫn chiến lược tuỳ chỉnh. Nếu trùng tên, chiến lược tuỳ chỉnh ghi đè chiến lược dựng sẵn.

Tên biến môi trường vẫn là `AGENT_SKILL_DIR` (tên cấu hình nội bộ đã thống nhất); về mặt sản phẩm, nó vẫn có nghĩa là "thư mục chiến lược tuỳ chỉnh".
