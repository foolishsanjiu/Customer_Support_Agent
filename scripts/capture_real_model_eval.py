import argparse
import asyncio
import json
from pathlib import Path

from pydantic import TypeAdapter

from app.agent.llm import OpenAICompatibleClient
from app.core.config import get_settings
from app.evaluation.loader import load_functional_cases
from app.evaluation.models import FunctionalObservation
from app.evaluation.real_model import capture_functional_case
from app.evaluation.scoring import score_functional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture real-model functional observations.")
    parser.add_argument("--dataset", type=Path, default=Path("evals/datasets/functional_v2.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--minimum-task-success-rate", type=float)
    parser.add_argument("--minimum-tool-selection-accuracy", type=float)
    return parser.parse_args()


async def capture(args: argparse.Namespace) -> None:
    _validate_thresholds(args)
    settings = get_settings()
    if settings.llm_api_key is None or not settings.llm_api_key.get_secret_value():
        raise RuntimeError("LLM_API_KEY is not configured")
    if not settings.llm_base_url or not settings.llm_model:
        raise RuntimeError("LLM_BASE_URL and LLM_MODEL are required")

    cases = load_functional_cases(args.dataset)
    if args.case_id:
        selected = set(args.case_id)
        cases = [case for case in cases if case.id in selected]
        missing = selected - {case.id for case in cases}
        if missing:
            raise ValueError(f"unknown case ids: {sorted(missing)}")
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit must be positive")
        cases = cases[: args.limit]
    if args.retries < 0:
        raise ValueError("--retries cannot be negative")

    client = OpenAICompatibleClient(
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=60,
    )
    observations = _load_existing(args.output) if args.resume else []
    completed_ids = {observation.case_id for observation in observations}
    expected_ids = {case.id for case in cases}
    unexpected = completed_ids - expected_ids
    if unexpected:
        raise ValueError(f"existing output has unexpected case ids: {sorted(unexpected)}")
    fingerprints: set[str] = set()
    response_models: set[str] = set()
    metadata_path = args.output.with_suffix(".metadata.json")
    if args.resume and metadata_path.exists():
        existing_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        fingerprints.update(existing_metadata.get("system_fingerprints", []))
        response_models.update(existing_metadata.get("response_models", []))
    for index, case in enumerate(cases, start=1):
        if case.id in completed_ids:
            print(f"skipped {index}/{len(cases)}: {case.id}")
            continue
        for attempt in range(args.retries + 1):
            try:
                result = await capture_functional_case(case, client, run_id=index)
                break
            except Exception:
                if attempt == args.retries:
                    raise
                print(f"retrying {case.id}: attempt {attempt + 2}/{args.retries + 1}")
        observations.append(result.observation)
        fingerprints.update(
            record.system_fingerprint for record in result.call_records if record.system_fingerprint
        )
        response_models.update(
            record.response_model for record in result.call_records if record.response_model
        )
        observations.sort(
            key=lambda item: next(i for i, case in enumerate(cases) if case.id == item.case_id)
        )
        _write_json(args.output, [item.model_dump(mode="json") for item in observations])
        _write_metadata(
            metadata_path, settings.llm_model, response_models, fingerprints, observations
        )
        print(f"captured {index}/{len(cases)}: {case.id}")

    metrics = score_functional(cases, observations)
    gate = evaluate_quality_gate(
        metrics,
        minimum_task_success_rate=args.minimum_task_success_rate,
        minimum_tool_selection_accuracy=args.minimum_tool_selection_accuracy,
    )
    _write_metadata(
        metadata_path,
        settings.llm_model,
        response_models,
        fingerprints,
        observations,
        metrics,
        gate,
    )
    print(f"observations={args.output}; metadata={metadata_path}")
    if gate is not None and not gate["passed"]:
        raise RuntimeError("real-model quality gate failed: " + "; ".join(gate["failures"]))


def _validate_thresholds(args: argparse.Namespace) -> None:
    for name in ("minimum_task_success_rate", "minimum_tool_selection_accuracy"):
        value = getattr(args, name)
        if value is not None and not 0 <= value <= 1:
            raise ValueError(f"--{name.replace('_', '-')} must be between zero and one")


def evaluate_quality_gate(
    metrics: dict[str, float | int],
    *,
    minimum_task_success_rate: float | None,
    minimum_tool_selection_accuracy: float | None,
) -> dict | None:
    thresholds = {
        metric: threshold
        for metric, threshold in (
            ("task_success_rate", minimum_task_success_rate),
            ("tool_selection_accuracy", minimum_tool_selection_accuracy),
        )
        if threshold is not None
    }
    if not thresholds:
        return None
    failures = [
        f"{metric} {float(metrics[metric]):.6f} is below minimum {threshold:.6f}"
        for metric, threshold in thresholds.items()
        if float(metrics[metric]) < threshold
    ]
    return {"passed": not failures, "thresholds": thresholds, "failures": failures}


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _load_existing(path: Path) -> list[FunctionalObservation]:
    if not path.exists():
        return []
    return TypeAdapter(list[FunctionalObservation]).validate_json(path.read_text(encoding="utf-8"))


def _write_metadata(
    path: Path,
    requested_model: str,
    response_models: set[str],
    fingerprints: set[str],
    observations: list[FunctionalObservation],
    metrics: dict[str, float | int] | None = None,
    quality_gate: dict | None = None,
) -> None:
    value = {
        "requested_model": requested_model,
        "response_models": sorted(response_models),
        "system_fingerprints": sorted(fingerprints),
        "case_count": len(observations),
        "case_ids": [observation.case_id for observation in observations],
    }
    if metrics is not None:
        value["functional_metrics"] = metrics
    if quality_gate is not None:
        value["quality_gate"] = quality_gate
    _write_json(path, value)


def main() -> int:
    asyncio.run(capture(parse_args()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
