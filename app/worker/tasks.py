import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta

from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from sqlalchemy import select

from app.agent.llm import OpenAICompatibleClient
from app.agent.run_guard import RunAction, RunStateGuard, RunTrigger
from app.agent.runner import AgentRunner
from app.agent.store import DatabaseAgentStore
from app.approvals.service import utc_now_naive
from app.core.config import get_settings
from app.infrastructure.database.session import create_database_engine, create_session_factory
from app.models import AgentRun, Approval, AuditLog
from app.models.enums import AgentRunStatus
from app.worker.celery_app import celery_app


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
    delivery_info = task.request.delivery_info or {}
    trigger = RunTrigger.RECOVERY if delivery_info.get("redelivered") else RunTrigger.START
    _run(
        _with_runtime(
            lambda settings, sessions, saver: _execute(
                run_id, trigger, settings, sessions, saver
            )
        )
    )


async def _start(run_id, settings, sessions, saver) -> None:
    await _execute(run_id, RunTrigger.START, settings, sessions, saver)


async def _execute(run_id, trigger, settings, sessions, saver) -> None:
    config = {"configurable": {"thread_id": str(run_id)}}
    checkpoint_exists = (
        await saver.aget_tuple(config) is not None
        if trigger is RunTrigger.RECOVERY
        else False
    )
    guard = RunStateGuard(
        sessions, max_recovery_attempts=settings.max_recovery_attempts
    )
    action = await guard.acquire(
        run_id, trigger, checkpoint_exists=checkpoint_exists
    )
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


@celery_app.task(name="resolvex.resume_agent_run")
def resume_agent_run(run_id: int, approval_id: int, recovery: bool = False) -> None:
    _run(
        _with_runtime(
            lambda settings, sessions, saver: _resume(
                run_id, approval_id, recovery, settings, sessions, saver
            )
        )
    )


async def _resume(run_id, approval_id, recovery, settings, sessions, saver) -> None:
    config = {"configurable": {"thread_id": str(run_id)}}
    checkpoint_exists = await saver.aget_tuple(config) is not None
    trigger = RunTrigger.RECOVERY if recovery else RunTrigger.APPROVAL_RESUME
    guard = RunStateGuard(
        sessions, max_recovery_attempts=settings.max_recovery_attempts
    )
    action = await guard.acquire(
        run_id, trigger, checkpoint_exists=checkpoint_exists
    )
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
