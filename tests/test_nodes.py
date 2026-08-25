"""Focused unit tests for node contracts."""

from __future__ import annotations

from copy import deepcopy
from typing import Self

import pytest
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel
from pytest import MonkeyPatch

from langgraph_agent_lab import nodes
from langgraph_agent_lab.state import AgentState


class _FakeStructuredLLM:
    def __init__(self, response: object) -> None:
        self.response = response
        self.prompt = ""

    def with_structured_output(self, schema: type[BaseModel]) -> Self:
        return self

    def invoke(self, prompt: str) -> object:
        self.prompt = prompt
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class _FakeAnswerLLM:
    def __init__(self, response: AIMessage | Exception) -> None:
        self.response = response
        self.messages: list[BaseMessage] = []

    def invoke(self, messages: list[BaseMessage]) -> AIMessage:
        self.messages = messages
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.mark.parametrize(
    ("route", "expected_risk"),
    [
        ("simple", "low"),
        ("tool", "low"),
        ("missing_info", "low"),
        ("risky", "high"),
        ("error", "low"),
    ],
)
def test_classify_node_uses_validated_llm_output(
    monkeypatch: MonkeyPatch,
    route: str,
    expected_risk: str,
) -> None:
    fake_llm = _FakeStructuredLLM({"route": route})

    def fake_get_llm() -> _FakeStructuredLLM:
        return fake_llm

    monkeypatch.setattr(nodes, "get_llm", fake_get_llm)
    state: AgentState = {
        "query": "Please handle this request",
        "scenario_id": "must-not-appear-in-prompt",
    }
    original_state = deepcopy(state)

    update = nodes.classify_node(state)

    assert update["route"] == route
    assert update["risk_level"] == expected_risk
    assert "errors" not in update
    assert "Please handle this request" in fake_llm.prompt
    assert "must-not-appear-in-prompt" not in fake_llm.prompt
    assert state == original_state
    event = update["events"][0]
    assert event["node"] == "classify"
    assert event["event_type"] == "completed"
    assert event["metadata"]["structured_validation"] is True


def test_classify_node_routes_llm_failure_to_auditable_error(
    monkeypatch: MonkeyPatch,
) -> None:
    fake_llm = _FakeStructuredLLM(RuntimeError("secret-api-key"))

    def fake_get_llm() -> _FakeStructuredLLM:
        return fake_llm

    monkeypatch.setattr(nodes, "get_llm", fake_get_llm)

    update = nodes.classify_node({"query": "A provider failure should be controlled"})

    assert update["route"] == "error"
    assert update["risk_level"] == "low"
    assert len(update["errors"]) == 1
    assert "secret-api-key" not in str(update)
    event = update["events"][0]
    assert event["event_type"] == "failed"
    assert event["metadata"]["structured_validation"] is False
    assert event["metadata"]["error_type"] == "RuntimeError"


def test_classify_node_rejects_route_outside_contract(monkeypatch: MonkeyPatch) -> None:
    fake_llm = _FakeStructuredLLM({"route": "done"})

    def fake_get_llm() -> _FakeStructuredLLM:
        return fake_llm

    monkeypatch.setattr(nodes, "get_llm", fake_get_llm)

    update = nodes.classify_node({"query": "invalid structured route"})

    assert update["route"] == "error"
    assert update["events"][0]["event_type"] == "failed"


def test_clarification_node_requests_actionable_missing_information() -> None:
    state: AgentState = {"query": "Can you fix it?"}
    original_state = deepcopy(state)

    update = nodes.ask_clarification_node(state)

    assert update["pending_question"] == update["final_answer"]
    assert "what is affected" in str(update["pending_question"])
    assert "identifier" in str(update["pending_question"])
    assert update["events"][0]["metadata"]["reason"] == "missing_information"
    assert state == original_state


def test_clarification_node_explains_rejected_approval() -> None:
    state: AgentState = {
        "query": "Refund this customer",
        "proposed_action": "Issue a customer refund",
        "approval": {
            "approved": False,
            "reviewer": "reviewer-1",
            "comment": "Refund amount is missing",
        },
    }

    update = nodes.ask_clarification_node(state)

    question = str(update["pending_question"])
    assert "not approved" in question
    assert "Refund amount is missing" in question
    assert "modify or cancel" in question
    assert update["events"][0]["metadata"]["reason"] == "approval_rejected"


def test_risky_action_node_only_prepares_action() -> None:
    state: AgentState = {
        "query": "Refund the customer and send a confirmation email",
        "risk_level": "high",
        "tool_results": ["existing result"],
    }
    original_state = deepcopy(state)

    update = nodes.risky_action_node(state)

    assert "Refund the customer" in str(update["proposed_action"])
    assert "approved before execution" in str(update["proposed_action"])
    assert "tool_results" not in update
    assert update["events"][0]["metadata"]["requires_approval"] is True
    assert state == original_state


def test_approval_node_records_serializable_mock_approval() -> None:
    state: AgentState = {"proposed_action": "Issue a customer refund"}
    original_state = deepcopy(state)

    update = nodes.approval_node(state)

    assert update["approval"] == {
        "approved": True,
        "reviewer": "mock-reviewer",
        "comment": "Approved by the default non-interactive review policy.",
    }
    assert update["events"][0]["metadata"]["approved"] is True
    assert "tool_results" not in update
    assert state == original_state


def test_approval_node_rejects_missing_proposed_action() -> None:
    update = nodes.approval_node({})

    assert update["approval"]["approved"] is False
    assert len(update["errors"]) == 1
    assert update["events"][0]["event_type"] == "failed"


@pytest.mark.parametrize("attempt", [0, 1])
def test_tool_node_simulates_transient_error_without_incrementing_attempt(attempt: int) -> None:
    state: AgentState = {
        "route": "error",
        "attempt": attempt,
        "query": "Service timeout",
    }
    original_state = deepcopy(state)

    update = nodes.tool_node(state)

    assert len(update["tool_results"]) == 1
    assert "ERROR" in update["tool_results"][0]
    assert "attempt" not in update
    assert update["events"][0]["event_type"] == "failed"
    assert state == original_state


def test_tool_node_succeeds_after_transient_attempt_threshold() -> None:
    update = nodes.tool_node(
        {"route": "error", "attempt": 2, "query": "Service timeout"}
    )

    assert "SUCCESS" in update["tool_results"][0]
    assert update["events"][0]["event_type"] == "completed"


def test_tool_node_blocks_risky_action_without_approval() -> None:
    update = nodes.tool_node(
        {
            "route": "risky",
            "attempt": 0,
            "query": "Refund the customer",
            "proposed_action": "Issue a refund",
        }
    )

    assert "ERROR" in update["tool_results"][0]
    assert len(update["errors"]) == 1
    assert update["events"][0]["metadata"]["approval_observed"] is False


def test_tool_node_executes_approved_risky_action() -> None:
    update = nodes.tool_node(
        {
            "route": "risky",
            "attempt": 0,
            "query": "Refund the customer",
            "proposed_action": "Issue a refund",
            "approval": {
                "approved": True,
                "reviewer": "mock-reviewer",
                "comment": "Approved",
            },
        }
    )

    assert "SUCCESS" in update["tool_results"][0]
    assert "Issue a refund" in update["tool_results"][0]
    assert update["events"][0]["event_type"] == "completed"


@pytest.mark.parametrize(
    ("tool_results", "expected_verdict"),
    [
        (["ERROR: old failure", "SUCCESS: latest result"], "success"),
        (["SUCCESS: old result", "ERROR: latest failure"], "needs_retry"),
    ],
)
def test_evaluate_node_uses_only_latest_result(
    tool_results: list[str],
    expected_verdict: str,
) -> None:
    update = nodes.evaluate_node({"tool_results": tool_results})

    assert update["evaluation_result"] == expected_verdict
    assert update["events"][0]["metadata"]["verdict"] == expected_verdict


def test_evaluate_node_retries_when_result_is_missing() -> None:
    update = nodes.evaluate_node({"tool_results": []})

    assert update["evaluation_result"] == "needs_retry"
    assert len(update["errors"]) == 1
    assert update["events"][0]["event_type"] == "failed"


def test_retry_node_increments_once_and_returns_append_only_updates() -> None:
    state: AgentState = {
        "attempt": 1,
        "max_attempts": 3,
        "tool_results": ["ERROR: transient failure"],
        "errors": ["existing error"],
    }
    original_state = deepcopy(state)

    update = nodes.retry_or_fallback_node(state)

    assert update["attempt"] == 2
    assert len(update["errors"]) == 1
    assert "Retry 2/3" in update["errors"][0]
    assert update["events"][0]["metadata"]["attempt"] == 2
    assert state == original_state


def test_dead_letter_node_escalates_without_overwriting_route() -> None:
    state: AgentState = {
        "route": "error",
        "attempt": 3,
        "max_attempts": 3,
        "errors": ["failure 1", "failure 2"],
        "tool_results": ["ERROR: latest failure"],
    }

    update = nodes.dead_letter_node(state)

    assert "manual review" in update["final_answer"]
    assert "route" not in update
    assert update["events"][0]["node"] == "dead_letter"
    assert update["events"][0]["metadata"]["error_count"] == 2


def test_answer_node_uses_grounded_context_without_scenario_id(
    monkeypatch: MonkeyPatch,
) -> None:
    fake_llm = _FakeAnswerLLM(AIMessage(content="The approved refund was completed."))

    def fake_get_llm() -> _FakeAnswerLLM:
        return fake_llm

    monkeypatch.setattr(nodes, "get_llm", fake_get_llm)
    state: AgentState = {
        "query": "Refund the customer",
        "scenario_id": "must-not-appear-in-answer-prompt",
        "tool_results": ["SUCCESS: refund completed"],
        "proposed_action": "Issue a customer refund",
        "approval": {
            "approved": True,
            "reviewer": "mock-reviewer",
            "comment": "Approved",
        },
    }
    original_state = deepcopy(state)

    update = nodes.answer_node(state)

    assert update["final_answer"] == "The approved refund was completed."
    prompt = "\n".join(str(message.content) for message in fake_llm.messages)
    assert "Refund the customer" in prompt
    assert "SUCCESS: refund completed" in prompt
    assert "Issue a customer refund" in prompt
    assert "must-not-appear-in-answer-prompt" not in prompt
    assert update["events"][0]["event_type"] == "completed"
    assert update["events"][0]["metadata"]["tool_result_count"] == 1
    assert state == original_state


def test_answer_node_prompt_forbids_claiming_rejected_action(
    monkeypatch: MonkeyPatch,
) -> None:
    fake_llm = _FakeAnswerLLM(AIMessage(content="The action was not performed."))

    def fake_get_llm() -> _FakeAnswerLLM:
        return fake_llm

    monkeypatch.setattr(nodes, "get_llm", fake_get_llm)
    state: AgentState = {
        "query": "Delete the account",
        "proposed_action": "Delete the account permanently",
        "approval": {
            "approved": False,
            "reviewer": "reviewer-1",
            "comment": "Rejected",
        },
    }

    update = nodes.answer_node(state)

    system_prompt = str(fake_llm.messages[0].content)
    context = str(fake_llm.messages[1].content)
    assert "not performed" in system_prompt
    assert '"approved": false' in context
    assert update["final_answer"] == "The action was not performed."


@pytest.mark.parametrize(
    "response",
    [RuntimeError("secret-api-key"), AIMessage(content="   ")],
)
def test_answer_node_returns_audited_fallback(
    monkeypatch: MonkeyPatch,
    response: AIMessage | Exception,
) -> None:
    fake_llm = _FakeAnswerLLM(response)

    def fake_get_llm() -> _FakeAnswerLLM:
        return fake_llm

    monkeypatch.setattr(nodes, "get_llm", fake_get_llm)

    update = nodes.answer_node({"query": "Help me"})

    assert "could not generate a reliable response" in update["final_answer"]
    assert len(update["errors"]) == 1
    assert "secret-api-key" not in str(update)
    assert update["events"][0]["event_type"] == "failed"
    assert update["events"][0]["metadata"]["fallback_used"] is True


@pytest.mark.parametrize(
    "state",
    [
        {"route": "simple", "final_answer": "Here is the answer."},
        {"route": "missing_info", "pending_question": "Which account is affected?"},
    ],
)
def test_finalize_node_appends_one_event_without_overwriting_route(state: AgentState) -> None:
    original_state = deepcopy(state)

    update = nodes.finalize_node(state)

    assert update == {
        "events": [
            {
                "node": "finalize",
                "event_type": "completed",
                "message": "workflow finished",
                "latency_ms": 0,
                "metadata": {},
            }
        ]
    }
    assert "route" not in update
    assert state == original_state


def test_finalize_node_audits_missing_user_facing_output() -> None:
    update = nodes.finalize_node({"route": "simple"})

    assert len(update["errors"]) == 1
    assert "route" not in update
    assert update["events"][0]["node"] == "finalize"
    assert update["events"][0]["event_type"] == "failed"


def test_non_loop_path_has_consistent_audit_trail(monkeypatch: MonkeyPatch) -> None:
    fake_llm = _FakeStructuredLLM({"route": "missing_info"})

    def fake_get_llm() -> _FakeStructuredLLM:
        return fake_llm

    monkeypatch.setattr(nodes, "get_llm", fake_get_llm)
    intake_update = nodes.intake_node({"query": "  Can you fix it?  "})
    classify_update = nodes.classify_node({"query": str(intake_update["query"])})
    clarify_update = nodes.ask_clarification_node({"query": str(intake_update["query"])})
    finalize_update = nodes.finalize_node(
        {"pending_question": str(clarify_update["pending_question"])}
    )

    audit_events = [
        *intake_update["events"],
        *classify_update["events"],
        *clarify_update["events"],
        *finalize_update["events"],
    ]

    assert [event["node"] for event in audit_events] == [
        "intake",
        "classify",
        "clarify",
        "finalize",
    ]
    assert all(
        set(event) == {"node", "event_type", "message", "latency_ms", "metadata"}
        for event in audit_events
    )
