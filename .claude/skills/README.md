# Repository Claude Skills

Thư mục này lưu các skill cộng tác ở cấp độ repo, thuộc tài sản của kho mã nguồn.

- Nguồn sự thật của quy tắc: `AGENTS.md` ở thư mục gốc của repo
- Lối vào tương thích: `CLAUDE.md` ở thư mục gốc (phải là symlink trỏ tới `AGENTS.md`)
- Các skill trong thư mục này phải nhất quán với `AGENTS.md`
- `.claude/reviews/` là sản phẩm phân tích cục bộ, không phải nguồn sự thật của quy tắc

Nếu sau này cần tương thích với các thư mục agent khác (ví dụ `.agents/skills/` hoặc `.github/skills/`), phải xác định rõ nguồn sự thật duy nhất trước, rồi đồng bộ bằng script hoặc cơ chế mirror, thay vì duy trì thủ công nhiều bản nội dung trùng nghĩa trong thời gian dài.
