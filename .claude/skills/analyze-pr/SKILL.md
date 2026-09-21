# Analyze PR

Phân tích GitHub Pull Request, đánh giá tính cần thiết, mức độ đầy đủ của mô tả, bằng chứng kiểm chứng, rủi ro chính và khả năng merge trực tiếp.

**Repository**: https://github.com/ZhuLinsen/daily_stock_analysis/pulls

## Usage

```text
/analyze-pr <pr_number>
```

## Instructions

Khi phân tích hãy dùng tiếng Việt ngắn gọn, ưu tiên tuân theo `AGENTS.md` ở thư mục gốc của repo và `.github/PULL_REQUEST_TEMPLATE.md`.

### Step 1: Đồng bộ baseline mã nguồn mới nhất

Trước khi phân tích PR, bắt buộc phải làm mới trạng thái remote và cố gắng đưa bản địa tiến an toàn lên baseline mới nhất:

```bash
git status --short
git fetch --all --prune
# Chỉ chạy khi working tree sạch và nhánh hiện tại có thể fast-forward:
git pull --ff-only
```

- Chỉ thực hiện và chấp nhận kết quả của `git pull --ff-only` khi working tree sạch và nhánh hiện tại có upstream fast-forward được.
- Nếu có thay đổi cục bộ, đang ở trạng thái conflict, có file chưa được theo dõi mang rủi ro, không có nhánh upstream hoặc không thể fast-forward: không được chạy `stash`, `reset`, ép chuyển nhánh hay ghi đè trạng thái cục bộ; thay vào đó dùng `origin/main`, PR head hoặc GitHub diff đã fetch để phân tích.
- Ghi lại kết quả đồng bộ trong mục `Validation Evidence` của tài liệu đầu ra: HEAD cục bộ, baseline remote đã dùng, và lý do không cập nhật working tree cục bộ (nếu có).

### Step 2: Lấy thông tin cơ bản của PR

```bash
gh pr view <pr_number> --repo ZhuLinsen/daily_stock_analysis
gh pr view <pr_number> --repo ZhuLinsen/daily_stock_analysis --comments
gh pr checks <pr_number> --repo ZhuLinsen/daily_stock_analysis
gh pr diff <pr_number> --repo ZhuLinsen/daily_stock_analysis
```

Nếu có CI thất bại, ưu tiên xem log lỗi trước thay vì chạy lại toàn bộ kiểm tra ở máy cục bộ:

```bash
gh run view <run_id> --log-failed
```

### Step 3: Kiểm tra tiêu đề và mức độ đầy đủ của mô tả

Trước hết kiểm tra tiêu đề PR có phù hợp với khuyến nghị không-chặn trong `AGENTS.md` hay không:

- Định dạng nên là `<loại>: <nội dung thay đổi>`, ví dụ `fix: 修复大盘分析历史记录丢失`
- Loại ưu tiên dùng `fix`/`feat`/`refactor`/`docs`/`chore`/`test`/`ci`
- Không nên chứa tiền tố nguồn gốc công cụ/agent như `[codex]`, `codex`, `autocode`, `copilot`
- Tiêu đề phải mô tả thay đổi thực tế; nếu tiêu đề không khớp với diff thì chỉ ra ở phần đánh giá mức độ đầy đủ của mô tả, nhưng không dùng riêng nó làm điều kiện chặn quy trình review.

Đối chiếu với `.github/PULL_REQUEST_TEMPLATE.md`, xác nhận PR đã bao phủ:

- `PR Type`
- `Background And Problem`
- `Scope Of Change`
- `Issue Link`
- `Verification Commands And Results`
- `Visual Evidence` (chỉ bắt buộc ảnh chụp màn hình hoặc bằng chứng trực quan thay thế khi PR sửa định dạng báo cáo, hiệu quả render báo cáo hoặc giao diện Web UI)
- `Compatibility And Risk`
- `Rollback Plan`

Nếu PR liên quan tới ngữ nghĩa tương thích của mô hình/API bên thứ ba, giá trị cố định của tham số request, routing kiểu OpenAI-compatible, YAML alias, hành vi fallback, hoặc logic lưu / xoá / migrate cấu hình lúc chạy, cần kiểm tra thêm xem mô tả có ghi rõ:

- Link nguồn chính thức hoặc thông báo chính thức
- Phạm vi tương thích của phụ thuộc đang khoá / runtime hiện tại (ví dụ cửa sổ phiên bản LiteLLM)
- Mức độ bao phủ của các đường gọi đã được kiểm chứng
- Cấu hình cũ có bị ghi đè, xoá trắng, migrate ngầm hay giữ nguyên
- Đường rollback tối thiểu (thường là revert chính PR này)

Nếu PR sửa định dạng báo cáo, hiệu quả render báo cáo hoặc giao diện Web UI, còn phải kiểm tra `Visual Evidence` đã đính kèm ảnh chụp báo cáo / trang bị ảnh hưởng chưa; khi có khác biệt trước–sau thì ưu tiên kiểm tra ảnh so sánh trước–sau. Nếu không chụp được màn hình, mô tả phải nêu lý do và bằng chứng trực quan thay thế.

### Step 4: Ưu tiên dùng bằng chứng từ CI / Diff

- Trước tiên dựa vào `gh pr checks`, PR diff, các test hiện có và log workflow để đánh giá vấn đề
- Chỉ bổ sung kiểm chứng tối thiểu ở máy cục bộ khi CI chưa bao phủ phạm vi thay đổi, kết quả CI không đủ để kết luận vấn đề, hoặc cần xác minh rủi ro hồi quy then chốt
- Không mặc định chuyển nhánh hiện tại hoặc chạy `gh pr checkout`

Nếu buộc phải bổ sung kiểm chứng cục bộ, hãy chọn kiểm tra gần nhất với phạm vi thay đổi, ví dụ:

- Backend: `./scripts/ci_gate.sh` hoặc `python -m py_compile <changed_python_files>`
- Frontend: `cd apps/dsa-web && npm ci && npm run lint && npm run build`
- Desktop: build Web trước, rồi build Electron

### Step 5: Đánh giá tính đúng đắn và rủi ro

Trọng tâm kiểm tra:

- Có giải quyết đúng vấn đề đã xác định và không mang theo thay đổi không liên quan hay không
- Có phá vỡ tương thích API / Schema / Web / Desktop hay không
- Có phá vỡ fallback, đường suy giảm (degradation), chuỗi thông báo hoặc quy trình phát hành hay không
- Có lỗi logic rõ ràng, nuốt ngoại lệ, vấn đề bảo mật, hoặc thay đổi ngữ nghĩa cấu hình mà chưa đồng bộ tài liệu hay không

### Step 6: Sinh tài liệu review

Lưu vào `.claude/reviews/prs/pr-<number>.md`

## Output Document Format

```markdown
# PR #<number> Analysis

**Date**: YYYY-MM-DD
**Status**: Pending Review

## Findings

- [mức độ nghiêm trọng] file:line - mô tả vấn đề

## Summary

- Tính cần thiết:
- Có issue tương ứng không:
- Loại PR:
- PR title:
- Mức độ đầy đủ của description:
- Tình hình kiểm chứng:
- Rủi ro chính:
- Có thể merge trực tiếp không:

## Validation Evidence

- Baseline đồng bộ mã nguồn:
- Kết luận CI:
- Kiểm chứng cục bộ bổ sung (nếu có):

## Compatibility And Risk

- API / Web / Desktop:
- Cấu hình / Docker / GitHub Actions:
- fallback / thông báo / cấu trúc báo cáo:
- Phụ thuộc bên thứ ba / nguồn ràng buộc chính thức:
- Cửa sổ tương thích runtime / các đường gọi đã bao phủ:
- Rủi ro migrate hoặc ghi đè ngầm cấu hình cũ:

## Draft Review Comment

<nội dung bình luận đề xuất>
```

## Allowed Auto-Actions (No Confirmation Needed)

- Lấy metadata, diff, bình luận và trạng thái CI của PR
- Chạy `git fetch --all --prune`, và chạy `git pull --ff-only` khi working tree sạch và có thể fast-forward
- Đọc mã nguồn, template, workflow và tài liệu liên quan
- Chạy kiểm chứng cục bộ tối thiểu khi cần thiết
- Sinh tài liệu review

## Actions Requiring Confirmation

Trước khi thực hiện các hành động sau, phải hỏi người dùng:

1. Đăng bình luận
2. Approve PR
3. Request changes
4. Merge PR
5. Đóng PR
