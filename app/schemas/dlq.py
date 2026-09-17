from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import DeadLetterStatus


class DeadLetterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    approval_id: int | None
    task_name: str
    task_id: str | None
    status: DeadLetterStatus
    reason_code: str
    error_type: str | None
    failure_count: int
    replay_count: int
    replayed_by: str | None
    last_replayed_at: datetime | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime
