# Bounded retry và dead-letter

## 1. Nguyên tắc quan trọng

Retry loop chỉ kết thúc an toàn khi **chỉ `retry` node được tăng `attempt`**:

```text
retry node: attempt mới = attempt cũ + 1
routing: đọc attempt mới để chọn tool hoặc dead_letter
```

`tool` và các node khác không được tăng hoặc reset counter. Mỗi lần có retry event phải tương ứng đúng một lần counter tăng.

## 2. Điều kiện biên

| State sau retry node | Node tiếp theo | Ý nghĩa |
|---|---|---|
| `attempt < max_attempts` | `tool` | Vẫn còn lượt thử |
| `attempt == max_attempts` | `dead_letter` | Đã chạm giới hạn |
| `attempt > max_attempts` | `dead_letter` | Fail closed khi state bất thường |

Khi attempt đã bằng giới hạn, graph không được gọi tool thêm.

## 3. Luồng của error route

Error route bắt đầu ở `retry`, không đi thẳng tới `tool`:

```text
classify(error)
→ retry (tăng attempt)
→ tool hoặc dead_letter
→ evaluate nếu tool đã chạy
→ retry nếu result cần thử lại
→ answer hoặc dead_letter
→ finalize
→ END
```

Nhờ routing đọc counter sau khi retry cập nhật, loop luôn có giới hạn và không cần tăng recursion limit.

## 4. Trường hợp `S07_dead_letter`

Scenario này có:

```text
initial attempt = 0
max_attempts = 1
```

Trace đúng phải là:

```text
classify(error)
→ retry: attempt 0 → 1
→ vì 1 >= 1 nên dead_letter
→ finalize
→ END
```

`S07` không được gọi tool. Nếu tool vẫn chạy hoặc graph lặp vô hạn thì counter ownership hoặc conditional edge đang sai.

## 5. Các điểm cần kiểm tra

- Mỗi retry event tương ứng đúng một lần tăng `attempt`.
- `errors` được append bằng reducer, không replace lịch sử cũ.
- Dead-letter tạo `final_answer` giải thích không thể hoàn tất và đã escalate.
- Sau `dead_letter` chỉ đi tới `finalize`, sau đó `END`.
- Error route thông thường và `S07` đều kết thúc hữu hạn.
- Không cần nâng recursion limit để che một retry loop sai.

Chạy checkpoint nhỏ:

```powershell
python -m pytest tests/test_routing.py -k retry -q
```

Kết quả mong đợi: retry boundary pass ở cả `<`, `==`, `>`; riêng `S07` đi thẳng tới dead-letter ngay khi attempt chạm max.

## 6. Kết quả kiểm tra trong project

Đã bổ sung regression test trong `tests/test_graph_build.py`:

- Error route mặc định kết thúc với `attempt=2`; hai retry event lần lượt ghi attempt 1 và 2.
- Số phần tử mới trong `errors` bằng số retry event.
- `S07_dead_letter` có event trail chính xác:

```text
intake → classify → retry → dead_letter → finalize
```

- `S07` kết thúc tại `attempt == max_attempts == 1` và `tool_results` vẫn rỗng.
- Sáu checkpoint test tập trung vào retry/dead-letter đã pass.
