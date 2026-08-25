# Các thay đổi đã triển khai

## 1. Nạp cấu hình từ `.env`

### File đã sửa

- `pyproject.toml`
- `src/langgraph_agent_lab/llm.py`
- `README.md`
- `tests/test_llm.py`

### Thay đổi

- Thêm dependency `python-dotenv>=1.0`.
- Gọi `load_dotenv()` một lần khi module LLM factory được import.
- Giữ ưu tiên cho biến đã có trong process vì không dùng `override=True`.
- Thêm `has_llm_api_key()` để kiểm tra một trong ba key được hỗ trợ:
  `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`.

### Ý nghĩa

`os.getenv()` không tự đọc file `.env`. Sau thay đổi này, key trong `.env` được đưa vào environment của Python trước khi `get_llm()` chọn provider.

## 2. Sửa smoke test bị skip dù đã có key

### File đã sửa

- `tests/test_graph_smoke.py`

### Thay đổi

Điều kiện `skipif` chuyển từ việc gọi trực tiếp `os.getenv()` sang:

```python
pytest.mark.skipif(
    not has_llm_api_key(),
    reason="No LLM API key configured",
)
```

Import `has_llm_api_key()` làm module `llm.py` nạp `.env` trước khi pytest đánh giá marker. Vì vậy `OPENAI_API_KEY` trong `.env` được nhận diện đúng và sáu smoke test không còn bị skip.

Smoke test hiện đã đi tới bước chạy graph. Lỗi tiếp theo là `build_graph()` vẫn còn `NotImplementedError` của starter, không còn là lỗi cấu hình secret.

## 3. Hoàn thiện thiết kế `AgentState`

### File đã sửa

- `src/langgraph_agent_lab/state.py`
- `tests/test_state.py`
- `design_agentstate.md`

### Các field được bổ sung

| Field | Mục đích | Cách merge |
|---|---|---|
| `evaluation_result` | Cho routing quyết định answer hay retry | Overwrite |
| `pending_question` | Lưu câu hỏi clarification hiện tại | Overwrite |
| `proposed_action` | Lưu hành động đang chờ phê duyệt | Overwrite |
| `approval` | Cho approval routing, answer và metrics | Overwrite |

`evaluation_result` được giới hạn bằng type:

```python
Literal["success", "needs_retry"] | None
```

`approval` dùng `ApprovalState`, một `TypedDict` gồm:

```python
class ApprovalState(TypedDict):
    approved: bool
    reviewer: str
    comment: str
```

Cấu trúc này là dictionary thuần nên dễ lưu checkpoint và chuyển sang JSON.

### Reducer

Chỉ bốn list lịch sử dùng reducer `add`:

```python
messages: Annotated[list[str], add]
tool_results: Annotated[list[str], add]
errors: Annotated[list[str], add]
events: Annotated[list[dict[str, Any]], add]
```

Các field scalar không có reducer nên update mới sẽ overwrite giá trị cũ. Mỗi node cần trả list chỉ chứa dữ liệu mới, ví dụ `events: [make_event(...)]`, thay vì mutate list lấy từ state.

### Khởi tạo state

`initial_state()` hiện khởi tạo bốn field mới bằng `None`. Nhờ vậy routing, metrics và node có thể đọc chúng an toàn trước khi có node cập nhật.

Không được đổi `route` thành `done` hoặc `dead_letter` ở cuối flow vì metrics dùng `route` làm kết quả phân loại thực tế.

## 4. Implement `classify_node`

### File đã sửa

- `src/langgraph_agent_lab/nodes.py`
- `tests/test_nodes.py`

### Thay đổi

- Định nghĩa schema `_ClassificationDecision` bằng Pydantic và `Literal`, chỉ chấp nhận năm route: `simple`, `tool`, `missing_info`, `risky`, `error`.
- Gọi `get_llm().with_structured_output(...)` làm đường phân loại chính.
- Prompt mô tả rõ ý nghĩa route và priority `risky > tool > missing_info > error > simple`.
- Prompt chỉ dùng `query`, không dùng `scenario_id` hoặc hard-code sample.
- `risk_level="high"` cho risky và `low` cho các route còn lại.
- Event thành công ghi route, risk và trạng thái structured validation nhưng không ghi prompt hoặc secret.
- Nếu provider hoặc validation lỗi, node chuyển có kiểm soát sang route `error`, đồng thời append `errors` và failed event. Nội dung exception không được đưa vào state để tránh lộ dữ liệu nhạy cảm.

### Kiểm thử

- Kiểm tra đủ năm route hợp lệ và risk tương ứng.
- Kiểm tra `scenario_id` không xuất hiện trong prompt.
- Kiểm tra node không mutate state đầu vào.
- Kiểm tra lỗi provider và route ngoài schema đều tạo fallback có audit.
- Test sử dụng fake LLM nên không tiêu tốn API key.

## 5. Implement các nhánh không loop

### `ask_clarification_node`

- Với query thiếu thông tin, tạo câu hỏi yêu cầu đối tượng bị ảnh hưởng, kết quả mong đợi và identifier liên quan.
- Với approval bị từ chối, câu hỏi nêu lý do từ chối và cho phép người dùng sửa hoặc hủy yêu cầu.
- Ghi cùng câu hỏi vào `pending_question` và `final_answer`, rồi append một event có lý do clarification.

### `risky_action_node`

- Chuyển query thành mô tả `proposed_action` có liên hệ trực tiếp với yêu cầu.
- Nêu rõ hành động có side effect và phải được duyệt trước khi thực thi.
- Chỉ ghi đề xuất và event; không gọi tool và không tạo `tool_results` mới.

### `approval_node`

- Khi có `proposed_action`, mặc định tạo mock decision `approved=True` để test/CI không bị interactive block.
- Decision là dictionary thuần gồm `approved`, `reviewer`, `comment`.
- Khi thiếu `proposed_action`, từ chối có kiểm soát, append error và failed event thay vì approve một hành động rỗng.
- Node chỉ ghi quyết định; không thực thi tool.

### Kiểm thử

- Kiểm tra clarification cho cả missing info và rejected approval.
- Kiểm tra risky action không thay đổi tool history.
- Kiểm tra mock approval đúng shape và serialize được.
- Kiểm tra input state không bị mutate.

## 6. Implement chuỗi tool loop

### `tool_node`

- Đọc `route`, `attempt`, `query` và approval context khi route là risky.
- Với `route="error"` và `attempt < 2`, tạo đúng một result chứa `ERROR` để mô phỏng transient failure.
- Các trường hợp hợp lệ khác tạo một mock `SUCCESS` result tổng quát.
- Risky action chỉ được mock execute khi có cả `proposed_action` và approval hợp lệ; nếu không, tool bị block và có failed event.
- Node không tăng `attempt`; nó chỉ append result/event mới.

### `evaluate_node`

- Chỉ đánh giá phần tử cuối của `tool_results`.
- Result chứa `ERROR` tạo verdict `needs_retry`; result khác tạo `success`.
- Khi chưa có result, trả `needs_retry` cùng error và failed event để failure có thể audit.
- `evaluation_result` là scalar nên được overwrite.

### `retry_or_fallback_node`

- Tăng `attempt` đúng một lần bằng `previous_attempt + 1`.
- Append đúng một error mô tả retry evidence và một event chứa attempt mới/retry bound.
- Không mutate error history và không tự quyết định có retry tiếp hay không; routing sẽ làm việc đó.

### `dead_letter_node`

- Tạo `final_answer` thông báo không thể hoàn tất và đã escalate để review thủ công.
- Event ghi attempt, retry bound, số error và việc có tool evidence hay không.
- Không overwrite classified `route` và không quay lại tool/retry.

### Kiểm thử

- Kiểm tra transient failure ở attempt 0/1 và success từ attempt 2.
- Kiểm tra risky tool bị chặn khi chưa duyệt và chạy khi đã duyệt.
- Kiểm tra evaluate chỉ đọc latest result.
- Kiểm tra retry chỉ tăng attempt một lần và trả append-only updates.
- Kiểm tra dead letter tạo final answer nhưng giữ nguyên route.

## 7. Implement `answer_node` bằng LLM

### Context và prompt

- Dùng `SystemMessage` để đặt quy tắc grounding và `HumanMessage` để truyền JSON context.
- Context gồm `query`, toàn bộ `tool_results`, `proposed_action` và `approval`.
- Không đưa `scenario_id` vào prompt.
- Tool result là source of truth cho lookup/action; model không được tự bịa việc tool đã làm.
- Khi approval bị từ chối, model phải nói rõ action chưa được thực hiện.

### Output và audit

- Gọi `get_llm().invoke(messages)` làm đường chính và ghi text trả về vào `final_answer`.
- Success event ghi provider class, số tool result và việc có approval context hay không; không ghi prompt hoặc secret.
- Nếu provider lỗi hoặc trả nội dung rỗng, node trả một fallback trung thực, append error và failed event có `fallback_used=True`.
- Exception message không được ghi vào state để tránh lộ dữ liệu nhạy cảm.

### Kiểm thử

- Kiểm tra query, tool result và approval/action xuất hiện trong context.
- Kiểm tra `scenario_id` không xuất hiện trong prompt.
- Kiểm tra system prompt cấm tuyên bố rejected action đã được thực hiện.
- Kiểm tra provider exception và empty response đều tạo audited fallback.
- Test dùng fake LLM nên không tiêu tốn API key.

## 8. Implement `finalize_node` và kiểm tra audit trail

### `finalize_node`

- Đọc `final_answer` và `pending_question` để xác nhận workflow có user-facing output.
- Khi có output, chỉ append đúng một event `make_event("finalize", "completed", "workflow finished")`.
- Không overwrite `route`, `final_answer` hoặc các list audit cũ.
- Nếu thiếu cả answer lẫn pending question, append error và failed finalize event thay vì kết thúc âm thầm.

### Audit trail

- Test một path không loop theo thứ tự `intake → classify → clarify → finalize`.
- Xác nhận event xuất hiện đúng thứ tự node.
- Xác nhận mọi event có cùng schema: `node`, `event_type`, `message`, `latency_ms`, `metadata`.
- Xác nhận finalize chỉ trả một event mới và không mutate state đầu vào.
- Kiểm tra này bắt đầu ở cấp node path; mọi route end-to-end được kiểm tra sau khi graph wiring hoàn tất.

## 9. Implement bốn routing function

- `route_after_classify` dùng lookup table và default về `answer` cho route thiếu/không hợp lệ.
- `route_after_evaluate` chỉ đi `retry` khi verdict chính xác là `needs_retry`; trường hợp khác đi `answer`.
- `route_after_retry` đi `tool` khi `attempt < max_attempts`, ngược lại đi `dead_letter`.
- `route_after_approval` chỉ đi `tool` khi mapping approval có `approved is True`; thiếu hoặc rejected đều đi `clarify`.
- Các hàm chỉ đọc state và trả tên node; không gọi LLM, không ghi event và không gây side effect.

## 10. Build và compile StateGraph

- `build_graph()` tạo `StateGraph(AgentState)` và đăng ký đủ 11 node bằng đúng tên public.
- Nối 8 fixed edge từ `START → intake` đến `finalize → END`.
- Nối 4 conditional edge sau `classify`, `evaluate`, `retry` và `approval` bằng routing function tương ứng.
- Path map dùng đúng destination mà routing functions trả về.
- Compile bằng chính `checkpointer` nhận từ caller; không tạo backend mới trong graph builder.
- Type import của LangGraph được đặt sau `TYPE_CHECKING` hoặc bên trong builder để module tiếp tục import-safe.

### Kiểm thử graph

- Offline graph test dùng fake LLM để chạy đủ `simple`, `tool`, `missing_info`, `risky`, `error` mà không dùng quota.
- Kiểm tra đủ node registry và checkpointer object được truyền nguyên vẹn.
- Kiểm tra retry loop, approval path và mọi route đều có `finalize` event.
- Smoke test dùng OpenAI thật được chạy một lần ngoài network sandbox và pass toàn bộ 6 test.

## 11. Scenario runner và report

- Lần chạy đầu hoàn tất scenarios và ghi metrics nhưng dừng ở `report.py` vì renderer còn `NotImplementedError` của starter.
- Đã implement Markdown renderer gồm metrics summary, scenario table, architecture/state, failure analysis, persistence evidence và improvement plan.
- Renderer escape ký tự trong Markdown table và không ghi prompt/secret.
- `make run-scenarios` sau khi sửa đã hoàn tất, ghi cả `outputs/metrics.json` và `reports/lab_report.md`.
- `make grade-local` xác nhận metrics schema hợp lệ với success rate 100%.

## 12. Kết quả kiểm tra

- `tests/test_llm.py`: 2 test pass.
- `tests/test_state.py`: 4 test pass.
- `tests/test_routing.py`: 13 test pass.
- `tests/test_graph_build.py`: 12 test pass, gồm retry, approval và MemorySaver history.
- `tests/test_graph_smoke.py`: 6 test pass với OpenAI thật.
- Report/metrics tests: 4 test pass.
- Các test node/config/state liên quan: 36 test pass.
- Ruff cho các file đã sửa: pass.
- Mypy cho `state.py`, `nodes.py`, `routing.py` và `graph.py`: pass.
- `.env` vẫn được `.gitignore` loại trừ và không được commit.
