from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.agent.run_guard import RunAction, RunTrigger
from app.models.enums import ApprovalStatus, DeadLetterStatus
from app.worker import tasks


def worker_settings(**overrides):
    values = {
        "database_url": "mysql://test",
        "langgraph_redis_url": "redis://test/1",
        "llm_api_key": SecretStr("key"),
        "llm_base_url": "https://llm.example",
        "llm_model": "model",
        "max_agent_steps": 12,
        "max_recovery_attempts": 3,
        "external_circuit_failure_threshold": 3,
        "external_circuit_recovery_seconds": 30,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_worker_llm_requires_complete_configuration() -> None:
    first = tasks._llm(worker_settings())
    second = tasks._llm(worker_settings())
    assert first.model == "model"
    assert first.circuit_breaker is second.circuit_breaker
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        tasks._llm(worker_settings(llm_api_key=None))
    with pytest.raises(RuntimeError, match="LLM_BASE_URL"):
        tasks._llm(worker_settings(llm_base_url=None))


def test_celery_task_wrappers_enter_async_runtime(monkeypatch) -> None:
    events: list[object] = []
    monkeypatch.setattr(tasks, "_with_runtime", lambda operation: "wrapped")
    monkeypatch.setattr(tasks, "_reconcile", lambda: "reconcile")
    monkeypatch.setattr(tasks, "_run", lambda value: events.append(value) or 3)
    tasks.run_agent.run(1)
    tasks.resume_agent_run.run(1, 2, True)
    assert tasks.reconcile_resume_pending.run() == 3
    assert events == ["wrapped", "wrapped", "reconcile"]


@pytest.mark.asyncio
async def test_start_guard_controls_new_run(monkeypatch) -> None:
    calls: list[tuple] = []

    class Guard:
        action = RunAction.START

        def __init__(self, sessions, *, max_recovery_attempts):
            pass

        async def acquire(self, run_id, trigger, *, checkpoint_exists):
            assert (run_id, trigger, checkpoint_exists) == (4, RunTrigger.START, False)
            return self.action

    class Store:
        def __init__(self, sessions):
            pass

        async def get_run_context(self, run_id):
            return 8, 9

    class Runner:
        def __init__(self, **values):
            calls.append(("init", values["max_steps"], values["checkpointer"]))

        async def run_existing(self, *values):
            calls.append(("run", *values))

    monkeypatch.setattr(tasks, "RunStateGuard", Guard)
    monkeypatch.setattr(tasks, "DatabaseAgentStore", Store)
    monkeypatch.setattr(tasks, "AgentRunner", Runner)
    monkeypatch.setattr(tasks, "_llm", lambda settings: "llm")
    await tasks._start(4, worker_settings(), "sessions", "saver")
    assert calls[-1] == ("run", 4, 8, 9)

    Guard.action = RunAction.NOOP
    calls.clear()
    await tasks._start(4, worker_settings(), "sessions", "saver")
    assert calls == []


@pytest.mark.asyncio
async def test_redelivery_recovers_existing_checkpoint(monkeypatch) -> None:
    calls: list[tuple] = []

    class Saver:
        async def aget_tuple(self, config):
            return object()

    class Guard:
        def __init__(self, sessions, *, max_recovery_attempts):
            pass

        async def acquire(self, run_id, trigger, *, checkpoint_exists):
            calls.append(("guard", trigger, checkpoint_exists))
            return RunAction.RESUME

    class Runner:
        def __init__(self, **values):
            pass

        async def recover(self, run_id):
            calls.append(("recover", run_id))

    monkeypatch.setattr(tasks, "RunStateGuard", Guard)
    monkeypatch.setattr(tasks, "AgentRunner", Runner)
    monkeypatch.setattr(tasks, "_llm", lambda settings: "llm")
    await tasks._execute(4, RunTrigger.RECOVERY, worker_settings(), "sessions", Saver())
    assert calls == [
        ("guard", RunTrigger.RECOVERY, True),
        ("recover", 4),
    ]


@pytest.mark.asyncio
async def test_exhausted_recovery_is_captured_in_dlq(monkeypatch) -> None:
    calls = []

    class Saver:
        async def aget_tuple(self, config):
            return object()

    class Guard:
        def __init__(self, sessions, *, max_recovery_attempts):
            pass

        async def acquire(self, run_id, trigger, *, checkpoint_exists):
            return RunAction.RECOVERY_REQUIRED

    class Dlq:
        def __init__(self, sessions):
            pass

        async def capture(self, **values):
            calls.append(values)

    monkeypatch.setattr(tasks, "RunStateGuard", Guard)
    monkeypatch.setattr(tasks, "DeadLetterStore", Dlq)

    await tasks._execute(4, RunTrigger.RECOVERY, worker_settings(), "sessions", Saver())

    assert calls == [
        {
            "run_id": 4,
            "task_name": "resolvex.run_agent",
            "reason_code": "checkpoint_recovery_required",
        }
    ]


@pytest.mark.asyncio
async def test_cancel_action_finalizes_without_starting_runner(monkeypatch) -> None:
    calls = []

    class Guard:
        def __init__(self, sessions, *, max_recovery_attempts):
            pass

        async def acquire(self, run_id, trigger, *, checkpoint_exists):
            return RunAction.CANCEL

    class Store:
        def __init__(self, sessions):
            pass

        async def finalize_cancellation(self, run_id):
            calls.append(("cancelled", run_id))

    monkeypatch.setattr(tasks, "RunStateGuard", Guard)
    monkeypatch.setattr(tasks, "DatabaseAgentStore", Store)

    await tasks._execute(4, RunTrigger.START, worker_settings(), "sessions", "saver")

    assert calls == [("cancelled", 4)]


@pytest.mark.asyncio
async def test_resume_uses_checkpoint_and_authoritative_approval(monkeypatch) -> None:
    calls: list[tuple] = []

    class Saver:
        async def aget_tuple(self, config):
            return object()

    class Guard:
        action = RunAction.RESUME

        def __init__(self, sessions, *, max_recovery_attempts):
            pass

        async def acquire(self, run_id, trigger, *, checkpoint_exists):
            calls.append(("guard", trigger, checkpoint_exists))
            return self.action

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, model, approval_id):
            return SimpleNamespace(id=approval_id, run_id=4, status=ApprovalStatus.APPROVED)

    class Sessions:
        def __call__(self):
            return Session()

    class Runner:
        def __init__(self, **values):
            pass

        async def resume(self, run_id, decision):
            calls.append(("resume", run_id, decision))

    monkeypatch.setattr(tasks, "RunStateGuard", Guard)
    monkeypatch.setattr(tasks, "AgentRunner", Runner)
    monkeypatch.setattr(tasks, "_llm", lambda settings: "llm")
    await tasks._resume(4, 7, False, worker_settings(), Sessions(), Saver())
    assert calls == [
        ("guard", RunTrigger.APPROVAL_RESUME, True),
        ("resume", 4, {"approval_id": 7, "status": "APPROVED"}),
    ]

    Guard.action = RunAction.NOOP
    calls.clear()
    await tasks._resume(4, 7, True, worker_settings(), Sessions(), Saver())
    assert calls == [("guard", RunTrigger.RECOVERY, True)]


@pytest.mark.asyncio
async def test_runtime_factory_disposes_engine(monkeypatch) -> None:
    events: list[str] = []

    class Engine:
        async def dispose(self):
            events.append("disposed")

    class SaverContext:
        async def __aenter__(self):
            return "saver"

        async def __aexit__(self, *args):
            pass

    class SaverFactory:
        @staticmethod
        def from_conn_string(url):
            return SaverContext()

    monkeypatch.setattr(tasks, "get_settings", worker_settings)
    monkeypatch.setattr(tasks, "create_database_engine", lambda url: Engine())
    monkeypatch.setattr(tasks, "create_session_factory", lambda engine: "sessions")
    monkeypatch.setattr(tasks, "AsyncRedisSaver", SaverFactory)

    async def operation(settings, sessions, saver):
        assert (sessions, saver) == ("sessions", "saver")
        return 6

    assert await tasks._with_runtime(operation) == 6
    assert events == ["disposed"]

    monkeypatch.setattr(tasks, "get_settings", lambda: worker_settings(database_url=None))
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        await tasks._with_runtime(operation)


@pytest.mark.asyncio
async def test_dlq_replay_uses_manual_trigger_and_marks_success(monkeypatch) -> None:
    events = []
    letter = SimpleNamespace(
        id=3,
        run_id=4,
        approval_id=None,
        task_name=tasks.RUN_AGENT_TASK,
        status=DeadLetterStatus.REPLAYING,
    )

    class Store:
        def __init__(self, sessions):
            pass

        async def get(self, dead_letter_id):
            return letter

        async def mark_replayed(self, dead_letter_id):
            events.append(("replayed", dead_letter_id))

        async def capture(self, **values):
            events.append(("capture", values))

    async def execute(run_id, trigger, settings, sessions, saver):
        events.append(("execute", run_id, trigger))

    monkeypatch.setattr(tasks, "DeadLetterStore", Store)
    monkeypatch.setattr(tasks, "_execute", execute)

    await tasks._replay_dead_letter(3, "replay-task", worker_settings(), "sessions", "saver")

    assert events == [
        ("execute", 4, RunTrigger.DLQ_REPLAY),
        ("replayed", 3),
    ]


@pytest.mark.asyncio
async def test_failed_dlq_replay_reopens_same_record(monkeypatch) -> None:
    captured = []
    letter = SimpleNamespace(
        id=3,
        run_id=4,
        approval_id=None,
        task_name=tasks.RUN_AGENT_TASK,
        status=DeadLetterStatus.REPLAYING,
    )

    class Store:
        def __init__(self, sessions):
            pass

        async def get(self, dead_letter_id):
            return letter

        async def capture(self, **values):
            captured.append(values)

    async def execute(*args):
        raise RuntimeError("failed again")

    monkeypatch.setattr(tasks, "DeadLetterStore", Store)
    monkeypatch.setattr(tasks, "_execute", execute)

    with pytest.raises(RuntimeError, match="failed again"):
        await tasks._replay_dead_letter(
            3,
            "replay-task",
            worker_settings(),
            "sessions",
            "saver",
        )

    assert captured == [
        {
            "run_id": 4,
            "approval_id": None,
            "task_name": tasks.RUN_AGENT_TASK,
            "task_id": "replay-task",
            "reason_code": "replay_failed",
            "error_type": "RuntimeError",
            "mark_run_failed": True,
        }
    ]


@pytest.mark.asyncio
async def test_reconciler_audits_and_requeues_stale_runs(monkeypatch) -> None:
    events: list[tuple] = []
    run = SimpleNamespace(id=4, ticket_id=8)
    approval = SimpleNamespace(id=7, tool_call_id="call-1")

    class Result:
        def all(self):
            return [(run, approval)]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, statement):
            return Result()

        def add(self, audit):
            events.append((audit.event_type, audit.run_id, audit.approval_id))

    class Sessions:
        def begin(self):
            return Session()

    class Engine:
        async def dispose(self):
            events.append(("disposed",))

    class ResumeTask:
        def delay(self, *values):
            events.append(("delay", *values))

    monkeypatch.setattr(tasks, "get_settings", worker_settings)
    monkeypatch.setattr(tasks, "create_database_engine", lambda url: Engine())
    monkeypatch.setattr(tasks, "create_session_factory", lambda engine: Sessions())
    monkeypatch.setattr(tasks, "resume_agent_run", ResumeTask())
    assert await tasks._reconcile() == 1
    assert ("resume_reconciled", 4, 7) in events
    assert ("delay", 4, 7, True) in events
    assert events[-1] == ("disposed",)

    monkeypatch.setattr(tasks, "get_settings", lambda: worker_settings(database_url=None))
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        await tasks._reconcile()


@pytest.mark.asyncio
async def test_pending_reconciler_requeues_stale_starts(monkeypatch) -> None:
    events: list[tuple] = []
    run = SimpleNamespace(id=4, ticket_id=8)

    class Scalars:
        def all(self):
            return [run]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def scalars(self, statement):
            return Scalars()

        def add(self, audit):
            events.append((audit.event_type, audit.run_id))

    class Sessions:
        def begin(self):
            return Session()

    class Engine:
        async def dispose(self):
            events.append(("disposed",))

    class RunTask:
        def delay(self, run_id):
            events.append(("delay", run_id))

    monkeypatch.setattr(tasks, "get_settings", worker_settings)
    monkeypatch.setattr(tasks, "create_database_engine", lambda url: Engine())
    monkeypatch.setattr(tasks, "create_session_factory", lambda engine: Sessions())
    monkeypatch.setattr(tasks, "run_agent", RunTask())
    assert await tasks._reconcile_pending() == 1
    assert ("pending_run_reconciled", 4) in events
    assert ("delay", 4) in events
    assert events[-1] == ("disposed",)
