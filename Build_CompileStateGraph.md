# Build và compile StateGraph

## 1. Mục tiêu

`build_graph(checkpointer)` phải:

1. Tạo `StateGraph(AgentState)`.
2. Đăng ký đủ 11 node.
3. Nối fixed edge và conditional edge.
4. Compile bằng đúng checkpointer được truyền vào.
5. Trả compiled graph.

```text
AgentState → node hiện tại → routing/edge → node tiếp theo → ... → END
```

## 2. Đăng ký 11 node

Tên graph node là contract public mà routing function trả về:

| Tên graph node | Python function |
|---|---|
| `intake` | `intake_node` |
| `classify` | `classify_node` |
| `tool` | `tool_node` |
| `evaluate` | `evaluate_node` |
| `answer` | `answer_node` |
| `clarify` | `ask_clarification_node` |
| `risky_action` | `risky_action_node` |
| `approval` | `approval_node` |
| `retry` | `retry_or_fallback_node` |
| `dead_letter` | `dead_letter_node` |
| `finalize` | `finalize_node` |

Không đổi tên hoặc tạo alias vì routing và tests phụ thuộc chính xác các tên này.

## 3. Fixed edges

Các đường luôn đi cố định:

```text
START        → intake
intake       → classify
tool         → evaluate
risky_action → approval
answer       → finalize
clarify      → finalize
dead_letter  → finalize
finalize     → END
```

## 4. Conditional edges

Bốn node dùng routing function để chọn đường tiếp theo:

| Node nguồn | Routing function |
|---|---|
| `classify` | `route_after_classify` |
| `evaluate` | `route_after_evaluate` |
| `retry` | `route_after_retry` |
| `approval` | `route_after_approval` |

Destination trong path map phải khớp chính xác tên node đã đăng ký.

## 5. Compile graph

Cuối cùng compile và trả graph:

```python
compiled = builder.compile(checkpointer=checkpointer)
return compiled
```

Không tạo checkpointer mới bên trong `build_graph()`. CLI chịu trách nhiệm chọn backend và quản lý lifecycle; graph chỉ sử dụng object được truyền vào.

## 6. Kiểm tra

Khi provider và quota đã sẵn sàng, chạy có chủ đích:

```powershell
python -m pytest tests/test_graph_smoke.py -q
```

Smoke test gọi LLM thật khi có API key. Không ghi prompt, output nhạy cảm hoặc secret vào repository.

Kết quả mong đợi:

- Graph compile với checkpointer được truyền vào.
- Có đủ 11 node.
- Mọi nhánh cuối đều đi qua `finalize → END`.
