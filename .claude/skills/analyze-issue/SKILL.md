# Analyze Issue

Phân tích GitHub Issue, đánh giá tính xác thực, mức độ ưu tiên, ranh giới trách nhiệm của repo và hành động đề xuất.

**Repository**: https://github.com/ZhuLinsen/daily_stock_analysis/issues

## Usage

```text
/analyze-issue <issue_number>
```

## Instructions

Khi phân tích hãy dùng tiếng Việt ngắn gọn, ưu tiên tuân theo `AGENTS.md` ở thư mục gốc của repo.

### Step 1: Đồng bộ baseline mã nguồn mới nhất

Trước khi phân tích issue, bắt buộc phải làm mới trạng thái remote và cố gắng đưa bản địa tiến an toàn lên baseline mới nhất:

```bash
git status --short
git fetch --all --prune
# Chỉ chạy khi working tree sạch và nhánh hiện tại có thể fast-forward:
git pull --ff-only
```

- Chỉ thực hiện và chấp nhận kết quả của `git pull --ff-only` khi working tree sạch và nhánh hiện tại có upstream fast-forward được.
- Nếu có thay đổi cục bộ, đang ở trạng thái conflict, có file chưa được theo dõi mang rủi ro, không có nhánh upstream hoặc không thể fast-forward: không được chạy `stash`, `reset`, ép chuyển nhánh hay ghi đè trạng thái cục bộ; thay vào đó dùng `origin/main` hoặc các remote ref liên quan đã fetch để phân tích.
- Ghi lại kết quả đồng bộ trong mục `Evidence` của tài liệu đầu ra: HEAD cục bộ, baseline remote đã dùng, và lý do không cập nhật working tree cục bộ (nếu có).

### Step 2: Lấy thông tin Issue

```bash
gh issue view <issue_number> --repo ZhuLinsen/daily_stock_analysis
gh issue view <issue_number> --repo ZhuLinsen/daily_stock_analysis --comments
```

Nếu là bug, ưu tiên đối chiếu xem template issue đã cung cấp đủ các thông tin sau chưa:

- Đã đồng bộ lên phiên bản mới nhất chưa
- Commit hash / baseline phiên bản
- Môi trường chạy và các bước tái hiện
- Log hoặc thông báo lỗi

### Step 3: Trả lời 4 câu hỏi cốt lõi

1. Phiên bản đã rõ ràng chưa
2. Vấn đề có thật và có kiểm chứng được không
3. Có thuộc ranh giới trách nhiệm của repo không
4. Có đáng xử lý ngay không

### Step 4: Kiểm tra bằng chứng dựa trên hiện trạng repo

- Đọc mã nguồn, cấu hình, test, script, workflow và tài liệu liên quan
- Nếu vấn đề liên quan tới API, fallback nguồn dữ liệu, sinh báo cáo, gửi thông báo, xác thực, ứng dụng desktop, quy trình phát hành: phải ghi rõ phạm vi ảnh hưởng
- Xác định đây là bug thật, vấn đề cấu hình môi trường, vấn đề cách sử dụng, hay vấn đề của phụ thuộc bên ngoài
- Nếu nghi ngờ vấn đề đã được sửa, hãy kiểm tra mã nguồn hiện tại thay vì chỉ đọc mô tả issue

### Step 5: Đưa ra kết luận

Tối thiểu phải nêu các trường sau:

- `Baseline phiên bản`: mới nhất / không phải mới nhất / không cung cấp
- `Có hợp lý không`: có/không + lý do
- `Có phải là issue không`: có/không + lý do
- `Có dễ xử lý không`: có/không + điểm khó
- `Kết luận`: `thành lập / thành lập một phần / không thành lập`
- `Phân loại`: `bug / feature / docs / question / external`
- `Mức ưu tiên`: `P0 / P1 / P2 / P3`
- `Độ khó`: `easy / medium / hard`
- `Hành động đề xuất`: `sửa ngay / đưa vào kế hoạch sửa / làm rõ bằng tài liệu / đóng issue`

### Step 6: Sinh tài liệu phân tích

Lưu vào `.claude/reviews/issues/issue-<number>.md`

## Output Document Format

```markdown
# Issue #<number> Analysis

**Date**: YYYY-MM-DD
**Status**: Pending Review

## Summary

- Baseline phiên bản:
- Có hợp lý không:
- Có phải là issue không:
- Có dễ xử lý không:
- Kết luận:
- Phân loại:
- Mức ưu tiên:
- Độ khó:
- Hành động đề xuất:

## Evidence

- Baseline đồng bộ mã nguồn:
- Thông tin issue then chốt:
- Bằng chứng từ mã nguồn/script/workflow:

## Impact Scope

- Module bị ảnh hưởng:
- Đường chạy bị ảnh hưởng (cục bộ / Docker / GitHub Actions / API / Web / Desktop):

## Root Cause / Main Reasoning

<nguyên nhân gốc hoặc căn cứ đánh giá chính>

## Proposed Handling

<đề xuất cách sửa, làm rõ hoặc đóng issue>

Nếu đề xuất tạo PR ở bước sau, tiêu đề PR gợi ý phải tuân theo `AGENTS.md`: dùng `<loại>: <nội dung thay đổi>`, không thêm tiền tố nguồn gốc công cụ/agent như `[codex]`, `codex`, `autocode`, `copilot`; quy ước này chỉ nhằm nhắc nhở tính nhất quán khi cộng tác, không nên dùng riêng nó làm điều kiện chặn quy trình review.

## Risks And Rollback

- Điểm rủi ro:
- Nếu sửa thì rollback thế nào:

## Draft Reply

<nội dung trả lời đề xuất>
```

## Allowed Auto-Actions (No Confirmation Needed)

- Lấy chi tiết issue và các bình luận
- Chạy `git fetch --all --prune`, và chạy `git pull --ff-only` khi working tree sạch và có thể fast-forward
- Đọc mã nguồn, cấu hình, script, workflow và tài liệu liên quan
- Sinh tài liệu phân tích

## Actions Requiring Confirmation

Trước khi thực hiện các hành động sau, phải hỏi người dùng:

1. Thêm hoặc sửa nhãn (label)
2. Bình luận dưới issue
3. Đóng issue
4. Bắt đầu sửa issue
