from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from app.agent.store import DatabaseAgentStore
from app.models.enums import PrincipalRole
from app.schemas.approvals import AgentRunCreateRequest, AgentRunResponse
from app.security.principal import AuthenticatedPrincipal, get_principal
from app.worker.tasks import run_agent

router = APIRouter(prefix="/api/v1/agent-runs", tags=["agent-runs"])
Principal = Annotated[AuthenticatedPrincipal, Depends(get_principal)]


@router.post("", response_model=AgentRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_agent_run(
    payload: AgentRunCreateRequest,
    principal: Principal,
    request: Request,
) -> AgentRunResponse:
    if principal.role is not PrincipalRole.CUSTOMER or principal.customer_id is None:
        from app.core.errors import ObjectAccessDenied

        raise ObjectAccessDenied("customer identity is required")
    store = DatabaseAgentStore(request.app.state.db_session_factory)
    run = await store.create_run(payload.ticket_id, principal.customer_id)
    run_agent.delay(run.id)
    return AgentRunResponse(run_id=run.id, status=run.status.value)
