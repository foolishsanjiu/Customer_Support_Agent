import argparse
import asyncio
import json
import math
import os
import platform
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from time import perf_counter
from urllib.parse import urlparse

import httpx


@dataclass(frozen=True)
class LoadTestConfig:
    base_url: str
    paths: tuple[str, ...]
    requests: int
    concurrency: int
    warmup_requests: int
    timeout_seconds: float
    expected_statuses: frozenset[int]
    minimum_success_rate: float
    max_p95_ms: float | None

    def validate(self) -> None:
        parsed_url = urlparse(self.base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if not self.paths or any(not path.startswith("/") for path in self.paths):
            raise ValueError("at least one absolute request path is required")
        if len(set(self.paths)) != len(self.paths):
            raise ValueError("request paths must be unique")
        if self.requests < 1:
            raise ValueError("requests must be at least 1")
        if self.requests < len(self.paths):
            raise ValueError("requests must cover every request path")
        if self.concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if self.warmup_requests < 0:
            raise ValueError("warmup_requests cannot be negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not self.expected_statuses or any(
            status < 100 or status > 599 for status in self.expected_statuses
        ):
            raise ValueError("expected_statuses must contain valid HTTP status codes")
        if not 0 <= self.minimum_success_rate <= 1:
            raise ValueError("minimum_success_rate must be between 0 and 1")
        if self.max_p95_ms is not None and self.max_p95_ms <= 0:
            raise ValueError("max_p95_ms must be positive")


@dataclass(frozen=True)
class RequestResult:
    path: str
    latency_ms: float
    status_code: int | None
    successful: bool
    error: str | None = None


async def run_load_test(
    config: LoadTestConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, object]:
    config.validate()
    limits = httpx.Limits(
        max_connections=config.concurrency,
        max_keepalive_connections=config.concurrency,
    )
    async with httpx.AsyncClient(
        base_url=config.base_url,
        timeout=config.timeout_seconds,
        limits=limits,
        transport=transport,
    ) as client:
        for index in range(config.warmup_requests):
            await _request(
                client, config.paths[index % len(config.paths)], config.expected_statuses
            )

        started_at = datetime.now(UTC)
        started = perf_counter()
        worker_results = await asyncio.gather(
            *(_run_worker(client, config, worker_id) for worker_id in range(config.concurrency))
        )
        duration_seconds = perf_counter() - started

    results = [result for batch in worker_results for result in batch]
    return _build_report(config, results, duration_seconds, started_at)


async def _run_worker(
    client: httpx.AsyncClient,
    config: LoadTestConfig,
    worker_id: int,
) -> list[RequestResult]:
    results = []
    for index in range(worker_id, config.requests, config.concurrency):
        path = config.paths[index % len(config.paths)]
        results.append(await _request(client, path, config.expected_statuses))
    return results


async def _request(
    client: httpx.AsyncClient,
    path: str,
    expected_statuses: frozenset[int],
) -> RequestResult:
    started = perf_counter()
    try:
        response = await client.get(path)
    except httpx.HTTPError as exc:
        return RequestResult(
            path=path,
            latency_ms=_elapsed_ms(started),
            status_code=None,
            successful=False,
            error=type(exc).__name__,
        )
    return RequestResult(
        path=path,
        latency_ms=_elapsed_ms(started),
        status_code=response.status_code,
        successful=response.status_code in expected_statuses,
    )


def _build_report(
    config: LoadTestConfig,
    results: list[RequestResult],
    duration_seconds: float,
    started_at: datetime,
) -> dict[str, object]:
    successful = [result for result in results if result.successful]
    latencies = sorted(result.latency_ms for result in successful)
    status_codes = Counter(
        str(result.status_code) for result in results if result.status_code is not None
    )
    errors = Counter(result.error for result in results if result.error is not None)
    success_rate = len(successful) / len(results)
    latency_metrics = {
        "minimum": round(latencies[0], 3) if latencies else None,
        "mean": round(fmean(latencies), 3) if latencies else None,
        "p50": _percentile(latencies, 0.50),
        "p95": _percentile(latencies, 0.95),
        "p99": _percentile(latencies, 0.99),
        "maximum": round(latencies[-1], 3) if latencies else None,
    }
    failures = []
    if success_rate < config.minimum_success_rate:
        failures.append(
            f"success_rate {success_rate:.6f} is below minimum {config.minimum_success_rate:.6f}"
        )
    p95 = latency_metrics["p95"]
    if config.max_p95_ms is not None and (p95 is None or p95 > config.max_p95_ms):
        failures.append(f"p95 latency {p95} ms exceeds maximum {config.max_p95_ms:.3f} ms")

    return {
        "schema_version": "load-test-v1",
        "timestamp": started_at.isoformat(),
        "metadata": {
            "git_commit": _git_commit(),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "logical_cpu_count": os.cpu_count(),
        },
        "target": {"base_url": config.base_url, "paths": list(config.paths)},
        "load": {
            "requests": config.requests,
            "concurrency": config.concurrency,
            "warmup_requests": config.warmup_requests,
            "timeout_seconds": config.timeout_seconds,
        },
        "metrics": {
            "attempted": len(results),
            "successful": len(successful),
            "failed": len(results) - len(successful),
            "success_rate": round(success_rate, 6),
            "duration_seconds": round(duration_seconds, 6),
            "requests_per_second": round(len(results) / duration_seconds, 3),
            "latency_ms": latency_metrics,
            "status_codes": dict(sorted(status_codes.items())),
            "errors": dict(sorted(errors.items())),
            "by_path": {
                path: _summarize_path([result for result in results if result.path == path])
                for path in config.paths
            },
        },
        "gate": {
            "passed": not failures,
            "minimum_success_rate": config.minimum_success_rate,
            "max_p95_ms": config.max_p95_ms,
            "failures": failures,
        },
    }


def _percentile(sorted_values: list[float], quantile: float) -> float | None:
    if not sorted_values:
        return None
    index = max(0, math.ceil(quantile * len(sorted_values)) - 1)
    return round(sorted_values[index], 3)


def _summarize_path(results: list[RequestResult]) -> dict[str, object]:
    successful = [result for result in results if result.successful]
    latencies = sorted(result.latency_ms for result in successful)
    return {
        "attempted": len(results),
        "successful": len(successful),
        "failed": len(results) - len(successful),
        "success_rate": round(len(successful) / len(results), 6),
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
        },
        "status_codes": dict(
            sorted(
                Counter(
                    str(result.status_code) for result in results if result.status_code is not None
                ).items()
            )
        ),
    }


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _elapsed_ms(started: float) -> float:
    return (perf_counter() - started) * 1000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a bounded HTTP load test against ResolveX.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--path", action="append", dest="paths")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--warmup-requests", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--expected-status", type=int, action="append", dest="expected_statuses")
    parser.add_argument("--minimum-success-rate", type=float, default=0.99)
    parser.add_argument("--max-p95-ms", type=float)
    parser.add_argument("--output", type=Path, default=Path("artifacts/load-test.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = LoadTestConfig(
        base_url=args.base_url,
        paths=tuple(args.paths or ["/health/ready"]),
        requests=args.requests,
        concurrency=args.concurrency,
        warmup_requests=args.warmup_requests,
        timeout_seconds=args.timeout_seconds,
        expected_statuses=frozenset(args.expected_statuses or [200]),
        minimum_success_rate=args.minimum_success_rate,
        max_p95_ms=args.max_p95_ms,
    )
    try:
        report = asyncio.run(run_load_test(config))
    except ValueError as exc:
        raise SystemExit(f"Invalid load-test configuration: {exc}") from exc

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    metrics = report["metrics"]
    gate = report["gate"]
    assert isinstance(metrics, dict)
    assert isinstance(gate, dict)
    print(
        f"Load-test gate {'passed' if gate['passed'] else 'failed'}; "
        f"success={metrics['success_rate']:.2%}; "
        f"p95={metrics['latency_ms']['p95']} ms; "
        f"rps={metrics['requests_per_second']}; report={args.output}"
    )
    return 0 if gate["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
