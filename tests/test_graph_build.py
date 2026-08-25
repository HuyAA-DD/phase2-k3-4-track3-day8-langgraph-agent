"""Offline tests for graph construction and route wiring."""

from __future__ import annotations

from typing import Self

import pytest
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.checkpoint.memory import MemorySaver
from pytest import MonkeyPatch

from langgraph_agent_lab import nodes
from langgraph_agent_lab.graph import build_graph
from langgraph_agent_lab.persistence import build_checkpointer
from langgraph_agent_lab.state import AgentState, Route, Scenario, initial_state, make_event


class _FakeGraphLLM:
    def __init__(self, route: str) -> None:
        self.route = route

    def with_structured_output(self, schema: object) -> Self:
        return self

    def invoke(self, value: str | list[BaseMessage]) -> dict[str, str] | AIMessage:
        if isinstance(value, str):
            return {"route": self.route}
        return AIMessage(content="Grounded fake answer.")


def test_build_graph_registers_all_nodes_and_passes_through_checkpointer() -> None:
    checkpointer = MemorySaver()

    graph = build_graph(checkpointer=checkpointer)

    graph_nodes = set(graph.get_graph().nodes)
    assert {
        "intake",
        "classify",
        "tool",
        "evaluate",
        "answer",
        "clarify",
        "risky_action",
        "approval",
        "retry",
        "dead_letter",
        "finalize",
    } <= graph_nodes
    assert graph.checkpointer is checkpointer


@pytest.mark.parametrize(
    ("route", "expected_nodes"),
    [
        ("simple", ["intake", "classify", "answer", "finalize"]),
        ("tool", ["intake", "classify", "tool", "evaluate", "answer", "finalize"]),
        ("missing_info", ["intake", "classify", "clarify", "finalize"]),
        (
            "risky",
            [
                "intake",
                "classify",
                "risky_action",
                "approval",
                "tool",
                "evaluate",
                "answer",
                "finalize",
            ],
        ),
        (
            "error",
            [
                "intake",
                "classify",
                "retry",
                "tool",
                "evaluate",
                "retry",
                "tool",
                "evaluate",
                "answer",
                "finalize",
            ],
        ),
    ],
)
def test_graph_executes_each_route_offline(
    monkeypatch: MonkeyPatch,
    route: str,
    expected_nodes: list[str],
) -> None:
    fake_llm = _FakeGraphLLM(route)

    def fake_get_llm() -> _FakeGraphLLM:
        return fake_llm

    monkeypatch.setattr(nodes, "get_llm", fake_get_llm)
    scenario = Scenario(
        id=f"offline-{route}",
        query="A generic support request",
        expected_route=Route(route),
    )
    state = initial_state(scenario)

    result = build_graph().invoke(state)

    assert result["route"] == route
    assert [event["node"] for event in result["events"]] == expected_nodes
    assert result.get("final_answer") or result.get("pending_question")


def test_error_route_retry_events_match_counter(monkeypatch: MonkeyPatch) -> None:
    fake_llm = _FakeGraphLLM("error")

    def fake_get_llm() -> _FakeGraphLLM:
        return fake_llm

    monkeypatch.setattr(nodes, "get_llm", fake_get_llm)
    scenario = Scenario(
        id="bounded-error",
        query="A recoverable service failure",
        expected_route=Route.ERROR,
        max_attempts=3,
    )

    result = build_graph().invoke(initial_state(scenario))

    retry_events = [event for event in result["events"] if event["node"] == "retry"]
    assert result["attempt"] == 2
    assert [event["metadata"]["attempt"] for event in retry_events] == [1, 2]
    assert len(result["errors"]) == len(retry_events) == 2
    assert len(result["tool_results"]) == 2
    assert result["events"][-1]["node"] == "finalize"


def test_s07_dead_letter_stops_before_tool(monkeypatch: MonkeyPatch) -> None:
    fake_llm = _FakeGraphLLM("error")

    def fake_get_llm() -> _FakeGraphLLM:
        return fake_llm

    monkeypatch.setattr(nodes, "get_llm", fake_get_llm)
    scenario = Scenario(
        id="S07_dead_letter",
        query="System failure cannot recover after multiple attempts",
        expected_route=Route.ERROR,
        should_retry=True,
        max_attempts=1,
    )

    result = build_graph().invoke(initial_state(scenario))

    event_nodes = [event["node"] for event in result["events"]]
    assert event_nodes == ["intake", "classify", "retry", "dead_letter", "finalize"]
    assert result["attempt"] == result["max_attempts"] == 1
    assert result["tool_results"] == []
    assert len(result["errors"]) == 1
    assert "manual review" in result["final_answer"]
    assert event_nodes[-2:] == ["dead_letter", "finalize"]


def test_approved_risky_action_runs_tool_only_after_approval(
    monkeypatch: MonkeyPatch,
) -> None:
    fake_llm = _FakeGraphLLM("risky")

    def fake_get_llm() -> _FakeGraphLLM:
        return fake_llm

    monkeypatch.setattr(nodes, "get_llm", fake_get_llm)
    scenario = Scenario(
        id="generic-approved-probe",
        query="Perform an action that requires review",
        expected_route=Route.RISKY,
    )

    result = build_graph().invoke(initial_state(scenario))

    event_nodes = [event["node"] for event in result["events"]]
    assert event_nodes == [
        "intake",
        "classify",
        "risky_action",
        "approval",
        "tool",
        "evaluate",
        "answer",
        "finalize",
    ]
    assert event_nodes.index("approval") < event_nodes.index("tool")
    assert result["approval"]["approved"] is True
    assert event_nodes[-1] == "finalize"


def test_rejected_risky_action_clarifies_without_running_tool(
    monkeypatch: MonkeyPatch,
) -> None:
    fake_llm = _FakeGraphLLM("risky")

    def fake_get_llm() -> _FakeGraphLLM:
        return fake_llm

    def rejected_approval(state: AgentState) -> dict[str, object]:
        assert state.get("proposed_action")
        return {
            "approval": {
                "approved": False,
                "reviewer": "local-probe-reviewer",
                "comment": "More details are required before approval.",
            },
            "events": [
                make_event(
                    "approval",
                    "completed",
                    "rejected approval decision recorded",
                    approved=False,
                )
            ],
        }

    monkeypatch.setattr(nodes, "get_llm", fake_get_llm)
    monkeypatch.setattr(nodes, "approval_node", rejected_approval)
    scenario = Scenario(
        id="generic-rejected-probe",
        query="Perform an action that requires review",
        expected_route=Route.RISKY,
    )

    result = build_graph().invoke(initial_state(scenario))

    event_nodes = [event["node"] for event in result["events"]]
    assert event_nodes == [
        "intake",
        "classify",
        "risky_action",
        "approval",
        "clarify",
        "finalize",
    ]
    assert "tool" not in event_nodes
    assert result["approval"]["approved"] is False
    assert result["pending_question"]
    assert event_nodes[-1] == "finalize"


def test_memory_checkpointer_records_history_for_thread(monkeypatch: MonkeyPatch) -> None:
    fake_llm = _FakeGraphLLM("missing_info")

    def fake_get_llm() -> _FakeGraphLLM:
        return fake_llm

    monkeypatch.setattr(nodes, "get_llm", fake_get_llm)
    checkpointer = build_checkpointer("memory")
    assert checkpointer is not None
    graph = build_graph(checkpointer=checkpointer)
    scenario = Scenario(
        id="history-probe",
        query="A request that needs more details",
        expected_route=Route.MISSING_INFO,
    )
    state = initial_state(scenario)
    config = {"configurable": {"thread_id": state["thread_id"]}}

    result = graph.invoke(state, config=config)
    latest = graph.get_state(config)
    history = list(graph.get_state_history(config))

    assert graph.checkpointer is checkpointer
    assert result["thread_id"] == state["thread_id"]
    assert latest.values["scenario_id"] == scenario.id
    assert latest.values["thread_id"] == state["thread_id"]
    assert len(history) >= 4
    assert all(
        snapshot.config["configurable"]["thread_id"] == state["thread_id"]
        for snapshot in history
    )


def test_memory_checkpointer_isolates_scenario_threads(monkeypatch: MonkeyPatch) -> None:
    fake_llm = _FakeGraphLLM("missing_info")

    def fake_get_llm() -> _FakeGraphLLM:
        return fake_llm

    monkeypatch.setattr(nodes, "get_llm", fake_get_llm)
    checkpointer = build_checkpointer("memory")
    assert checkpointer is not None
    graph = build_graph(checkpointer=checkpointer)
    first_state = initial_state(
        Scenario(id="thread-one", query="First request", expected_route=Route.MISSING_INFO)
    )
    second_state = initial_state(
        Scenario(id="thread-two", query="Second request", expected_route=Route.MISSING_INFO)
    )
    first_config = {"configurable": {"thread_id": first_state["thread_id"]}}
    second_config = {"configurable": {"thread_id": second_state["thread_id"]}}

    graph.invoke(first_state, config=first_config)
    graph.invoke(second_state, config=second_config)
    first_latest = graph.get_state(first_config)
    second_latest = graph.get_state(second_config)

    assert first_state["thread_id"] != second_state["thread_id"]
    assert first_latest.values["scenario_id"] == "thread-one"
    assert second_latest.values["scenario_id"] == "thread-two"
    assert first_latest.values["query"] == "First request"
    assert second_latest.values["query"] == "Second request"
