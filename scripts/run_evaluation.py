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
from app.evaluation.scoring import score_functional, score_security


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
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--config-version", required=True)
    parser.add_argument("--temperature", type=float, default=0)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--quality-tolerance", type=float, default=0.02)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    functional_cases = load_functional_cases(args.functional_dataset)
    security_cases = load_security_cases(args.security_dataset)
    functional_observations = _load_observations(
        args.functional_observations, FunctionalObservation
    )
    security_observations = _load_observations(args.security_observations, SecurityObservation)
    report = EvalReport(
        metadata=EvalMetadata(
            git_commit=_git_commit(),
            dataset_version=args.dataset_version,
            prompt_version=args.prompt_version,
            eval_provider=args.provider,
            eval_model_id=args.model_id,
            eval_model_version_or_snapshot=args.model_version,
            temperature=args.temperature,
            seed_if_supported=args.seed,
            eval_config_version=args.config_version,
            timestamp=datetime.now(UTC),
        ),
        functional_metrics=score_functional(functional_cases, functional_observations),
        security_metrics=score_security(security_cases, security_observations),
    )
    baseline = EvalReport.model_validate_json(args.baseline.read_text()) if args.baseline else None
    gate = evaluate_regression_gate(report, baseline, quality_tolerance=args.quality_tolerance)
    payload = report.model_dump(mode="json")
    payload["gate"] = gate.model_dump(mode="json")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Evaluation gate {'passed' if gate.passed else 'failed'}; report={args.output}")
    return 0 if gate.passed else 1


def _load_observations(path: Path, model):
    return TypeAdapter(list[model]).validate_json(path.read_text(encoding="utf-8"))


def _git_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
