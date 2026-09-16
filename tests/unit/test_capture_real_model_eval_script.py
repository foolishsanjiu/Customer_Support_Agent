from argparse import Namespace
from pathlib import Path

import pytest

from scripts.capture_real_model_eval import _validate_thresholds, evaluate_quality_gate, parse_args


def test_capture_defaults_to_p1_functional_dataset(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["capture_real_model_eval", "--output", "observations.json"])

    args = parse_args()

    assert args.dataset == Path("evals/datasets/functional_v2.json")


def test_real_model_quality_gate_records_passed_absolute_thresholds() -> None:
    gate = evaluate_quality_gate(
        {"task_success_rate": 1.0, "tool_selection_accuracy": 1.0},
        minimum_task_success_rate=1.0,
        minimum_tool_selection_accuracy=1.0,
    )

    assert gate == {
        "passed": True,
        "thresholds": {"task_success_rate": 1.0, "tool_selection_accuracy": 1.0},
        "failures": [],
    }


def test_real_model_quality_gate_reports_all_failed_thresholds() -> None:
    gate = evaluate_quality_gate(
        {"task_success_rate": 0.7, "tool_selection_accuracy": 0.8},
        minimum_task_success_rate=0.8,
        minimum_tool_selection_accuracy=0.9,
    )

    assert gate is not None
    assert gate["passed"] is False
    assert len(gate["failures"]) == 2


def test_real_model_quality_gate_is_optional() -> None:
    assert (
        evaluate_quality_gate(
            {"task_success_rate": 0.0, "tool_selection_accuracy": 0.0},
            minimum_task_success_rate=None,
            minimum_tool_selection_accuracy=None,
        )
        is None
    )


def test_real_model_quality_gate_rejects_invalid_threshold() -> None:
    args = Namespace(
        minimum_task_success_rate=1.1,
        minimum_tool_selection_accuracy=None,
    )

    with pytest.raises(ValueError, match="must be between zero and one"):
        _validate_thresholds(args)
