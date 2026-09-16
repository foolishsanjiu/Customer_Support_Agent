import json
from pathlib import Path

from app.evaluation.gates import evaluate_regression_gate
from app.evaluation.models import EvalReport, GateResult

BASELINE_PATH = Path("evals/baselines/p0-release-deepseek-flash-aeb56401.json")
P1_BASELINE_PATH = Path("evals/baselines/p1-functional-v2-deepseek-flash-aeb56401.json")


def test_protected_p0_baseline_is_valid_and_comparable() -> None:
    payload = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    report = EvalReport.model_validate(payload)
    stored_gate = GateResult.model_validate(payload["gate"])

    comparison = evaluate_regression_gate(report, report)

    assert report.metadata.git_commit == "64a7f7134f98cdf64b4ae5896935c59b91b336ca"
    assert report.functional_metrics["case_count"] == 60
    assert report.functional_category_metrics == {}
    assert report.security_metrics["case_count"] == 20
    assert stored_gate.passed is True
    assert stored_gate.category_quality_thresholds == {}
    assert comparison.passed is True
    assert comparison.comparable_to_baseline is True


def test_protected_p1_baseline_is_valid_and_comparable() -> None:
    payload = json.loads(P1_BASELINE_PATH.read_text(encoding="utf-8"))
    report = EvalReport.model_validate(payload)
    stored_gate = GateResult.model_validate(payload["gate"])

    comparison = evaluate_regression_gate(
        report,
        report,
        minimum_category_task_success_rate=0.8,
        minimum_category_tool_selection_accuracy=0.9,
    )

    assert report.metadata.git_commit == "802e5601219696152644d8e6b28794f5ad27aa64"
    assert report.functional_metrics["case_count"] == 150
    assert set(report.functional_category_metrics) == {
        "order",
        "shipping",
        "refund",
        "cancellation",
        "policy_faq",
        "multi_turn",
        "missing_or_failure",
    }
    assert report.security_metrics["case_count"] == 20
    assert stored_gate.passed is True
    assert stored_gate.category_quality_thresholds == {
        "task_success_rate": 0.8,
        "tool_selection_accuracy": 0.9,
    }
    assert comparison.passed is True
    assert comparison.comparable_to_baseline is True
