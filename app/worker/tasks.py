import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta

from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from sqlalchemy import select
from structlog.contextvars import bind_contextvars

from app.agent.llm import OpenAICompatibleClient
from app.agent.run_guard import RunAction, RunStateGuard, RunTrigger
from app.agent.runner import AgentRunner
from app.agent.store import DatabaseAgentStore
from app.approvals.service import utc_now_naive
from app.core.config import get_settings
from app.dlq import DeadLetterStore
from app.infrastructure.database.session import create_database_engine, create_session_factory
from app.models import AgentRun, Approval, AuditLog
from app.models.enums import AgentRunStatus, DeadLetterStatus
from app.observability import get_logger
from app.resilience import shared_circuit_breaker
from app.worker.celery_app import celery_app

RUN_AGENT_TASK = "resolvex.run_agent"
RESUME_AGENT_TASK = "resolvex.resume_agent_run"
logger = get_logger(__name__)


def _run[T](awaitable: Awaitable[T]) -> T:
    return asyncio.run(awaitable)


def _llm(settings) -> OpenAICompatibleClient:
    if settings.llm_api_key is None or not settings.llm_api_key.get_secret_value():
        raise RuntimeError("LLM_API_KEY is not configured")
    if not settings.llm_base_url or not settings.llm_model:
        raise RuntimeError("LLM_BASE_URL and LLM_MODEL are required")
    return OpenAICompatibleClient(
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        circuit_breaker=shared_circuit_breaker(
            "llm",
            settings.external_circuit_failure_threshold,
            settings.external_circuit_recovery_seconds,
        ),
    )


async def _with_runtime[T](operation: Callable[..., Awaitable[T]]) -> T:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    try:
        async with AsyncRedisSaver.from_conn_string(settings.langgraph_redis_url) as saver:
            return await operation(settings, session_factory, saver)
    finally:
        await engine.dispose()


@celery_app.task(bind=True, name="resolvex.run_agent")
def run_agent(task, run_id: int) -> None:
    bind_contextvars(run_id=run_id)
    delivery_info = task.request.delivery_info or {}
    trigger = RunTrigger.RECOVERY if delivery_info.get("redelivered") else RunTrigger.START
    try:
        _run(
            _with_runtime(
                lambda settings, sessions, saver: _execute(
                    run_id, trigger, settings, sessions, saver
                )
            )
        )
    except Exception as exc:
        _capture_task_failure_safely(
            run_id=run_id,
            task_name=RUN_AGENT_TASK,
            task_id=task.request.id,
            error=exc,
        )
        raise


async def _start(run_id, settings, sessions, saver) -> None:
    await _execute(run_id, RunTrigger.START, settings, sessions, saver)


async def _execute(run_id, trigger, settings, sessions, saver) -> None:
    config = {"configurable": {"thread_id": str(run_id)}}
    checkpoint_exists = (
        await saver.aget_tuple(config) is not None
        if trigger in {RunTrigger.RECOVERY, RunTrigger.DLQ_REPLAY}
        else False
    )
    guard = RunStateGuard(sessions, max_recovery_attempts=settings.max_recovery_attempts)
    action = await guard.acquire(run_id, trigger, checkpoint_exists=checkpoint_exists)
    if action is RunAction.RECOVERY_REQUIRED:
        await DeadLetterStore(sessions).capture(
            run_id=run_id,
            task_name=RUN_AGENT_TASK,
            reason_code="checkpoint_recovery_required",
        )
        return
    if action not in {RunAction.START, RunAction.RESUME}:
        return
    runner = AgentRunner(
        session_factory=sessions,
        llm=_llm(settings),
        max_steps=settings.max_agent_steps,
        checkpointer=saver,
    )
    if action is RunAction.RESUME:
        await runner.recover(run_id)
        return
    ticket_id, customer_id = await DatabaseAgentStore(sessions).get_run_context(run_id)
    await runner.run_existing(run_id, ticket_id, customer_id)


@celery_app.task(bind=True, name="resolvex.resume_agent_run")
def resume_agent_run(task, run_id: int, approval_id: int, recovery: bool = False) -> None:
    bind_contextvars(run_id=run_id, approval_id=approval_id)
    try:
        _run(
            _with_runtime(
                lambda settings, sessions, saver: _resume(
                    run_id, approval_id, recovery, settings, sessions, saver
                )
            )
        )
    except Exception as exc:
        _capture_task_failure_safely(
            run_id=run_id,
            task_name=RESUME_AGENT_TASK,
            task_id=task.request.id,
            approval_id=approval_id,
            error=exc,
        )
        raise


async def _resume(
    run_id,
    approval_id,
    recovery,
    settings,
    sessions,
    saver,
    *,
    trigger_override=None,
) -> None:
    config = {"configurable": {"thread_id": str(run_id)}}
    checkpoint_exists = await saver.aget_tuple(config) is not None
    trigger = trigger_override or (RunTrigger.RECOVERY if recovery else RunTrigger.APPROVAL_RESUME)
    guard = RunStateGuard(sessions, max_recovery_attempts=settings.max_recovery_attempts)
    action = await guard.acquire(run_id, trigger, checkpoint_exists=checkpoint_exists)
    if action is RunAction.RECOVERY_REQUIRED:
        await DeadLetterStore(sessions).capture(
            run_id=run_id,
            approval_id=approval_id,
            task_name=RESUME_AGENT_TASK,
            reason_code="checkpoint_recovery_required",
        )
        return
    if action is not RunAction.RESUME:
        return
    async with sessions() as session:
        approval = await session.get(Approval, approval_id)
        if approval is None or approval.run_id != run_id:
            raise RuntimeError("approval does not match agent run")
        decision = {"approval_id": approval.id, "status": approval.status.value}
    runner = AgentRunner(
        session_factory=sessions,
        llm=_llm(settings),
        max_steps=settings.max_agent_steps,
        checkpointer=saver,
    )
    await runner.resume(run_id, decision)


@celery_app.task(bind=True, name="resolvex.replay_dead_letter")
def replay_dead_letter(task, dead_letter_id: int) -> None:
    bind_contextvars(dead_letter_id=dead_letter_id)
    try:
        _run(
            _with_runtime(
                lambda settings, sessions, saver: _replay_dead_letter(
                    dead_letter_id,
                    task.request.id,
                    settings,
                    sessions,
                    saver,
                )
            )
        )
    except Exception as exc:
        _reopen_replay_safely(dead_letter_id, type(exc).__name__)
        raise


async def _replay_dead_letter(dead_letter_id, task_id, settings, sessions, saver) -> None:
    store = DeadLetterStore(sessions)
    record = await store.get(dead_letter_id)
    if record.status is not DeadLetterStatus.REPLAYING:
        return
    try:
        if record.task_name == RUN_AGENT_TASK:
            await _execute(
                record.run_id,
                RunTrigger.DLQ_REPLAY,
                settings,
                sessions,
                saver,
            )
        elif record.task_name == RESUME_AGENT_TASK and record.approval_id is not None:
            await _resume(
                record.run_id,
                record.approval_id,
                True,
                settings,
                sessions,
                saver,
                trigger_override=RunTrigger.DLQ_REPLAY,
            )
        else:
            await store.reopen(dead_letter_id, reason_code="invalid_dead_letter_payload")
            return
    except Exception as exc:
        await store.capture(
            run_id=record.run_id,
            approval_id=record.approval_id,
            task_name=record.task_name,
            task_id=task_id,
            reason_code="replay_failed",
            error_type=type(exc).__name__,
            mark_run_failed=True,
        )
        raise
    await store.mark_replayed(dead_letter_id)


def _capture_task_failure_safely(
    *,
    run_id: int,
    task_name: str,
    task_id: str | None,
    error: Exception,
    approval_id: int | None = None,
) -> None:
    try:
        _run(
            _capture_task_failure(
                run_id=run_id,
                task_name=task_name,
                task_id=task_id,
                approval_id=approval_id,
                error_type=type(error).__name__,
            )
        )
    except Exception:
        logger.exception(
            "dead_letter_capture_failed",
            run_id=run_id,
            task_name=task_name,
            error_type=type(error).__name__,
        )


async def _capture_task_failure(
    *,
    run_id: int,
    task_name: str,
    task_id: str | None,
    approval_id: int | None,
    error_type: str,
) -> None:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    engine = create_database_engine(settings.database_url)
    sessions = create_session_factory(engine)
    try:
        await DeadLetterStore(sessions).capture(
            run_id=run_id,
            approval_id=approval_id,
            task_name=task_name,
            task_id=task_id,
            reason_code="task_exception",
            error_type=error_type,
            mark_run_failed=True,
        )
    finally:
        await engine.dispose()


def _reopen_replay_safely(dead_letter_id: int, error_type: str) -> None:
    try:
        _run(_reopen_replay(dead_letter_id))
    except Exception:
        logger.exception(
            "dead_letter_reopen_failed",
            dead_letter_id=dead_letter_id,
            error_type=error_type,
        )


async def _reopen_replay(dead_letter_id: int) -> None:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    engine = create_database_engine(settings.database_url)
    sessions = create_session_factory(engine)
    try:
        await DeadLetterStore(sessions).reopen(
            dead_letter_id,
            reason_code="replay_task_failed",
        )
    finally:
        await engine.dispose()


@celery_app.task(name="resolvex.reconcile_resume_pending")
def reconcile_resume_pending() -> int:
    return _run(_reconcile())


async def _reconcile() -> int:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    engine = create_database_engine(settings.database_url)
    sessions = create_session_factory(engine)
    try:
        cutoff = utc_now_naive() - timedelta(seconds=60)
        async with sessions.begin() as session:
            statement = (
                select(AgentRun, Approval)
                .join(Approval, Approval.run_id == AgentRun.id)
                .where(
                    AgentRun.status == AgentRunStatus.RESUME_PENDING,
                    AgentRun.updated_at <= cutoff,
                )
                .order_by(AgentRun.updated_at)
                .limit(100)
            )
            rows = list((await session.execute(statement)).all())
            for run, approval in rows:
                session.add(
                    AuditLog(
                        event_type="resume_reconciled",
                        run_id=run.id,
                        ticket_id=run.ticket_id,
                        approval_id=approval.id,
                        tool_call_id=approval.tool_call_id,
                        details={},
                    )
                )
        for run, approval in rows:
            resume_agent_run.delay(run.id, approval.id, True)
        return len(rows)
    finally:
        await engine.dispose()


@celery_app.task(name="resolvex.reconcile_pending_runs")
def reconcile_pending_runs() -> int:
    return _run(_reconcile_pending())


async def _reconcile_pending() -> int:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    engine = create_database_engine(settings.database_url)
    sessions = create_session_factory(engine)
    try:
        cutoff = utc_now_naive() - timedelta(seconds=60)
        async with sessions.begin() as session:
            statement = (
                select(AgentRun)
                .where(
                    AgentRun.status == AgentRunStatus.PENDING,
                    AgentRun.updated_at <= cutoff,
                )
                .order_by(AgentRun.updated_at)
                .limit(100)
            )
            runs = list((await session.scalars(statement)).all())
            for run in runs:
                session.add(
                    AuditLog(
                        event_type="pending_run_reconciled",
                        run_id=run.id,
                        ticket_id=run.ticket_id,
                        details={},
                    )
                )
        for run in runs:
            run_agent.delay(run.id)
        return len(runs)
    finally:
        await engine.dispose()
