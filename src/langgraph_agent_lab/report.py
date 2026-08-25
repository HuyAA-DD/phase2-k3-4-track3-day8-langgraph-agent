"""Report generation helper."""

from __future__ import annotations

from pathlib import Path

from .metrics import MetricsReport


def _markdown_cell(value: object) -> str:
    """Return a value safe for use inside a Markdown table cell."""
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_report(metrics: MetricsReport) -> str:
    """Render a complete Markdown lab report from metrics data."""
    lines = [
        "# Day 08 LangGraph Lab Report",
        "",
        "## 1. Student",
        "",
        "- Name: Tin Duong",
        "- Repo: `phase2-k3-4-track3-day8-langgraph-agent`",
        "- Base commit: `6d8252d` (report generated from the current working tree)",
        "- Report date: 2026-08-25",
        "- Secret handling: no API key, environment dump, credential, or raw prompt is included.",
        "",
        "## 2. Architecture",
        "",
        "The compiled `StateGraph(AgentState)` registers eleven focused nodes:",
        "",
        "| Graph node | Responsibility |",
        "|---|---|",
        "| `intake` | Normalize the incoming query and start the audit trail |",
        "| `classify` | Select one of five routes with validated LLM structured output |",
        "| `tool` | Execute a guarded mock tool and append one result |",
        "| `evaluate` | Judge the latest tool result as success or retry |",
        "| `answer` | Generate a context-grounded LLM answer |",
        "| `clarify` | Produce an actionable clarification question |",
        "| `risky_action` | Prepare, but do not execute, a side-effecting action |",
        "| `approval` | Record the mock review decision before tool execution |",
        "| `retry` | Increment the bounded retry counter exactly once |",
        "| `dead_letter` | Escalate after the retry bound is exhausted |",
        "| `finalize` | Append the terminal audit event |",
        "",
        "Fixed edges: `START → intake → classify`, `tool → evaluate`, "
        "`risky_action → approval`, and each terminal-output node (`answer`, `clarify`, "
        "`dead_letter`) goes to `finalize → END`.",
        "",
        "Conditional edges run after `classify`, `evaluate`, `retry`, and `approval`. "
        "Their path maps use the same public node names returned by the four routing functions.",
        "",
        "Termination is explicit: retry routes to `tool` only while "
        "`attempt < max_attempts`; equality or an over-limit value fails closed to "
        "`dead_letter`. Every output branch then passes through `finalize → END`.",
        "",
        "## 3. State schema and reducers",
        "",
        "`AgentState` is `TypedDict(total=False)`, so nodes return only fields they change.",
        "",
        "| Fields | Update policy | Reason |",
        "|---|---|---|",
        (
            "| `messages`, `tool_results`, `errors`, `events` | append (`add`) | "
            "Preserve ordered conversation, tool, failure, and audit history |"
        ),
        (
            "| `thread_id`, `scenario_id`, `query` | overwrite | "
            "Identify and normalize the current run without accumulating copies |"
        ),
        (
            "| `route`, `risk_level` | overwrite | "
            "Store the current classification; `route` remains stable for metrics |"
        ),
        (
            "| `attempt`, `max_attempts`, `evaluation_result` | overwrite | "
            "Represent the current bounded-loop control values |"
        ),
        (
            "| `proposed_action`, `approval` | overwrite | "
            "Represent the action and latest review decision |"
        ),
        (
            "| `pending_question`, `final_answer` | overwrite | "
            "Store the current user-facing output |"
        ),
        "",
        "Nodes never mutate input lists. An append-only field update contains only the new "
        "item, for example `events: [make_event(...)]`, and LangGraph applies the reducer.",
        "",
        "## 4. Scenario results",
        "",
        "Source: `outputs/metrics.json`, rendered from the `MetricsReport` passed to "
        "`write_report()`; values below are not maintained separately by hand.",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Total scenarios | {metrics.total_scenarios} |",
        f"| Success rate | {metrics.success_rate:.2%} |",
        f"| Average nodes visited | {metrics.avg_nodes_visited:.2f} |",
        f"| Total retries | {metrics.total_retries} |",
        f"| Approval-node visits (`total_interrupts`) | {metrics.total_interrupts} |",
        f"| Durable resume success | {'yes' if metrics.resume_success else 'no'} |",
        "",
        (
            "`total_interrupts` currently counts approval-node visits. Core uses mock approval; "
            "this is not evidence of a real HITL interrupt/resume."
        ),
        "",
        (
            "| Scenario | Expected | Actual | Success | Nodes | Retries | Approval visits | "
            "Latency (ms) | Errors |"
        ),
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]

    for item in metrics.scenario_metrics:
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(item.scenario_id),
                    _markdown_cell(item.expected_route),
                    _markdown_cell(item.actual_route or "missing"),
                    "yes" if item.success else "no",
                    str(item.nodes_visited),
                    str(item.retry_count),
                    str(item.interrupt_count),
                    str(item.latency_ms),
                    str(len(item.errors)),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            (
                "Latency values are `0` because node timing is not instrumented in the current "
                "implementation; no latency performance claim is made."
            ),
            "",
            "## 5. Failure analysis",
            "",
            (
                "| Failure mode | Origin | Detection evidence | Containment and termination | "
                "Residual risk |"
            ),
            "|---|---|---|---|---|",
            (
                "| Transient tool failure / exhausted retry | `tool` returns a result containing "
                "`ERROR` | `tool_results[-1]`, `evaluation_result=needs_retry`, retry events, "
                "`attempt`, and append-only `errors` | Only `retry` increments once. Routing uses "
                "the post-update counter; `< max` returns to tool and `>= max` goes to "
                "`dead_letter → finalize → END` | The mock `ERROR` heuristic is simple; real tools "
                "need typed error classes, idempotency, and backoff |"
            ),
            (
                "| Risky action missing/rejected approval | A risky request reaches "
                "`risky_action` before review | `proposed_action`, approval mapping, approval "
                "event, "
                "and the absence of a tool event on rejection | `approved is True` is required for "
                "tool routing; otherwise the graph goes `clarify → finalize → END`. The tool also "
                "fails closed if invoked without valid approval | Core approval is mocked, so it "
                "does not prove identity, authorization policy, or real interrupt/resume |"
            ),
            (
                "| LLM/provider or structured-validation failure | Provider call in `classify` or "
                "`answer` | Failed event, exception type, `structured_validation=False`, and an "
                "append-only error without the raw exception text | Classification enters the "
                "bounded error route; answer returns an explicitly audited safe fallback, then "
                "finalizes | Availability still depends on the external provider; production needs "
                "timeouts, rate-limit policy, and provider failover |"
            ),
            "",
            "Current-run evidence: `S05_error` recorded two retries and terminated through answer; "
            "`S07_dead_letter` recorded one retry at `max_attempts=1`, skipped tool, produced an "
            "escalation answer, and finalized. `S04_risky` and `S06_delete` each recorded one "
            "approval-node visit before tool execution.",
            "",
            "## 6. Persistence and recovery evidence",
            "",
            "The active config is `checkpointer: memory`. CLI creates the checkpointer, passes the "
            "same object to `build_graph(checkpointer=...)`, and invokes each scenario with "
            "`{\"configurable\": {\"thread_id\": state[\"thread_id\"]}}`.",
            "",
            "Evidence in `tests/test_graph_build.py` verifies that the compiled graph retains the "
            "exact `MemorySaver` object, `get_state(config)` returns the latest state for the same "
            "thread, `get_state_history(config)` returns multiple snapshots all tagged with that "
            "thread ID, and two different thread IDs retain separate scenario/query values.",
            "",
            f"Durable resume demonstrated: **{'yes' if metrics.resume_success else 'no'}**. "
            "This is state-history evidence inside one process, not crash recovery. Memory state "
            "is "
            "lost when the process exits.",
            "",
            "## 7. Extension work",
            "",
            "| Item | Status | Proof / limitation |",
            "|---|---|---|",
            (
                "| Offline graph probes | Completed | Route, retry/dead-letter, approved/rejected "
                "approval, node order, and MemorySaver history tests pass |"
            ),
            (
                "| Real-provider smoke test | Completed | Six smoke tests passed with OpenAI; no "
                "prompt or response content was committed as evidence |"
            ),
            "| SQLite/Postgres durability | Not implemented | No cross-process recovery claimed |",
            (
                "| Real HITL interrupt/resume | Not implemented | Mock approval remains the CI "
                "default |"
            ),
            "",
            "## 8. Improvement plan",
            "",
            "**Next production priority: SQLite durable persistence with WAL and a recorded "
            "crash/resume test.** This closes the largest evidence gap: MemorySaver proves thread "
            "scoping and in-process history but cannot recover after process termination. After "
            "durability is proven, the next steps are real HITL authorization, production tool "
            "idempotency, provider failover, and real latency instrumentation.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(metrics: MetricsReport, output_path: str | Path) -> None:
    """Write the rendered report to a file."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(metrics), encoding="utf-8")
