# Checkpointer và Thread ID

## 1. Luồng cấu hình hiện tại

Persistence đang được cấu hình theo luồng:

```text
configs/lab.yaml
→ CLI đọc loại checkpointer
→ build_checkpointer(...)
→ build_graph(checkpointer=...)
→ graph.invoke(..., configurable.thread_id)
```

Nguồn cấu hình đang hoạt động là `configs/lab.yaml`, mặc định dùng:

```yaml
checkpointer: memory
```

Biến `CHECKPOINTER` trong `.env.example` hiện chưa được `cli.py` đọc nên thay đổi biến đó không làm backend thay đổi.

## 2. Vai trò của `thread_id`

`thread_id` là khóa giúp checkpointer biết state thuộc lần chạy nào.

`initial_state()` tạo thread ID, sau đó CLI truyền đúng shape LangGraph yêu cầu:

```python
{"configurable": {"thread_id": state["thread_id"]}}
```

Quy tắc sử dụng:

- Dùng cùng `thread_id` khi đọc lại state/history hoặc resume một run.
- Dùng `thread_id` mới cho scenario khác để checkpoint không chồng lên nhau.
- Có database nhưng dùng sai thread ID vẫn không thể recovery đúng run.

## 3. Ba mức persistence

| Backend | Trạng thái | Evidence phù hợp |
|---|---|---|
| Memory | Có sẵn và là mặc định | Graph dùng `MemorySaver`, mỗi run có thread ID, đọc được state/history trong cùng process |
| SQLite | Extension thực tế | State còn sau khi process dừng; có thể chứng minh resume hoặc crash recovery |
| Postgres | Optional extension | Durable multi-process backend với lifecycle và config/secret rõ ràng |

Memory đủ để chứng minh core checkpoint wiring, nhưng dữ liệu mất khi process kết thúc. SQLite là bằng chứng recovery mạnh hơn. Postgres không bắt buộc cho core lab.

## 4. Evidence cần có

Không nên chỉ mô tả rằng persistence “đã được cấu hình”. Cần chứng minh bằng dữ liệu chạy thật:

- Compiled graph giữ đúng checkpointer được truyền vào.
- Mỗi run có `thread_id` riêng.
- Đọc được latest state hoặc state history theo đúng thread.
- Với SQLite/Postgres: state vẫn còn sau khi process cũ kết thúc hoặc có một lần resume thành công.

Database chạy thành công chưa đủ để chứng minh recovery; report cần có state history hoặc resume evidence.

## 5. Lưu ý khi làm extension

### SQLite

- Cài extra/package SQLite checkpointer.
- Dùng connection tương thích với `SqliteSaver`.
- Bật WAL theo hướng dẫn của repo.
- Không commit `checkpoints.db` hoặc các file WAL/SHM.

### Postgres

- Docker Compose chỉ khởi động database.
- Code vẫn phải implement adapter và truyền `database_url` vào checkpointer.
- Quản lý lifecycle, connection và secret rõ ràng.

## 6. Kết quả mong đợi

- Graph compile bằng checkpointer thật được truyền từ CLI.
- Mỗi scenario có thread ID riêng và ổn định trong suốt run.
- Report có evidence state/history hoặc recovery cụ thể.
- Không tuyên bố crash recovery nếu mới chỉ chạy MemorySaver hoặc khởi động database.

## 7. Evidence đã kiểm tra trong project

Đã bổ sung test bằng `MemorySaver` trong `tests/test_graph_build.py`:

- `build_checkpointer("memory")` tạo checkpointer thật.
- Compiled graph giữ đúng cùng object checkpointer được truyền vào.
- `graph.invoke()` nhận config theo shape `configurable.thread_id`.
- `graph.get_state(config)` đọc lại đúng latest state của thread.
- `graph.get_state_history(config)` trả nhiều snapshot và mọi snapshot gắn với đúng thread ID.
- Hai scenario có thread ID khác nhau giữ `scenario_id` và query riêng, không ghi đè nhau.

CLI dùng type `RunnableConfig` để biểu diễn config truyền vào graph. `build_checkpointer()` cũng có return type `BaseCheckpointSaver | None` rõ ràng.

Evidence này chứng minh Memory persistence trong cùng process; chưa được dùng để tuyên bố durable recovery sau khi process kết thúc.
