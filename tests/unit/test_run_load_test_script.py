from datetime import UTC, datetime

import httpx
import pytest

from scripts.run_load_test import (
    LoadTestConfig,
    RequestResult,
    _build_report,
    _percentile,
    run_load_test,
)


def _config(**overrides) -> LoadTestConfig:
    values = {
        "base_url": "https://resolvex.test",
        "paths": ("/health/ready", "/api/v1/orders/1"),
        "requests": 6,
        "concurrency": 2,
        "warmup_requests": 0,
        "timeout_seconds": 1.0,
        "expected_statuses": frozenset({200}),
        "minimum_success_rate": 0.99,
        "max_p95_ms": None,
    }
    values.update(overrides)
    return LoadTestConfig(**values)


@pytest.mark.asyncio
async def test_load_test_reports_round_robin_requests_and_latency_gate() -> None:
    requested_paths = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    report = await run_load_test(
        _config(max_p95_ms=100),
        transport=httpx.MockTransport(handler),
    )

    assert requested_paths.count("/health/ready") == 3
    assert requested_paths.count("/api/v1/orders/1") == 3
    assert report["metrics"]["attempted"] == 6
    assert report["metrics"]["success_rate"] == 1.0
    assert report["metrics"]["status_codes"] == {"200": 6}
    assert report["metrics"]["by_path"]["/health/ready"]["attempted"] == 3
    assert report["metrics"]["by_path"]["/api/v1/orders/1"]["attempted"] == 3
    assert report["gate"]["passed"] is True


@pytest.mark.asyncio
async def test_load_test_fails_when_success_rate_is_below_floor() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503 if calls == 1 else 200)

    report = await run_load_test(
        _config(requests=4, paths=("/health/ready",)),
        transport=httpx.MockTransport(handler),
    )

    assert report["metrics"]["successful"] == 3
    assert report["metrics"]["failed"] == 1
    assert report["metrics"]["status_codes"] == {"200": 3, "503": 1}
    assert report["gate"]["passed"] is False
    assert report["gate"]["failures"] == ["success_rate 0.750000 is below minimum 0.990000"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"base_url": "localhost:8000"},
        {"paths": ()},
        {"paths": ("/same", "/same")},
        {"requests": 0},
        {"paths": ("/one", "/two"), "requests": 1},
        {"concurrency": 0},
        {"warmup_requests": -1},
        {"timeout_seconds": 0},
        {"expected_statuses": frozenset()},
        {"minimum_success_rate": 1.01},
        {"max_p95_ms": 0},
    ],
)
def test_load_test_rejects_invalid_configuration(overrides) -> None:
    with pytest.raises(ValueError):
        _config(**overrides).validate()


def test_percentile_uses_nearest_rank() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]

    assert _percentile(values, 0.5) == 3.0
    assert _percentile(values, 0.95) == 5.0
    assert _percentile([], 0.95) is None


def test_load_test_applies_configured_p95_gate() -> None:
    config = _config(
        paths=("/health/ready",),
        requests=2,
        max_p95_ms=10,
    )
    results = [
        RequestResult(path="/health/ready", latency_ms=5, status_code=200, successful=True),
        RequestResult(path="/health/ready", latency_ms=20, status_code=200, successful=True),
    ]

    report = _build_report(config, results, 0.1, datetime.now(UTC))

    assert report["metrics"]["latency_ms"]["p95"] == 20
    assert report["gate"]["passed"] is False
    assert report["gate"]["failures"] == ["p95 latency 20 ms exceeds maximum 10.000 ms"]
