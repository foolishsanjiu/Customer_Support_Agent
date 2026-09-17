from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import BusinessConflict, ResourceNotFound
from app.models import AgentRun, AuditLog, DeadLetter
from app.models.enums import AgentRunStatus, DeadLetterStatus, PrincipalRole

TERMINAL_RUN_STATUSES = {
    AgentRunStatus.SUCCEEDED,
    AgentRunStatus.FAILED,
    AgentRunStatus.CANCEL_REQUESTED,
    AgentRunStatus.CANCELLED,
}


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class DeadLetterStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def capture(
        self,
        *,
        run_id: int,
        task_name: str,
        reason_code: str,
        error_type: str | None = None,
        task_id: str | None = None,
        approval_id: int | None = None,
        mark_run_failed: bool = False,
    ) -> DeadLetter:
        async with self.session_factory.begin() as session:
            run = await session.get(AgentRun, run_id, with_for_update=True)
            if run is None:
                raise ResourceNotFound("agent run not found")
            record = await session.scalar(
                select(DeadLetter)
                .where(DeadLetter.run_id == run_id, DeadLetter.task_name == task_name)
                .with_for_update()
            )
            if record is None:
                record = DeadLetter(
                    run_id=run_id,
                    approval_id=approval_id,
                    task_name=task_name,
                    failure_count=1,
                    replay_count=0,
                    reason_code=reason_code,
                    status=DeadLetterStatus.OPEN,
                )
                session.add(record)
            else:
                record.failure_count += 1
                record.status = DeadLetterStatus.OPEN
                record.resolved_at = None
                record.approval_id = approval_id or record.approval_id
                record.reason_code = reason_code
            record.task_id = task_id
            record.error_type = error_type
            if mark_run_failed and run.status not in TERMINAL_RUN_STATUSES:
                run.status = AgentRunStatus.FAILED
                run.success = False
                run.completed_at = utc_now_naive()
                run.error_code = "celery_task_failed"
                run.error_message = error_type or reason_code
            await session.flush()
            session.add(
                AuditLog(
                    event_type="dead_letter_recorded",
                    run_id=run.id,
                    ticket_id=run.ticket_id,
                    approval_id=record.approval_id,
                    details={
                        "dead_letter_id": record.id,
                        "task_name": task_name,
                        "reason_code": reason_code,
                        "failure_count": record.failure_count,
                    },
                )
            )
            return record

    async def list(self, status: DeadLetterStatus, *, limit: int = 100) -> list[DeadLetter]:
        async with self.session_factory() as session:
            return list(
                await session.scalars(
                    select(DeadLetter)
                    .where(DeadLetter.status == status)
                    .order_by(DeadLetter.updated_at.desc(), DeadLetter.id.desc())
                    .limit(limit)
                )
            )

    async def get(self, dead_letter_id: int) -> DeadLetter:
        async with self.session_factory() as session:
            record = await session.get(DeadLetter, dead_letter_id)
            if record is None:
                raise ResourceNotFound("dead letter not found")
            return record

    async def request_replay(self, dead_letter_id: int, *, actor_id: str) -> DeadLetter:
        async with self.session_factory.begin() as session:
            record = await session.get(DeadLetter, dead_letter_id, with_for_update=True)
            if record is None:
                raise ResourceNotFound("dead letter not found")
            if record.status is not DeadLetterStatus.OPEN:
                raise BusinessConflict("dead letter is not open")
            run = await session.get(AgentRun, record.run_id)
            if run is None:
                raise ResourceNotFound("agent run not found")
            if run.status not in {
                AgentRunStatus.FAILED,
                AgentRunStatus.RECOVERY_REQUIRED,
            }:
                raise BusinessConflict("agent run is not in a replayable failure state")
            record.status = DeadLetterStatus.REPLAYING
            record.replay_count += 1
            record.replayed_by = actor_id
            record.last_replayed_at = utc_now_naive()
            session.add(
                AuditLog(
                    event_type="dead_letter_replay_requested",
                    run_id=run.id,
                    ticket_id=run.ticket_id,
                    approval_id=record.approval_id,
                    actor_id=actor_id,
                    actor_role=PrincipalRole.ADMIN,
                    details={"dead_letter_id": record.id, "task_name": record.task_name},
                )
            )
            return record

    async def mark_replayed(self, dead_letter_id: int) -> None:
        async with self.session_factory.begin() as session:
            record = await session.get(DeadLetter, dead_letter_id, with_for_update=True)
            if record is None:
                raise ResourceNotFound("dead letter not found")
            if record.status is DeadLetterStatus.REPLAYING:
                record.status = DeadLetterStatus.REPLAYED
                record.resolved_at = utc_now_naive()

    async def reopen(self, dead_letter_id: int, *, reason_code: str) -> None:
        async with self.session_factory.begin() as session:
            record = await session.get(DeadLetter, dead_letter_id, with_for_update=True)
            if record is None:
                raise ResourceNotFound("dead letter not found")
            if record.status is DeadLetterStatus.REPLAYING:
                record.status = DeadLetterStatus.OPEN
                record.reason_code = reason_code
                record.resolved_at = None
