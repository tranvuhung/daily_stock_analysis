# Trung tâm tài liệu

Đây là điểm vào tài liệu của dự án. README chỉ giới thiệu tổng quan và cách chạy nhanh; cấu hình chi tiết, triển khai, hướng dẫn tính năng và xử lý sự cố đi từ trang này.

> Phạm vi hiện tại: hệ thống **chỉ hỗ trợ thị trường chứng khoán Việt Nam** (HOSE / HNX / UPCoM). Xem [Phạm vi thị trường](market-support.md).
>
> Nhiều tài liệu chuyên đề bên dưới vẫn đang viết bằng tiếng Trung (ghi chú "tiếng Trung"); khi nội dung mâu thuẫn với `.env.example` hoặc code thì code là nguồn sự thật.

## Chọn theo mục đích

| Tôi muốn | Đọc trước | Đọc tiếp |
| --- | --- | --- |
| Biết dự án làm được gì | [README](../README.md) | [Hướng dẫn đầy đủ (EN)](full-guide_EN.md) |
| Chạy dự án lần đầu | [Cài đặt client cho người mới](beginner-client-setup.md) (tiếng Trung) | [Hướng dẫn đầy đủ (EN)](full-guide_EN.md) |
| Cấu hình mô hình LLM | [Hướng dẫn cấu hình LLM](LLM_CONFIG_GUIDE.md) (tiếng Trung) | [Cấu hình nhà cung cấp LLM](llm-providers.md) (tiếng Trung, có xAI Grok) |
| Cấu hình thông báo | [Năng lực thông báo](notifications.md) (tiếng Trung) | [Hướng dẫn đầy đủ (EN)](full-guide_EN.md) |
| Triển khai lên server / cloud | [Hướng dẫn triển khai (EN)](DEPLOY_EN.md) | [Triển khai WebUI trên cloud](deploy-webui-cloud.md), [Triển khai Zeabur](docker/zeabur-deployment.md) |
| Chạy trên Windows trong mạng LAN | [Chạy trên Windows qua LAN](windows-lan-deploy.md) | [Đóng gói desktop](desktop-package.md) |
| Dùng Bot / IM | [Lệnh Bot](bot-command.md) (tiếng Trung) | [Cấu hình Discord Bot](bot/discord-bot-config.md), [Tích hợp Grok Bot](grok-bot-integration.md) |
| Xử lý sự cố khi chạy | [FAQ (EN)](FAQ_EN.md) | [Nhật ký thay đổi](CHANGELOG.md) |
| Xử lý lỗi / suy giảm nguồn dữ liệu | [Độ ổn định nguồn dữ liệu](data-source-stability.md) | [Phạm vi thị trường](market-support.md) |
| Đóng góp code hoặc PR | [Hướng dẫn đóng góp](CONTRIBUTING.md) | [Đặc tả API](architecture/api_spec.json) |

## Bắt đầu nhanh

| Tài liệu | Nội dung |
| --- | --- |
| [README](../README.md) | Định vị dự án, năng lực chính, chạy nhanh |
| [Hướng dẫn đầy đủ (EN)](full-guide_EN.md) | Chuẩn bị môi trường, cách chạy, cấu hình, triển khai, câu hỏi thường gặp |
| [FAQ (EN)](FAQ_EN.md) | Câu hỏi thường gặp về cấu hình, mô hình, thông báo, triển khai |
| [Phạm vi thị trường](market-support.md) | Thị trường Việt Nam: định dạng mã, sàn, chỉ số, nguồn dữ liệu, các tính năng đã gỡ |
| [Độ ổn định nguồn dữ liệu](data-source-stability.md) | Chuỗi VCI → SSI → vnstock (tuỳ chọn) → Yahoo, fallback và cấu hình khuyến nghị |
| [Nhật ký thay đổi](CHANGELOG.md) | Thay đổi theo phiên bản, điều chỉnh năng lực và ghi chú di chuyển |

## Cấu hình

| Tài liệu | Nội dung |
| --- | --- |
| [Hướng dẫn cấu hình LLM](LLM_CONFIG_GUIDE.md) (tiếng Trung) / [EN](LLM_CONFIG_GUIDE_EN.md) | Kênh mô hình, cấu hình ba tầng, trang cài đặt Web |
| [Cấu hình nhà cung cấp LLM](llm-providers.md) (tiếng Trung) | Preset provider, ánh xạ GitHub Actions, phân loại lỗi và chẩn đoán |
| [Ví dụ LiteLLM YAML](examples/litellm_config.example.yaml) | Ví dụ cấu hình LiteLLM nhiều kênh |
| [Ví dụ Skill cho Grok Bot](examples/grok_bot/README.md) | Skill tối giản dán vào Grok Bot, gọi REST có sẵn |
| [Năng lực thông báo](notifications.md) (tiếng Trung) | Telegram, Email, Discord, Slack, Pushover, ntfy, Gotify, Custom Webhook |

## Chuyên đề sử dụng

| Tài liệu | Nội dung |
| --- | --- |
| [Lệnh Bot](bot-command.md) (tiếng Trung) / [EN](bot-command_EN.md) | Lệnh Bot, Webhook, tích hợp nền tảng và callback |
| [Cấu hình Discord Bot](bot/discord-bot-config.md) (tiếng Trung) | Cấu hình Discord Bot |
| [Trung tâm cảnh báo realtime](alerts.md) (tiếng Trung) | EventMonitor, quản lý rule trên Web, kết quả thông báo, cooldown |
| [Hướng dẫn sử dụng Cảnh báo](alerts-user-guide.md) | Bật tính năng, tạo/quản lý quy tắc, đọc lịch sử kích hoạt, xử lý sự cố |
| [Cơ chế hoạt động của Cảnh báo](alerts-mechanism.md) | Mô hình dữ liệu, loại rule, worker định kỳ, engine realtime MQTT, thông báo, API, Web |
| [API hồ sơ cổ phiếu](stock-profile-api.md) (tiếng Trung) | Hợp đồng stock-profile, trạng thái chất lượng từng khối, chuẩn hoá mã |
| [DecisionSignal - tín hiệu quyết định](decision-signals.md) (tiếng Trung) | Trường dữ liệu, API, hiển thị Web, liên kết cảnh báo/thông báo/rủi ro danh mục, đánh giá hậu kiểm, che dữ liệu nhạy cảm, rollback |
| [ResearchArtifact](research-artifact.md) (tiếng Trung) | Hợp đồng `structured_report`: Thesis / Evidence / Invalidation / Next Action / Data Quality |
| [Nguồn tin tức / tình báo](intelligence-sources.md) (tiếng Trung) | Cấu hình RSS/Atom, kiểm thử, kéo tin, khử trùng lặp, lưu trữ |
| [Gói ngữ cảnh phân tích](analysis-context-pack.md) (tiếng Trung) | AnalysisContextPack: phạm vi, trạng thái chất lượng trường, tiêu thụ trong prompt, hiển thị lịch sử/API/Web |
| [Prompt nhận diện ảnh](image-extract-prompt.md) (tiếng Trung) | Prompt và giới hạn khi nhận diện mã cổ phiếu từ ảnh |
| [Tích hợp OpenClaw Skill](openclaw-skill-integration.md) (tiếng Trung) | Tích hợp ngoài qua OpenClaw / Skill |
| [Tích hợp Grok Bot](grok-bot-integration.md) (tiếng Trung) | Grok Bot (AI teammate) với REST / DecisionSignal / Skill của DSA |

## Triển khai và đóng gói

| Tài liệu | Nội dung |
| --- | --- |
| [Hướng dẫn triển khai (EN)](DEPLOY_EN.md) | Server, Docker, systemd, Supervisor |
| [Triển khai WebUI trên cloud](deploy-webui-cloud.md) (tiếng Trung) | Truy cập WebUI trên server cloud |
| [Triển khai Zeabur](docker/zeabur-deployment.md) (tiếng Trung) | Triển khai trên nền tảng Zeabur |
| [Đóng gói desktop](desktop-package.md) (tiếng Trung) | Đóng gói Electron desktop và bản build Web |
| [Chạy trên Windows qua LAN](windows-lan-deploy.md) | Build trên Mac, chạy FastAPI + Web UI + lịch + cảnh báo realtime trên máy Windows, truy cập qua IP LAN |

## Tham khảo và phát triển

| Tài liệu | Nội dung |
| --- | --- |
| [Đặc tả API](architecture/api_spec.json) | Bản OpenAPI của FastAPI |
| [Workflow Agent](agent-workflow.md) | Sơ đồ pipeline phân tích mã, single/multi-agent, vòng ReAct và Chat hỏi đáp |
| [Hướng dẫn đóng góp](CONTRIBUTING.md) / [EN](CONTRIBUTING_EN.md) | Issue, PR, kiểm thử, đồng bộ tài liệu |

## Ngôn ngữ khác

| Tài liệu | Nội dung |
| --- | --- |
| [Mục lục tài liệu tiếng Anh](INDEX_EN.md) | English documentation index |
| [README tiếng Anh](README_EN.md) | English project overview and quick start |
