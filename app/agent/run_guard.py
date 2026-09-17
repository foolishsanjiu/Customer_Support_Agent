from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ResourceNotFound
from app.models import AgentRun, AuditLog
from app.models.enums import AgentRunStatus
from app.observability.tracing import current_trace_id


class RunTrigger(StrEnum):
    START = "START"
    APPROVAL_RESUME = "APPROVAL_RESUME"
    RECOVERY = "RECOVERY"
    DLQ_REPLAY = "DLQ_REPLAY"


class RunAction(StrEnum):
    START = "START"
    RESUME = "RESUME"
    NOOP = "NOOP"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    CANCEL = "CANCEL"


TERMINAL_STATUSES = {
    AgentRunStatus.SUCCEEDED,
    AgentRunStatus.FAILED,
    AgentRunStatus.CANCELLED,
}


def decide_run_action(
    status: AgentRunStatus,
    trigger: RunTrigger,
    *,
    checkpoint_exists: bool,
    recovery_attempts: int,
    max_recovery_attempts: int,
    current_node: str | None = None,
) -> RunAction:
    if status is AgentRunStatus.CANCEL_REQUESTED:
        return RunAction.CANCEL
    if trigger is RunTrigger.DLQ_REPLAY:
        if status is AgentRunStatus.PENDING:
            return RunAction.START
        if status not in {AgentRunStatus.FAILED, AgentRunStatus.RECOVERY_REQUIRED}:
            return RunAction.NOOP
        if checkpoint_exists:
            return RunAction.RESUME
        return (
            RunAction.START
            if status is AgentRunStatus.FAILED and current_node in {None, "START"}
            else RunAction.RECOVERY_REQUIRED
        )
    if status in TERMINAL_STATUSES or status is AgentRunStatus.WAITING_APPROVAL:
        return RunAction.NOOP
    if trigger is RunTrigger.START:
        return RunAction.START if status is AgentRunStatus.PENDING else RunAction.NOOP
    if trigger is RunTrigger.APPROVAL_RESUME:
        if status is not AgentRunStatus.RESUME_PENDING:
            return RunAction.NOOP
        return RunAction.RESUME if checkpoint_exists else RunAction.RECOVERY_REQUIRED
    if recovery_attempts >= max_recovery_attempts:
        return RunAction.RECOVERY_REQUIRED
    if status is AgentRunStatus.PENDING:
        return RunAction.START
    if status in {AgentRunStatus.RUNNING, AgentRunStatus.RESUME_PENDING}:
        return RunAction.RESUME if checkpoint_exists else RunAction.RECOVERY_REQUIRED
    return RunAction.NOOP


class RunStateGuard:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        max_recovery_attempts: int,
    ) -> None:
        self.session_factory = session_factory
        self.max_recovery_attempts = max_recovery_attempts

    async def acquire(
        self,
        run_id: int,
        trigger: RunTrigger,
        *,
        checkpoint_exists: bool,
    ) -> RunAction:
        async with self.session_factory.begin() as session:
            run = await session.get(AgentRun, run_id, with_for_update=True)
            if run is None:
                raise ResourceNotFound("agent run not found")
            action = decide_run_action(
                run.status,
                trigger,
                checkpoint_exists=checkpoint_exists,
                recovery_attempts=run.recovery_attempts,
                max_recovery_attempts=self.max_recovery_attempts,
                current_node=run.current_node,
            )
            if trigger is RunTrigger.RECOVERY and action in {
                RunAction.START,
                RunAction.RESUME,
            }:
                run.recovery_attempts += 1
            if action in {RunAction.START, RunAction.RESUME}:
                run.status = AgentRunStatus.RUNNING
            elif action is RunAction.RECOVERY_REQUIRED:
                run.status = AgentRunStatus.RECOVERY_REQUIRED
                run.error_code = "checkpoint_recovery_required"
            session.add(
                AuditLog(
                    event_type="agent_run_guard",
                    run_id=run.id,
                    ticket_id=run.ticket_id,
                    trace_id=current_trace_id(),
                    details={"trigger": trigger.value, "action": action.value},
                )
            )
            return action
