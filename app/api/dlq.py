from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.dlq import DeadLetterStore
from app.models.enums import DeadLetterStatus, PrincipalRole
from app.observability import get_logger
from app.schemas.dlq import DeadLetterResponse
from app.security.principal import AuthenticatedPrincipal, get_principal
from app.worker.tasks import replay_dead_letter

router = APIRouter(prefix="/api/v1/dlq", tags=["dlq"])
Principal = Annotated[AuthenticatedPrincipal, Depends(get_principal)]
logger = get_logger(__name__)


@router.get("", response_model=list[DeadLetterResponse])
async def list_dead_letters(
    principal: Principal,
    request: Request,
    status_filter: Annotated[DeadLetterStatus, Query(alias="status")] = DeadLetterStatus.OPEN,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> list[DeadLetterResponse]:
    _require_admin(principal)
    records = await DeadLetterStore(request.app.state.db_session_factory).list(
        status_filter,
        limit=limit,
    )
    return [DeadLetterResponse.model_validate(record) for record in records]


@router.post(
    "/{dead_letter_id}/replay",
    response_model=DeadLetterResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def replay(
    dead_letter_id: int,
    principal: Principal,
    request: Request,
) -> DeadLetterResponse:
    _require_admin(principal)
    store = DeadLetterStore(request.app.state.db_session_factory)
    record = await store.request_replay(dead_letter_id, actor_id=principal.principal_id)
    try:
        replay_dead_letter.delay(record.id)
    except Exception as exc:
        await store.reopen(record.id, reason_code="replay_enqueue_failed")
        logger.exception(
            "dead_letter_replay_enqueue_failed",
            dead_letter_id=record.id,
            error_type=type(exc).__name__,
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Dead-letter replay could not be enqueued",
        ) from exc
    return DeadLetterResponse.model_validate(record)


def _require_admin(principal: AuthenticatedPrincipal) -> None:
    if principal.role is not PrincipalRole.ADMIN:
        from app.core.errors import ObjectAccessDenied

        raise ObjectAccessDenied("administrator role is required")
