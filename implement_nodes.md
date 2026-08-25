# Hướng dẫn implement các node LangGraph

## 1. Hiểu đơn giản về node

Có thể xem graph như một quy trình gồm nhiều trạm:

```text
state → node xử lý → cập nhật một phần state → node tiếp theo
```

- **Node** là một hàm làm đúng một nhiệm vụ nhỏ.
- **State** là bộ nhớ chung được truyền giữa các node.
- **Routing** chọn node tiếp theo dựa trên state.
- **Event** là nhật ký giúp biết node nào đã chạy và kết quả ra sao.

Mỗi node chỉ đọc dữ liệu cần thiết và trả về **partial update**. Không sửa trực tiếp state đầu vào và không chứa logic của toàn bộ workflow.

## 2. Thứ tự nên triển khai

Nên làm theo thứ tự sau để phần sau chỉ phụ thuộc phần đã hoàn thành:

1. Chốt `AgentState` và reducer.
2. Implement `classify_node`.
3. Implement các nhánh không có loop: `clarify`, `risky_action`, `approval`.
4. Implement tool loop: `tool`, `evaluate`, `retry`, `dead_letter`.
5. Implement `answer_node` bằng LLM.
6. Implement `finalize_node` và kiểm tra audit trail.

Repo có tổng cộng 11 node. `intake_node` đã có sẵn làm ví dụ; 10 node còn lại cần implement.

## 3. Contract của từng node

Contract cho biết node được đọc gì, phải ghi gì và không được làm gì.

### `intake_node`

- Đọc `query` thô rồi gọi `.strip()`.
- Ghi lại `query`, thêm một `message` và một `event`.
- Đây là mẫu chuẩn về cách trả partial update.
- Không trả lại toàn bộ list cũ và không `.append()` trực tiếp vào state.

### `classify_node`

- Dùng `query` để chọn một trong năm route: `simple`, `tool`, `missing_info`, `risky`, `error`.
- Phải dùng LLM với structured output để kết quả được validate.
- Ghi `route`, `risk_level` và một event.
- Thứ tự ưu tiên: `risky > tool > missing_info > error > simple`.
- Không dùng `scenario_id`, không hard-code câu mẫu và không chỉ phân loại bằng keyword.

Luồng chính:

```text
query → prompt mô tả route/priority → LLM structured output
      → validate → route + risk_level + event
```

### `ask_clarification_node`

- Dùng khi query thiếu thông tin hoặc approval bị từ chối.
- Tạo một câu hỏi cụ thể để người dùng biết cần bổ sung gì.
- Ghi cùng câu hỏi vào `pending_question` và `final_answer`, sau đó thêm event.
- Không gọi tool và không hỏi lại quá chung chung.

### `risky_action_node`

- Mô tả hành động có side effect đang được đề xuất và lý do cần duyệt.
- Ghi `proposed_action` và event.
- Chỉ tạo đề xuất; tuyệt đối chưa thực hiện tool hoặc side effect tại đây.

### `approval_node`

- Đọc `proposed_action`.
- Ghi dictionary `approval` gồm `approved`, `reviewer`, `comment` và thêm event.
- Mặc định dùng mock `approved=True` để test/CI không chờ nhập từ bàn phím.
- Không gọi tool trong node này. `interrupt()` thật chỉ là phần mở rộng.

### `tool_node`

- Đọc `route`, `attempt`, `query`; với route rủi ro phải có approval trước.
- Thêm đúng một kết quả mới vào `tool_results` và một event.
- Theo starter: nếu `route == "error"` và `attempt < 2`, kết quả phải chứa `ERROR`; trường hợp khác trả mock success tổng quát.
- Không tăng `attempt` tại đây và không thay thế toàn bộ lịch sử tool.

### `evaluate_node`

- Chỉ đọc kết quả mới nhất: `tool_results[-1]`.
- Nếu kết quả có bằng chứng lỗi như `ERROR`, ghi `evaluation_result="needs_retry"`; ngược lại ghi `success`.
- Thêm event chứa verdict và lý do ngắn.
- Heuristic kiểm tra `ERROR` là đủ cho core; LLM-as-judge chỉ là extension.

### `retry_or_fallback_node`

- Đọc `attempt`, `max_attempts` và lỗi/tool result mới nhất.
- Tăng `attempt` đúng một lần.
- Thêm một phần tử mới vào `errors` và một event.
- Routing sẽ dùng giá trị attempt sau khi tăng để retry hoặc chuyển sang dead letter.
- Không reset attempt và không tăng attempt ở cả retry lẫn tool.

### `dead_letter_node`

- Dùng khi đã hết số lần retry.
- Đọc retry evidence rồi tạo `final_answer` thông báo không thể hoàn tất hoặc cần escalate.
- Thêm event dead-letter.
- Không quay lại retry và không đổi `route` ban đầu thành `dead_letter`.

### `answer_node`

- Dùng LLM để tạo câu trả lời từ context thật: `query`, tool results và approval/action nếu có.
- Ghi `final_answer` và event.
- Không hard-code câu trả lời hoặc dùng `scenario_id`.
- Không tuyên bố một hành động đã được thực hiện nếu approval bị từ chối.

Luồng chính:

```text
query + tool results + approval context → grounded prompt → LLM
                                       → final_answer + event
```

### `finalize_node`

- Xác nhận workflow đã có `final_answer` hoặc `pending_question`.
- Chỉ thêm một event:

```python
make_event("finalize", "completed", "workflow finished")
```

- Không đổi `route` thành `done` vì metrics cần giữ route do classify tạo ra.
- Mọi nhánh trong graph đều phải đi qua finalize trước khi kết thúc.

## 4. Quy tắc chung khi cập nhật state

Cách đúng:

```python
return {
    "tool_results": [new_result],
    "events": [make_event("tool", "completed", "tool finished")],
}
```

Cách sai:

```python
results = state["tool_results"]
results.append(new_result)
return {"tool_results": results}
```

Cách sai có thể mutate state và làm reducer nối lại toàn bộ list, gây nhân đôi dữ liệu.

## 5. Xử lý lỗi LLM

Không được âm thầm biến lỗi provider thành kết quả thành công. Cần chọn rõ một policy:

- Chuyển lỗi có kiểm soát sang error/retry; hoặc
- Dùng fallback nhưng phải ghi rõ fallback trong `errors` và `events`.

Event không được chứa API key, secret hoặc toàn bộ prompt nhạy cảm.

## 6. Checklist hoàn thành

- Cả 11 node có một nhiệm vụ rõ ràng.
- `classify_node` và `answer_node` dùng LLM thật.
- `classify_node` dùng structured output.
- Mỗi node chỉ trả partial update và ít nhất một event phù hợp.
- Không node nào mutate state đầu vào.
- Risky action chỉ chạy sau approval.
- Retry có giới hạn và cuối cùng đi dead letter khi hết lượt.
- Không overwrite route phân loại ban đầu.
- Mọi route đều đi qua `finalize_node`.
- Không hard-code sample scenario hoặc chứa full workflow trong một node.
