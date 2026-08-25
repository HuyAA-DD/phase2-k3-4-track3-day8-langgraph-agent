"""Tests for Markdown report rendering."""

from langgraph_agent_lab.metrics import MetricsReport, ScenarioMetric
from langgraph_agent_lab.report import render_report


def test_render_report_contains_metrics_scenarios_and_analysis() -> None:
    metrics = MetricsReport(
        total_scenarios=2,
        success_rate=0.5,
        avg_nodes_visited=4.5,
        total_retries=1,
        total_interrupts=1,
        resume_success=False,
        scenario_metrics=[
            ScenarioMetric(
                scenario_id="S01|simple",
                success=True,
                expected_route="simple",
                actual_route="simple",
                nodes_visited=4,
            ),
            ScenarioMetric(
                scenario_id="S02_error",
                success=False,
                expected_route="error",
                actual_route="error",
                nodes_visited=5,
                retry_count=1,
                errors=["failure"],
            ),
        ],
    )

    report = render_report(metrics)

    assert "| Total scenarios | 2 |" in report
    assert "| Success rate | 50.00% |" in report
    assert "S01\\|simple" in report
    assert "## 1. Student" in report
    assert "## 2. Architecture" in report
    assert "`dead_letter`" in report
    assert "## 3. State schema and reducers" in report
    assert "node timing is not instrumented" in report
    assert "## 5. Failure analysis" in report
    assert "Detection evidence" in report
    assert "Residual risk" in report
    assert "## 6. Persistence and recovery evidence" in report
    assert "## 7. Extension work" in report
    assert "## 8. Improvement plan" in report
    assert "SQLite" in report
    assert "TODO(student)" not in report
