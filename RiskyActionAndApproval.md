# Kiểm tra risky action và approval

## 1. Approval là cổng an toàn

Approval phải xảy ra **trước** khi tool tạo side effect:

```text
risky request → chuẩn bị action → review → mới được phép chạy tool
```

Approval không phải event được ghi sau khi tool đã chạy. Nếu tool xuất hiện trước approval thì workflow không an toàn.

## 2. Hai trace bắt buộc

| Decision | Trace đúng | Điều không được xảy ra |
|---|---|---|
| Approved | `risky_action → approval → tool → evaluate → ... → finalize` | Tool chạy trước approval |
| Rejected | `risky_action → approval → clarify → finalize` | Tool xuất hiện sau rejection |

Trong cả hai trường hợp, `finalize` phải là event cuối cùng.

## 3. Trách nhiệm của từng node

### `risky_action_node`

- Chỉ tạo `proposed_action` có liên hệ với query.
- Ghi event cho biết action đang chờ review.
- Không gọi tool và không tạo side effect.

### `approval_node`

- Đọc `proposed_action`.
- Trả approval mapping có đủ:

```python
{
    "approved": True,
    "reviewer": "mock-reviewer",
    "comment": "...",
}
```

- Ghi approval event rõ ràng.
- Không gọi tool trong node này.

### Routing sau approval

- Chỉ `approved is True` mới đi tới `tool`.
- Rejected, thiếu approval hoặc thiếu decision đều đi tới `clarify`.

## 4. Mock approval và real HITL

Core workflow dùng mock `approved=True` mặc định để test và CI không bị treo vì chờ input.

Mock approval chỉ chứng minh routing/core flow hoạt động. Nó không phải bằng chứng cho real human-in-the-loop, interrupt hoặc resume. Real `interrupt()` nên được tách thành extension riêng.

## 5. Các checkpoint cần kiểm tra

Chạy routing test:

```powershell
python -m pytest tests/test_routing.py -k approval -q
```

Khi kiểm tra event trail:

- Approved: vị trí event `approval` phải đứng trước `tool`.
- Rejected: phải có `approval` và `clarify`, tuyệt đối không có `tool`.
- Cả hai nhánh: event cuối cùng phải là `finalize`.
- Test dùng state/query tổng quát, không dùng `scenario_id` để quyết định.

## 6. Kết quả mong đợi

- Risky action chỉ được chuẩn bị trước review.
- Tool không thể chạy khi chưa được duyệt.
- Approved mới đi tới tool và evaluate.
- Rejected đi tới clarification, không chạm tool.
- Cả approved và rejected đều kết thúc hữu hạn tại finalize.

## 7. Kết quả kiểm tra trong project

Đã bổ sung regression probe trong `tests/test_graph_build.py`:

- Approved trace:

```text
intake → classify → risky_action → approval → tool → evaluate → answer → finalize
```

- Rejected trace:

```text
intake → classify → risky_action → approval → clarify → finalize
```

- Approved probe xác nhận `index(approval) < index(tool)`.
- Rejected probe xác nhận không có `tool` trong toàn bộ event trail.
- Cả hai probe xác nhận `finalize` là terminal event.
- Routing approval, node guard và graph event-order tests đều pass.
