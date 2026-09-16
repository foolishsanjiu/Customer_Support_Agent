import pytest

from app.resilience import CircuitBreaker, CircuitBreakerOpen, CircuitState


def test_circuit_opens_after_threshold_and_allows_one_recovery_probe() -> None:
    now = [100.0]
    breaker = CircuitBreaker(
        "dependency",
        failure_threshold=2,
        recovery_timeout_seconds=10,
        clock=lambda: now[0],
    )

    breaker.record_failure(breaker.acquire())
    breaker.record_failure(breaker.acquire())

    assert breaker.snapshot().state is CircuitState.OPEN
    with pytest.raises(CircuitBreakerOpen) as blocked:
        breaker.acquire()
    assert blocked.value.retry_after_seconds == 10

    now[0] += 10
    probe = breaker.acquire()
    assert breaker.snapshot().state is CircuitState.HALF_OPEN
    with pytest.raises(CircuitBreakerOpen):
        breaker.acquire()

    breaker.record_success(probe)

    assert breaker.snapshot().state is CircuitState.CLOSED
    assert breaker.snapshot().consecutive_failures == 0


def test_failed_recovery_probe_reopens_for_full_cooldown() -> None:
    now = [0.0]
    breaker = CircuitBreaker(
        "dependency",
        failure_threshold=1,
        recovery_timeout_seconds=5,
        clock=lambda: now[0],
    )
    breaker.record_failure(breaker.acquire())
    now[0] = 5

    breaker.record_failure(breaker.acquire())

    assert breaker.snapshot().state is CircuitState.OPEN
    assert breaker.snapshot().retry_after_seconds == 5


def test_stale_success_cannot_close_an_open_circuit() -> None:
    breaker = CircuitBreaker("dependency", failure_threshold=1)
    failing_call = breaker.acquire()
    stale_success = breaker.acquire()

    breaker.record_failure(failing_call)
    breaker.record_success(stale_success)

    assert breaker.snapshot().state is CircuitState.OPEN


@pytest.mark.parametrize(
    ("failure_threshold", "recovery_seconds"),
    [(0, 1), (1, 0)],
)
def test_circuit_rejects_invalid_configuration(
    failure_threshold: int, recovery_seconds: float
) -> None:
    with pytest.raises(ValueError):
        CircuitBreaker(
            "dependency",
            failure_threshold=failure_threshold,
            recovery_timeout_seconds=recovery_seconds,
        )
