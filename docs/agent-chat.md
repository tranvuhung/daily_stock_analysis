# Hỏi đáp (Agent Chat) — cách hoạt động

Tài liệu mô tả luồng của trang **Hỏi đáp** (`/chat` trên Web UI): người dùng đặt câu hỏi về cổ phiếu, Agent gọi các công cụ dữ liệu rồi LLM tổng hợp thành báo cáo quyết định.

## 1. Tổng quan luồng

```
ChatPage.tsx ──► agentChatStore.startStream ──► POST /api/v1/agent/chat/stream (SSE)
                                                   │
                         AgentChatSessionService.prepare_turn (lưu tin nhắn user)
                                                   │
                         AgentChatExecutor ─► LiteLLMAgentBackend.run ─► run_agent_loop
                                                   │                        │
                                                   │          LLMToolAdapter.call_with_tools
                                                   │          (primary + LITELLM_FALLBACK_MODELS)
                                                   │                        │
                                                   │             ToolRegistry (quote, history, news…)
                                                   ▼
                         Lưu câu trả lời + provider trace ─► SSE `done` ─► hiển thị
```

| Lớp | File chính |
| --- | --- |
| Web page / store | `apps/dsa-web/src/pages/ChatPage.tsx`, `apps/dsa-web/src/stores/agentChatStore.ts`, `apps/dsa-web/src/api/agent.ts` |
| API | `api/v1/endpoints/agent.py` |
| Session | `src/services/agent_chat_session_service.py`, bảng `conversation_messages`, `conversation_session_states`, `conversation_summaries`, `agent_provider_turns` (`src/storage.py`) |
| Prompt + chiến lược | `src/agent/executor.py` (`prepare_agent_chat`), `src/agent/factory.py`, `strategies/*.yaml` |
| Vòng lặp Agent | `src/agent/runner.py` (`run_agent_loop`) |
| Gọi LLM + fallback | `src/agent/llm_adapter.py` (`call_completion`, `_convert_messages`) |
| Lịch sử + nén ngữ cảnh | `src/agent/chat_context.py`, `src/agent/provider_trace.py` |
| Công cụ | `src/agent/tools/*.py` (đăng ký tại `factory._build_tool_registry`) |

## 2. API

| Route | Mô tả |
| --- | --- |
| `POST /api/v1/agent/chat/stream` | Luồng SSE chính. Sự kiện: `accepted` → `thinking` / `tool_start` / `tool_done` / `stage_*` / `generating` → `done` hoặc `error`. Hợp đồng chi tiết: [agent-stream-events.md](agent-stream-events.md). |
| `POST /api/v1/agent/chat` | Bản đồng bộ (không stream). |
| `POST /api/v1/agent/chat/stream/{request_id}/cancel` | Dừng lượt đang chạy (chỉ backend Codex). |
| `GET/DELETE /api/v1/agent/chat/sessions[/{id}]` | Danh sách / chi tiết / xoá phiên ("Hội thoại trước"). |
| `POST /api/v1/agent/chat/send` | Nút **Gửi**: đẩy nội dung phiên (Markdown) tới các kênh thông báo đã cấu hình. |
| `GET /api/v1/agent/skills` | Danh sách chiến lược hiển thị dưới ô nhập. |

Nút **Xuất phiên** chạy hoàn toàn ở trình duyệt (`apps/dsa-web/src/utils/chatExport.ts`).

Nhãn tiến trình ("Đang lập kế hoạch phân tích...", "Đã xong «Lấy giá thời gian thực»…") được Web dựng theo `type` / `tool` / `stage` của sự kiện SSE và ngôn ngữ UI; trường `message` của backend (cố định tiếng Trung/Anh) chỉ dùng cho loại sự kiện Web chưa biết. Tên công cụ và giai đoạn nằm trong `locales/chatText.ts`.

## 3. Ngôn ngữ trả lời

- API gắn `report_language` (mặc định `REPORT_LANGUAGE=vi`) vào context của lượt chat; `prepare_agent_chat` thêm mục **Output Language** vào system prompt, yêu cầu trả lời tiếng Việt có dấu và dịch cả nội dung lấy từ prompt/chiến lược/kết quả công cụ đang là tiếng Trung.
- System prompt chat (`CHAT_SYSTEM_PROMPT`, `LEGACY_DEFAULT_CHAT_SYSTEM_PROMPT`, `CODEX_CHAT_SYSTEM_PROMPT` trong `src/agent/executor.py`), ngữ cảnh phân tích trước đó chèn vào lịch sử và hướng dẫn chiến lược đều là **tiếng Anh**; với `report_language=vi`, prompt chat đầy đủ không còn chữ Hán (có test `tests/test_chat_prompt_english.py`). Prompt báo cáo phân tích (`AGENT_SYSTEM_PROMPT`) vẫn tiếng Trung, nằm ngoài phạm vi Hỏi đáp.
- Nhãn phân tích kỹ thuật trả cho LLM dùng **tiếng Anh làm chuẩn**: enum và câu lý do của `src/stock_analyzer.py` (`analyze_trend`: "Bullish Alignment", "Low Volume Pullback"…) và các tool `calculate_ma` / `get_volume_analysis` / `analyze_pattern` (`src/agent/tools/analysis_tools.py`). Báo cáo bản địa hoá các nhãn này qua `src/report_language.py` (vẫn giữ khoá tiếng Trung cho bản ghi cũ).
- Nguồn chữ Hán còn lại: dữ liệu thật (tên công ty, tin tức, ngữ cảnh lưu từ báo cáo cũ) và tên người dùng tự đặt; bộ lọc đầu ra bên dưới vẫn giữ làm lưới an toàn. Vì vậy câu trả lời cuối luôn đi qua **bộ lọc đầu ra** `src/agent/output_language_guard.py` khi `report_language=vi` (áp dụng cho single-agent, multi-agent, Codex và đường chat cũ):
  1. Không có chữ Hán → giữ nguyên.
  2. Có chữ Hán → gọi LLM một lần (cùng tuyến model/fallback) để dịch các đoạn tiếng Trung sang tiếng Việt; bản dịch ngắn hơn 60% bản gốc hoặc lỗi thì bỏ qua.
  3. Luôn chạy bước làm sạch tất định: bỏ chú thích tiếng Trung trong ngoặc, dịch thuật ngữ theo bảng `VI_GLOSSARY`, xoá chữ Hán còn sót, chuẩn hoá dấu câu toàn khổ. Kết quả lưu vào lịch sử và trả về Web **không còn chữ Hán**.
- Khi bộ lọc kích hoạt, log ghi `Vietnamese output guard: removed N Han char(s) (llm_rewrite=…)`. Lượt dịch thêm làm câu trả lời chậm hơn vài giây đến vài chục giây.

- **Câu miễn trừ trách nhiệm:** mọi câu trả lời thành công đều được backend gắn thêm ở cuối dòng `---` + `*Nội dung do AI tạo, chỉ mang tính tham khảo, không phải khuyến nghị đầu tư*` (nhãn `not_investment_advice` dùng chung với báo cáo, theo `REPORT_LANGUAGE`). Việc gắn chạy sau bộ lọc chữ Hán (`finalize_chat_answer` trong `src/agent/executor.py`), không phụ thuộc model, không gắn trùng; prompt dặn model không tự viết câu này. Ghi chú thất bại / đã dừng không kèm câu miễn trừ.

## 4. Chiến lược (skills)

- Mỗi chiến lược là một file `strategies/*.yaml`. `description` và `instructions` của 15 chiến lược dựng sẵn viết bằng **tiếng Anh**, được chèn vào system prompt dưới mục `## Active trading skills` với tiêu đề `### Skill N: <display_name_en>`. `display_name` và `aliases` giữ tiếng Trung vì Web, bot và báo cáo dùng làm khoá đối chiếu; `display_name_en` (tuỳ chọn, mặc định = `display_name`) chỉ dùng trong prompt.
- Chọn tối đa 3 chiến lược / lượt; bỏ chọn tất cả = "Phân tích tổng quát". Lựa chọn được lưu theo phiên (`conversation_session_states`).
- `display_name` trong YAML là tiếng Trung. Web dịch tên/mô tả theo ngôn ngữ UI bằng bảng `apps/dsa-web/src/locales/chatText.ts` (khoá theo id), **chỉ khi** tên backend còn trùng `display_name` gốc — chiến lược tự đổi tên hoặc skill tuỳ biến giữ nguyên tên. Thêm chiến lược mới thì bổ sung vào bảng này.

## 5. Phạm vi mã cổ phiếu (stock scope)

Mỗi lượt hỏi được gắn một phạm vi mã; công cụ dữ liệu chỉ được gọi cho các mã trong phạm vi (`src/agent/stock_scope.py`, Web mirror ở `ChatPage.tsx` + `utils/chatStockCode.ts`).

| Chế độ | Khi nào | Mã được phép |
| --- | --- | --- |
| `maintain` | Câu hỏi tiếp theo về mã hiện tại, hoặc không có ý định chuyển rõ ràng | Chỉ mã hiện tại |
| `switch` | Có từ khoá chuyển mã **và** đúng một mã mới, ví dụ "Phân tích mã LAS", "Xem LAS thế nào", "LAS có nên mua không?", "Analyze FPT", "分析 LAS" | Chỉ mã mới |
| `compare` | Từ khoá so sánh / lựa chọn, ví dụ "So sánh HAG và LAS", "HAG với LAS mã nào tốt hơn" | Mã hiện tại + các mã được nhắc |

- Từ khoá tiếng Việt / tiếng Anh (`_VI_EN_*`) chỉ dùng để quyết định switch/compare. Mã viết thường (`phân tích las`) **không** được nhận diện — hãy viết hoa mã (LAS), vì trích mã chữ thường chỉ bật với từ khoá tiếng Trung để tránh nhận nhầm từ tiếng Việt không dấu thành mã.
- Khi AI trả lời "phiên đang giới hạn cho mã X", nghĩa là lượt đó ở chế độ `maintain`; viết lại câu hỏi có từ khoá chuyển mã và mã viết hoa, hoặc bắt đầu hội thoại mới.

## 6. Lịch sử hội thoại và nén ngữ cảnh

- `conversation_messages` chỉ lưu văn bản user/assistant. Các lượt gọi tool cần round-trip (ví dụ Gemini thought signature, reasoning của DeepSeek) được lưu riêng trong `agent_provider_turns` và chỉ phát lại khi model đích khớp model đã tạo ra trace.
- **Nén ngữ cảnh** (checkbox trên trang, cấu hình `AGENT_CONTEXT_COMPRESSION_ENABLED`, `_PROFILE`, `_TRIGGER_TOKENS`, `AGENT_CONTEXT_PROTECTED_TURNS`): khi lịch sử vượt ngưỡng token, các tin nhắn cũ được LLM tóm tắt; N lượt gần nhất giữ nguyên.

## 7. Fallback model và provider trace

- `call_completion` thử lần lượt model chính rồi `LITELLM_FALLBACK_MODELS`. Hết model thì trả `All LLM models failed. Last error: …`, lượt được lưu thành tin nhắn ghi chú thất bại theo `REPORT_LANGUAGE` (`vi`: `[Phân tích thất bại] …`, `[Đã dừng] …`, `[Quá thời gian] …`; xem `build_chat_failure_note` trong `src/agent/executor.py`).
- Mỗi message assistant có `tool_calls` và các message `tool` trả lời nó đều mang `_trace_provider` / `_trace_model` của model đã gọi tool.
- Khi gửi cho một model khác (fallback), `_convert_messages` bỏ **trọn cụm** assistant `tool_calls` + tool results của model cũ (vì chữ ký/trường riêng của provider không dùng chéo được). Model fallback sẽ tự gọi lại công cụ nếu cần.
- Lưới an toàn: message `tool` có `tool_call_id` không thuộc assistant `tool_calls` nào đã giữ lại sẽ bị bỏ, để không gửi tool result "mồ côi".

## 8. Xử lý sự cố

**`Missing corresponding tool call for tool response message … last_message_with_tool_calls=None`**

- Triệu chứng: Hỏi đáp trả `[分析失败] All LLM models failed. Last error: litellm.APIConnectionError: Missing corresponding tool call …` khi dùng Gemini.
- Nguyên nhân (đã sửa): model chính gọi tool xong rồi lỗi ở bước sau (429/503/timeout…), fallback sang model khác; trước đây message assistant `tool_calls` bị lọc nhưng tool result vẫn được gửi, Gemini từ chối.
- Nếu còn gặp: kiểm tra log `Agent LLM call failed with …` để biết vì sao model chính lỗi (quota, timeout), xem lại `LITELLM_MODEL` / `LITELLM_FALLBACK_MODELS`.

**Lượt lỗi cũ vẫn hiện trong phiên**: ghi chú tiếng Trung lưu trước đây (`[分析失败] …`, `[已停止] …`, `[已超时] …`) vẫn nằm trong `conversation_messages`; Web chỉ dịch khi hiển thị (`localizeLegacyChatNote`). Bắt đầu hội thoại mới hoặc xoá phiên nếu không cần.

## 9. Cấu hình liên quan (`.env.example`)

- Model: `LITELLM_MODEL`, `LITELLM_FALLBACK_MODELS`, `AGENT_LITELLM_MODEL`, `LLM_CHANNELS`.
- Agent: `AGENT_MODE`, `AGENT_BACKEND`, `AGENT_ARCH`, `AGENT_MAX_STEPS`, `AGENT_SKILLS`, `AGENT_SKILL_DIR`, `AGENT_ORCHESTRATOR_TIMEOUT_S` (cũng là giới hạn thời gian cho chat single-agent), `AGENT_*_TOOL_TIMEOUT_S`.
- Nén ngữ cảnh: `AGENT_CONTEXT_COMPRESSION_*`, `AGENT_CONTEXT_PROTECTED_TURNS`.
