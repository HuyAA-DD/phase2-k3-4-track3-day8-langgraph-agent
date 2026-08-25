# Thiết kế `AgentState` trong LangGraph

## 1. Hiểu đơn giản: `AgentState` là gì?

`AgentState` là **bộ nhớ chung của một lần chạy graph**. Mỗi node đọc state hiện tại, xử lý công việc của mình rồi chỉ trả về những field mà nó muốn cập nhật.

```text
state hiện tại → node xử lý → partial update → LangGraph merge vào state
```

Vì `AgentState` dùng `TypedDict(total=False)`, node không phải trả lại toàn bộ state. Ví dụ node phân loại chỉ cần trả:

```python
return {
    "route": "simple",
    "risk_level": "low",
    "events": [make_event("classify", "completed", "classified as simple")],
}
```

## 2. Reducer dùng để làm gì?

Reducer quyết định cách LangGraph ghép giá trị mới với giá trị đang có.

- **Append**: dùng `Annotated[list[...], add]` để nối dữ liệu mới vào list cũ.
- **Overwrite**: không khai báo reducer; giá trị mới thay thế giá trị cũ.

Chỉ bốn field lịch sử cần reducer `add`:

| Field | Nội dung |
|---|---|
| `messages` | Dấu vết hội thoại/xử lý |
| `tool_results` | Các kết quả tool theo thời gian |
| `errors` | Các lỗi theo thời gian |
| `events` | Audit event của các node |

Các field biểu diễn **giá trị hiện tại** như `route`, `attempt`, `approval`, `evaluation_result` và `final_answer` phải overwrite.

## 3. Bốn field cần bổ sung

| Field | Dùng ở đâu | Cách cập nhật |
|---|---|---|
| `evaluation_result` | Quyết định trả lời hay retry | Overwrite bằng `success` hoặc `needs_retry` |
| `pending_question` | Câu hỏi khi thiếu thông tin | Overwrite câu hỏi hiện tại |
| `proposed_action` | Nội dung hành động cần duyệt | Overwrite hành động đang chờ |
| `approval` | Routing, answer và metrics | Overwrite bằng dictionary thuần, có thể serialize |

Schema dự kiến:

```python
class ApprovalState(TypedDict):
    approved: bool
    reviewer: str
    comment: str


class AgentState(TypedDict, total=False):
    thread_id: str
    scenario_id: str
    query: str
    route: str
    risk_level: str
    attempt: int
    max_attempts: int
    final_answer: str | None

    evaluation_result: Literal["success", "needs_retry"] | None
    pending_question: str | None
    proposed_action: str | None
    approval: ApprovalState | None

    messages: Annotated[list[str], add]
    tool_results: Annotated[list[str], add]
    errors: Annotated[list[str], add]
    events: Annotated[list[dict[str, Any]], add]
```

`approval` nên có dạng dữ liệu đơn giản như:

```python
{
    "approved": True,
    "reviewer": "mock-reviewer",
    "comment": "Approved for execution",
}
```

Nếu tạo bằng model `ApprovalDecision`, hãy gọi `.model_dump()` trước khi đưa vào state để checkpoint có thể serialize ổn định.

## 4. Không mutate state đầu vào

Cách sai:

```python
events = state["events"]
events.append(make_event("tool", "completed", "tool finished"))
return {"events": events}
```

Đoạn trên vừa sửa trực tiếp state đầu vào, vừa trả lại cả list cũ. Khi reducer `add` chạy, event cũ có thể bị nhân đôi.

Cách đúng:

```python
event = make_event("tool", "completed", "tool finished")
return {
    "tool_results": [new_result],
    "events": [event],
}
```

Node chỉ trả phần dữ liệu **mới phát sinh**; LangGraph sẽ nối nó vào list hiện tại.

## 5. Giữ nguyên `route` ban đầu

`route` là kết quả phân loại ban đầu và được metrics dùng để so sánh với `expected_route`. Vì vậy:

- Không đổi `route` thành `done` trong `finalize_node`.
- Không đổi `route` thành `dead_letter` khi workflow đi qua dead-letter node.
- Ghi nhận việc hoàn tất bằng event của `finalize_node`.
- Ghi nhận dead letter bằng event/error/final answer tương ứng.

Nếu overwrite `route`, graph có thể chạy đúng nhưng metrics vẫn báo sai route.

## 6. Checklist triển khai

- Thêm đủ bốn field: `evaluation_result`, `pending_question`, `proposed_action`, `approval`.
- Chỉ `messages`, `tool_results`, `errors`, `events` dùng reducer `add`.
- Mỗi node trả partial update thay vì trả toàn bộ state.
- Không dùng `.append()` trực tiếp trên list lấy từ state.
- Mỗi node trả `events: [make_event(...)]` với cùng một schema.
- Giữ `route` ổn định từ sau bước classify đến hết workflow.
- Chỉ lưu string, number, boolean, list, dictionary hoặc dữ liệu đã chuyển thành dạng serializable.
