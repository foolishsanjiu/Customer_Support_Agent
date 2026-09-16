import httpx
import pytest

from scripts.run_load_matrix import run_load_matrix


@pytest.mark.asyncio
async def test_load_matrix_runs_each_profile_and_aggregates_medians() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    report = await run_load_matrix(
        base_url="https://resolvex.test",
        repeats=2,
        ready_requests=4,
        business_requests=4,
        warmup_requests=0,
        timeout_seconds=1,
        minimum_success_rate=0.99,
        environment_label="test",
        transport=httpx.MockTransport(handler),
    )

    assert calls == 32
    assert [profile["name"] for profile in report["profiles"]] == [
        "readiness-c10",
        "business-reads-c1",
        "business-reads-c10",
        "business-reads-c25",
    ]
    assert all(
        profile["aggregate"]["minimum_success_rate"] == 1.0 for profile in report["profiles"]
    )
    assert report["gate"]["passed"] is True


@pytest.mark.asyncio
async def test_load_matrix_rejects_zero_repeats() -> None:
    with pytest.raises(ValueError, match="repeats must be at least 1"):
        await run_load_matrix(
            base_url="https://resolvex.test",
            repeats=0,
            ready_requests=4,
            business_requests=4,
            warmup_requests=0,
            timeout_seconds=1,
            minimum_success_rate=0.99,
            environment_label="test",
        )
