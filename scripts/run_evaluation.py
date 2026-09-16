import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter

from app.evaluation.gates import evaluate_regression_gate
from app.evaluation.loader import load_functional_cases, load_security_cases
from app.evaluation.models import (
    EvalMetadata,
    EvalReport,
    FunctionalObservation,
    SecurityObservation,
)
from app.evaluation.reporting import write_evaluation_report
from app.evaluation.scoring import score_functional, score_functional_by_category, score_security


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score versioned ResolveX evaluation observations."
    )
    parser.add_argument("--functional-dataset", type=Path, required=True)
    parser.add_argument("--functional-observations", type=Path, required=True)
    parser.add_argument("--security-dataset", type=Path, required=True)
    parser.add_argument("--security-observations", type=Path, required=True)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--prompt-version", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--model-version")
    parser.add_argument("--capture-metadata", type=Path)
    parser.add_argument("--config-version", required=True)
    parser.add_argument("--temperature", type=float, default=0)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--quality-tolerance", type=float, default=0.02)
    parser.add_argument("--minimum-task-success-rate", type=float, default=0.80)
    parser.add_argument("--minimum-tool-selection-accuracy", type=float, default=0.90)
    parser.add_argument("--minimum-category-task-success-rate", type=float, default=0.80)
    parser.add_argument("--minimum-category-tool-selection-accuracy", type=float, default=0.90)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    git_commit = _git_commit()
    response_models, system_fingerprints = _capture_identity(args.capture_metadata, args.model_id)
    model_version = _model_version(
        args.model_version,
        response_models,
        system_fingerprints,
        git_commit,
        args.model_id,
    )
    functional_cases = load_functional_cases(args.functional_dataset)
    security_cases = load_security_cases(args.security_dataset)
    functional_observations = _load_observations(
        args.functional_observations, FunctionalObservation
    )
    security_observations = _load_observations(args.security_observations, SecurityObservation)
    report = EvalReport(
        metadata=EvalMetadata(
            git_commit=git_commit,
            dataset_version=args.dataset_version,
            prompt_version=args.prompt_version,
            eval_provider=args.provider,
            eval_model_id=args.model_id,
            eval_model_version_or_snapshot=model_version,
            eval_response_models=response_models,
            eval_system_fingerprints=system_fingerprints,
            temperature=args.temperature,
            seed_if_supported=args.seed,
            eval_config_version=args.config_version,
            timestamp=datetime.now(UTC),
        ),
        functional_metrics=score_functional(functional_cases, functional_observations),
        functional_category_metrics=score_functional_by_category(
            functional_cases, functional_observations
        ),
        security_metrics=score_security(security_cases, security_observations),
    )
    baseline = EvalReport.model_validate_json(args.baseline.read_text()) if args.baseline else None
    gate = evaluate_regression_gate(
        report,
        baseline,
        quality_tolerance=args.quality_tolerance,
        minimum_task_success_rate=args.minimum_task_success_rate,
        minimum_tool_selection_accuracy=args.minimum_tool_selection_accuracy,
        minimum_category_task_success_rate=args.minimum_category_task_success_rate,
        minimum_category_tool_selection_accuracy=(args.minimum_category_tool_selection_accuracy),
    )
    write_evaluation_report(args.output, report, gate)
    print(f"Evaluation gate {'passed' if gate.passed else 'failed'}; report={args.output}")
    return 0 if gate.passed else 1


def _load_observations(path: Path, model):
    return TypeAdapter(list[model]).validate_json(path.read_text(encoding="utf-8"))


def _capture_identity(path: Path | None, expected_model: str) -> tuple[list[str], list[str]]:
    if path is None:
        return [], []
    payload = json.loads(path.read_text(encoding="utf-8"))
    requested_model = payload.get("requested_model")
    if requested_model != expected_model:
        raise ValueError(
            f"capture requested_model {requested_model!r} does not match --model-id "
            f"{expected_model!r}"
        )
    response_models = sorted(set(payload.get("response_models", [])))
    system_fingerprints = sorted(set(payload.get("system_fingerprints", [])))
    if len(response_models) > 1:
        raise ValueError("official evaluation cannot mix provider response models")
    if len(system_fingerprints) > 1:
        raise ValueError("official evaluation cannot mix provider system fingerprints")
    return response_models, system_fingerprints


def _model_version(
    explicit_version: str | None,
    response_models: list[str],
    system_fingerprints: list[str],
    git_commit: str,
    requested_model: str,
) -> str:
    if explicit_version:
        return explicit_version
    response_model = response_models[0] if response_models else requested_model
    if system_fingerprints:
        return f"{response_model}@fp:{system_fingerprints[0]}"
    return f"{response_model}@unversioned:{git_commit}"


def _git_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
