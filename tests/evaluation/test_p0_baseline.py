import json
from pathlib import Path

from app.evaluation.gates import evaluate_regression_gate
from app.evaluation.models import EvalReport, GateResult

BASELINE_PATH = Path("evals/baselines/p0-release-deepseek-flash-aeb56401.json")


def test_protected_p0_baseline_is_valid_and_comparable() -> None:
    payload = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    report = EvalReport.model_validate(payload)
    stored_gate = GateResult.model_validate(payload["gate"])

    comparison = evaluate_regression_gate(report, report)

    assert report.metadata.git_commit == "64a7f7134f98cdf64b4ae5896935c59b91b336ca"
    assert report.functional_metrics["case_count"] == 60
    assert report.security_metrics["case_count"] == 20
    assert stored_gate.passed is True
    assert comparison.passed is True
    assert comparison.comparable_to_baseline is True
