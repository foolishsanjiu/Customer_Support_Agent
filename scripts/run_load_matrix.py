import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from statistics import median

import httpx

from scripts.run_load_test import LoadTestConfig, run_load_test


@dataclass(frozen=True)
class LoadProfile:
    name: str
    paths: tuple[str, ...]
    requests: int
    concurrency: int


async def run_load_matrix(
    *,
    base_url: str,
    repeats: int,
    ready_requests: int,
    business_requests: int,
    warmup_requests: int,
    timeout_seconds: float,
    minimum_success_rate: float,
    environment_label: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, object]:
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    profiles = (
        LoadProfile("readiness-c10", ("/health/ready",), ready_requests, 10),
        LoadProfile("business-reads-c1", _business_paths(), business_requests, 1),
        LoadProfile("business-reads-c10", _business_paths(), business_requests, 10),
        LoadProfile("business-reads-c25", _business_paths(), business_requests, 25),
    )

    profile_reports = []
    for profile in profiles:
        runs = []
        for _ in range(repeats):
            config = LoadTestConfig(
                base_url=base_url,
                paths=profile.paths,
                requests=profile.requests,
                concurrency=profile.concurrency,
                warmup_requests=warmup_requests,
                timeout_seconds=timeout_seconds,
                expected_statuses=frozenset({200}),
                minimum_success_rate=minimum_success_rate,
                max_p95_ms=None,
            )
            runs.append(await run_load_test(config, transport=transport))
        profile_reports.append(
            {
                "name": profile.name,
                "configuration": {
                    "paths": list(profile.paths),
                    "requests": profile.requests,
                    "concurrency": profile.concurrency,
                },
                "aggregate": _aggregate(runs),
                "runs": runs,
            }
        )

    failures = [
        profile["name"]
        for profile in profile_reports
        if not profile["aggregate"]["all_runs_passed"]
    ]
    return {
        "schema_version": "load-matrix-v1",
        "environment_label": environment_label,
        "repeats": repeats,
        "profiles": profile_reports,
        "gate": {
            "passed": not failures,
            "minimum_success_rate": minimum_success_rate,
            "failed_profiles": failures,
        },
    }


def _business_paths() -> tuple[str, ...]:
    return ("/api/v1/orders/1", "/api/v1/customers/1/orders")


def _aggregate(runs: list[dict[str, object]]) -> dict[str, float | bool]:
    metrics = [run["metrics"] for run in runs]
    gates = [run["gate"] for run in runs]
    return {
        "all_runs_passed": all(gate["passed"] for gate in gates),
        "minimum_success_rate": min(metric["success_rate"] for metric in metrics),
        "median_requests_per_second": round(
            median(metric["requests_per_second"] for metric in metrics), 3
        ),
        "minimum_requests_per_second": min(metric["requests_per_second"] for metric in metrics),
        "median_p50_ms": round(median(metric["latency_ms"]["p50"] for metric in metrics), 3),
        "median_p95_ms": round(median(metric["latency_ms"]["p95"] for metric in metrics), 3),
        "maximum_p95_ms": max(metric["latency_ms"]["p95"] for metric in metrics),
        "median_p99_ms": round(median(metric["latency_ms"]["p99"] for metric in metrics), 3),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the repeatable ResolveX read-performance matrix."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--ready-requests", type=int, default=1000)
    parser.add_argument("--business-requests", type=int, default=1000)
    parser.add_argument("--warmup-requests", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--minimum-success-rate", type=float, default=0.99)
    parser.add_argument("--environment-label", default="local-docker-compose")
    parser.add_argument("--output", type=Path, default=Path("artifacts/load-matrix.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = asyncio.run(
            run_load_matrix(
                base_url=args.base_url,
                repeats=args.repeats,
                ready_requests=args.ready_requests,
                business_requests=args.business_requests,
                warmup_requests=args.warmup_requests,
                timeout_seconds=args.timeout_seconds,
                minimum_success_rate=args.minimum_success_rate,
                environment_label=args.environment_label,
            )
        )
    except ValueError as exc:
        raise SystemExit(f"Invalid load-matrix configuration: {exc}") from exc

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for profile in report["profiles"]:
        aggregate = profile["aggregate"]
        print(
            f"{profile['name']}: success>={aggregate['minimum_success_rate']:.2%}; "
            f"median_p95={aggregate['median_p95_ms']} ms; "
            f"median_rps={aggregate['median_requests_per_second']}"
        )
    print(
        f"Load-matrix gate {'passed' if report['gate']['passed'] else 'failed'}; "
        f"report={args.output}"
    )
    return 0 if report["gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
