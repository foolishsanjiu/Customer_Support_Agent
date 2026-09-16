from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from threading import Lock
from time import monotonic

from app.observability.metrics import record_circuit_state


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpen(RuntimeError):
    def __init__(self, dependency: str, retry_after_seconds: float) -> None:
        self.dependency = dependency
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"{dependency} circuit is open; retry after {retry_after_seconds:.3f} seconds"
        )


@dataclass(frozen=True)
class CircuitPermit:
    generation: int


@dataclass(frozen=True)
class CircuitSnapshot:
    state: CircuitState
    consecutive_failures: int
    retry_after_seconds: float


class CircuitBreaker:
    def __init__(
        self,
        dependency: str,
        *,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 30,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if recovery_timeout_seconds <= 0:
            raise ValueError("recovery_timeout_seconds must be positive")
        self.dependency = dependency
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.clock = clock
        self._lock = Lock()
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._generation = 0
        record_circuit_state(self.dependency, self._state.value)

    def acquire(self) -> CircuitPermit:
        with self._lock:
            if self._state is CircuitState.CLOSED:
                return CircuitPermit(self._generation)
            retry_after = self._retry_after()
            if self._state is CircuitState.OPEN and retry_after <= 0:
                self._state = CircuitState.HALF_OPEN
                record_circuit_state(self.dependency, self._state.value)
                return CircuitPermit(self._generation)
            raise CircuitBreakerOpen(self.dependency, retry_after)

    def record_success(self, permit: CircuitPermit) -> None:
        with self._lock:
            if permit.generation != self._generation:
                return
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._opened_at = None
            self._generation += 1
            record_circuit_state(self.dependency, self._state.value)

    def record_failure(self, permit: CircuitPermit) -> None:
        with self._lock:
            if permit.generation != self._generation:
                return
            if self._state is CircuitState.HALF_OPEN:
                self._open()
                return
            if self._state is not CircuitState.CLOSED:
                return
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failure_threshold:
                self._open()

    def snapshot(self) -> CircuitSnapshot:
        with self._lock:
            return CircuitSnapshot(
                state=self._state,
                consecutive_failures=self._consecutive_failures,
                retry_after_seconds=self._retry_after(),
            )

    def _open(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = self.clock()
        self._generation += 1
        record_circuit_state(self.dependency, self._state.value)

    def _retry_after(self) -> float:
        if self._state is not CircuitState.OPEN or self._opened_at is None:
            return 0
        return max(0, self.recovery_timeout_seconds - (self.clock() - self._opened_at))


@lru_cache
def shared_circuit_breaker(
    dependency: str,
    failure_threshold: int,
    recovery_timeout_seconds: float,
) -> CircuitBreaker:
    return CircuitBreaker(
        dependency,
        failure_threshold=failure_threshold,
        recovery_timeout_seconds=recovery_timeout_seconds,
    )
