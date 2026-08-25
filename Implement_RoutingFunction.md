# Hướng dẫn implement Routing Function

## 1. Routing function là gì?

Routing function giống như một **ngã rẽ** trong workflow:

```text
state hiện tại → kiểm tra một giá trị → trả tên node tiếp theo
```

Routing function chỉ được:

- Đọc state.
- Trả về tên node dạng string.

Routing function không được gọi LLM, sửa state, chạy tool hoặc gây side effect. Tên trả về phải khớp chính xác với tên node đăng ký trong `graph.py`.

## 2. Bốn routing function cần implement

### `route_after_classify`

Đọc `state["route"]` và ánh xạ:

| Route | Node tiếp theo |
|---|---|
| `simple` | `answer` |
| `tool` | `tool` |
| `missing_info` | `clarify` |
| `risky` | `risky_action` |
| `error` | `retry` |
| Thiếu hoặc không hợp lệ | `answer` |

Nên dùng dictionary lookup và đặt `answer` làm giá trị mặc định.

### `route_after_evaluate`

Đọc `state["evaluation_result"]`:

- Nếu bằng `needs_retry` thì trả `retry`.
- Mọi giá trị khác, kể cả thiếu field, đều trả `answer`.

### `route_after_retry`

Đọc `attempt` và `max_attempts`:

- `attempt < max_attempts` → trả `tool`.
- `attempt >= max_attempts` → trả `dead_letter`.

Boundary rất quan trọng:

```text
attempt = 2, max = 3 → tool
attempt = 3, max = 3 → dead_letter
attempt = 4, max = 3 → dead_letter
```

Điều kiện này bảo đảm retry luôn có giới hạn và graph không lặp vô hạn.

### `route_after_approval`

Đọc approval dưới dạng mapping:

```python
{"approval": {"approved": True}}
```

- Chỉ khi `approved is True` mới trả `tool`.
- Nếu `False`, thiếu approval hoặc thiếu decision thì trả `clarify`.

Nguyên tắc này ngăn risky action chạy khi chưa được duyệt rõ ràng.

## 3. Quy tắc triển khai

- Không mutate state đầu vào.
- Không ghi event trong routing function; event thuộc trách nhiệm của node.
- Không dùng `scenario_id` để routing.
- Không tạo alias tên node tùy ý.
- Dùng đúng các tên public: `answer`, `tool`, `clarify`, `risky_action`, `retry`, `dead_letter`.
- Với dữ liệu thiếu hoặc không hợp lệ, chọn default an toàn theo contract.

## 4. Kiểm tra sau khi implement

Chạy:

```powershell
python -m pytest tests/test_routing.py -q
```

Kết quả mong đợi:

- Tất cả routing tests pass.
- Unknown route trở về `answer`.
- `needs_retry` đi tới `retry`.
- Retry boundary đúng ở cả `<`, `==` và `>` `max_attempts`.
- Approval chỉ đi tới `tool` khi `approved` thật sự là `True`.
