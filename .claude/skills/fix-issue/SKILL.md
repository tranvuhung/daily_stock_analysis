# Fix Issue

Triển khai bản sửa dựa trên kết quả phân tích issue, đồng thời bổ sung đầy đủ phần kiểm chứng, rủi ro và cách rollback theo quy tắc của repo.

**Repository**: https://github.com/ZhuLinsen/daily_stock_analysis

## Usage

```text
/fix-issue <issue_number>
```

## Prerequisites

Ưu tiên hoàn thành `/analyze-issue <issue_number>` trước, để chắc chắn vấn đề là thành lập và ranh giới đã rõ ràng.

## Instructions

### Step 1: Xác nhận baseline phân tích

Kiểm tra xem `.claude/reviews/issues/issue-<number>.md` có tồn tại hay không; nếu chưa có, hãy bổ sung phần phân tích issue trước, hoặc bổ sung kết luận phân tích tối thiểu ngay trong lần sửa này.

### Step 2: Đồng bộ baseline mã nguồn mới nhất và chọn cách làm việc an toàn

Trước khi bắt đầu sửa hoặc chuẩn bị tạo / cập nhật PR, hãy cập nhật mã nguồn theo `AGENTS.md`:

```bash
git status --short
git fetch --all --prune
# Chỉ chạy khi working tree sạch và nhánh hiện tại có thể fast-forward:
git pull --ff-only
```

- Mặc định thực hiện thay đổi tối thiểu và liên quan trực tiếp, dựa trên working tree hiện tại
- Chỉ thực hiện và chấp nhận kết quả của `git pull --ff-only` khi working tree sạch và nhánh hiện tại có upstream fast-forward được
- Nếu có thay đổi cục bộ, đang ở trạng thái conflict, có file chưa được theo dõi mang rủi ro, không có nhánh upstream hoặc không thể fast-forward: không được chạy `stash`, `reset`, ép chuyển nhánh hay ghi đè trạng thái cục bộ; trước hết hãy ghi lại HEAD cục bộ, baseline remote đã dùng và lý do không thể cập nhật working tree cục bộ
- Nếu sau đó cần tạo / cập nhật PR, trước tiên phải nêu rõ khác biệt giữa nhánh hiện tại và baseline mục tiêu; khi cần thiết hãy hỏi người dùng xác nhận rebase, merge hay tiếp tục dựa trên nhánh hiện tại
- Không mặc định chuyển nhánh hoặc ghi đè trạng thái làm việc hiện tại của người dùng
- Chỉ khi người dùng yêu cầu rõ ràng việc tạo nhánh thì mới thực hiện thao tác nhánh ở mức tối thiểu cần thiết

### Step 3: Triển khai bản sửa

- Dựa trên kết luận của issue để xác định các file liên quan
- Ưu tiên tái sử dụng module, điểm vào cấu hình, script và test hiện có
- Giữ hành vi mặc định tương thích ngược, tránh phá vỡ fallback / fail-open
- Nếu bản sửa ảnh hưởng tới hành vi người dùng thấy được, ngữ nghĩa cấu hình, CLI/API, triển khai, thông báo, cấu trúc báo cáo: phải đồng bộ cập nhật tài liệu liên quan, `docs/CHANGELOG.md`, `.env.example`
- Khi ghi mục vào `docs/CHANGELOG.md`, thêm một dòng vào phần `[Unreleased]`, theo định dạng `- [类型] 描述`, trong đó `[类型]` chọn từ `[新功能]/[改进]/[修复]/[文档]/[测试]/[chore]` tuỳ theo nội dung thay đổi lần này; chỉ dùng `[修复]` khi thực sự sửa bug; **không** được thêm `### 类目标题` bên trong `[Unreleased]`
- `README.md` chỉ chứa thông tin cấp trang chủ: định vị dự án, năng lực cốt lõi, hướng dẫn bắt đầu nhanh, các lối vào chính, tài trợ/hợp tác; không cần thiết thì không cập nhật README để tránh phình to liên tục
- Các nội dung chi tiết hơn như hành vi module, tương tác trang, cấu hình chuyên đề, hướng dẫn xử lý sự cố, hợp đồng trường dữ liệu, ngữ nghĩa triển khai và điều kiện biên: ưu tiên cập nhật vào `docs/*.md` tương ứng

### Step 4: Kiểm chứng theo phạm vi thay đổi

Thực hiện kiểm tra gần nhất theo ma trận kiểm chứng trong `AGENTS.md`:

- Backend ưu tiên: `./scripts/ci_gate.sh`
- Yêu cầu tối thiểu cho backend: `python -m py_compile <changed_python_files>`
- Frontend: `cd apps/dsa-web && npm ci && npm run lint && npm run build`
- Desktop: build Web trước, rồi build desktop

Nếu không thể hoàn tất kiểm chứng đầy đủ, bắt buộc phải ghi lại phần còn thiếu, lý do và rủi ro tiềm ẩn.

### Step 5: Cập nhật tài liệu phân tích issue

Bổ sung vào `.claude/reviews/issues/issue-<number>.md`:

```markdown
## Fix Implementation

**Date**: YYYY-MM-DD

### Changes Made

- File và các điểm thay đổi:

### Validation

- Đã thực hiện:
- Chưa thực hiện:

### Risks

- Điểm rủi ro:

### Rollback

- Cách rollback:
```

### Step 6: Các hành động tiếp theo cần xác nhận

Nếu người dùng yêu cầu tạo PR, sinh tiêu đề PR hoặc soạn mô tả PR, tiêu đề PR nên tuân theo `AGENTS.md`:

- Dùng định dạng `<loại>: <nội dung thay đổi>`, ví dụ `fix: 修复大盘分析历史记录丢失`
- Loại ưu tiên dùng `fix`/`feat`/`refactor`/`docs`/`chore`/`test`/`ci`
- Tiêu đề chỉ mô tả thay đổi thực tế, không nên thêm tiền tố nguồn gốc công cụ/agent như `[codex]`, `codex`, `autocode`, `copilot`
- Quy ước này chỉ nhằm giữ tính nhất quán khi cộng tác, không nên dùng riêng nó làm điều kiện chặn quy trình

Chỉ sau khi người dùng xác nhận rõ ràng mới được thực hiện:

- Tạo nhánh
- `git commit`
- `git push`
- Tạo PR
- Trả lời dưới issue hoặc đóng issue

## Allowed Auto-Actions (No Confirmation Needed)

- Đọc và phân tích mã nguồn
- Chạy `git fetch --all --prune`, và chạy `git pull --ff-only` khi working tree sạch và có thể fast-forward
- Áp dụng bản sửa tối thiểu liên quan trực tiếp tới nhiệm vụ hiện tại
- Chạy các kiểm chứng cục bộ không gây phá huỷ
- Cập nhật tài liệu phân tích issue ở máy cục bộ

## Actions Requiring Confirmation

1. Chuyển hoặc tạo nhánh
2. `git commit`
3. `git push`
4. Tạo PR
5. Trả lời hoặc đóng issue
