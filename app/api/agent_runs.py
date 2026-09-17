from asyncio import sleep
from collections.abc import AsyncIterator
from json import dumps
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sse_starlette.sse import EventSourceResponse
from structlog.contextvars import bind_contextvars

from app.agent.store import DatabaseAgentStore
from app.models import AgentRun
from app.models.enums import AgentRunStatus, PrincipalRole
from app.observability import get_logger, start_span
from app.schemas.approvals import (
    AgentRunCancelRequest,
    AgentRunCreateRequest,
    AgentRunResponse,
)
from app.security.principal import AuthenticatedPrincipal, get_principal
from app.worker.tasks import run_agent

router = APIRouter(prefix="/api/v1/agent-runs", tags=["agent-runs"])
Principal = Annotated[AuthenticatedPrincipal, Depends(get_principal)]
logger = get_logger(__name__)
TERMINAL_RUN_STATUSES = {
    AgentRunStatus.SUCCEEDED,
    AgentRunStatus.FAILED,
    AgentRunStatus.CANCELLED,
}


@router.post("", response_model=AgentRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_agent_run(
    payload: AgentRunCreateRequest,
    principal: Principal,
    request: Request,
) -> AgentRunResponse:
    customer_id = _require_customer(principal)
    store = DatabaseAgentStore(request.app.state.db_session_factory)
    with start_span("agent.enqueue", ticket_id=payload.ticket_id):
        run = await store.create_run(payload.ticket_id, customer_id)
        bind_contextvars(run_id=run.id, ticket_id=payload.ticket_id)
        run_agent.delay(run.id)
        logger.info("agent_run_enqueued")
    return AgentRunResponse(run_id=run.id, status=run.status.value)


@router.get("/{run_id}", response_model=AgentRunResponse)
async def get_agent_run(
    run_id: int,
    principal: Principal,
    request: Request,
) -> AgentRunResponse:
    customer_id = _require_customer(principal)
    run = await DatabaseAgentStore(request.app.state.db_session_factory).get_run(
        run_id, customer_id
    )
    return AgentRunResponse(run_id=run.id, status=run.status.value)


@router.post(
    "/{run_id}/cancel", response_model=AgentRunResponse, status_code=status.HTTP_202_ACCEPTED
)
async def cancel_agent_run(
    run_id: int,
    payload: AgentRunCancelRequest,
    principal: Principal,
    request: Request,
) -> AgentRunResponse:
    run = await DatabaseAgentStore(request.app.state.db_session_factory).request_cancellation(
        run_id,
        principal_id=principal.principal_id,
        role=principal.role,
        customer_id=principal.customer_id,
        reason=payload.reason,
    )
    logger.info("agent_run_cancellation_accepted", run_id=run.id, status=run.status.value)
    return AgentRunResponse(run_id=run.id, status=run.status.value)


@router.get("/{run_id}/events", response_class=EventSourceResponse)
async def stream_agent_run(
    run_id: int,
    principal: Principal,
    request: Request,
) -> EventSourceResponse:
    customer_id = _require_customer(principal)
    store = DatabaseAgentStore(request.app.state.db_session_factory)
    initial = await store.get_run(run_id, customer_id)
    return EventSourceResponse(
        _agent_run_events(request, store, run_id, customer_id, initial),
        ping=15,
    )


async def _agent_run_events(
    request: Request,
    store: DatabaseAgentStore,
    run_id: int,
    customer_id: int,
    initial: AgentRun,
    *,
    poll_interval_seconds: float = 1,
) -> AsyncIterator[dict[str, str]]:
    last_event_id = request.headers.get("last-event-id")
    run = initial
    while True:
        event_id = _run_event_id(run)
        if event_id != last_event_id:
            yield {
                "event": "agent_run.status",
                "id": event_id,
                "data": dumps(
                    {
                        "run_id": run.id,
                        "status": run.status.value,
                        "current_node": run.current_node,
                        "updated_at": run.updated_at.isoformat(),
                        "terminal": run.status in TERMINAL_RUN_STATUSES,
                    },
                    separators=(",", ":"),
                ),
            }
            last_event_id = event_id
        if run.status in TERMINAL_RUN_STATUSES or await request.is_disconnected():
            return
        await sleep(poll_interval_seconds)
        run = await store.get_run(run_id, customer_id)


def _run_event_id(run: AgentRun) -> str:
    return ":".join(
        (
            str(run.id),
            run.updated_at.isoformat(),
            run.status.value,
            run.current_node or "-",
        )
    )


def _require_customer(principal: AuthenticatedPrincipal) -> int:
    if principal.role is not PrincipalRole.CUSTOMER or principal.customer_id is None:
        from app.core.errors import ObjectAccessDenied

        raise ObjectAccessDenied("customer identity is required")
    return principal.customer_id
