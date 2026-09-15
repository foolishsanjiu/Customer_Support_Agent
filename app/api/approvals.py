from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from structlog.contextvars import bind_contextvars

from app.approvals.service import APPROVER_ROLES, ApprovalService
from app.core.errors import ObjectAccessDenied
from app.models.enums import ApprovalStatus
from app.observability import get_logger, start_span
from app.schemas.approvals import ApprovalDecisionRequest, ApprovalResponse
from app.security.principal import AuthenticatedPrincipal, get_principal
from app.worker.tasks import resume_agent_run

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])
Principal = Annotated[AuthenticatedPrincipal, Depends(get_principal)]
logger = get_logger(__name__)


def _require_approver(principal: AuthenticatedPrincipal) -> None:
    if principal.role not in APPROVER_ROLES:
        raise ObjectAccessDenied("manager approval role is required")


def _service(request: Request) -> ApprovalService:
    return ApprovalService(request.app.state.db_session_factory)


@router.get("", response_model=list[ApprovalResponse])
async def list_approvals(
    request: Request,
    principal: Principal,
    approval_status: Annotated[ApprovalStatus | None, Query(alias="status")] = None,
) -> object:
    _require_approver(principal)
    return await _service(request).list(approval_status)


@router.get("/{approval_id}", response_model=ApprovalResponse)
async def get_approval(approval_id: int, request: Request, principal: Principal) -> object:
    _require_approver(principal)
    return await _service(request).get(approval_id)


async def _decide(
    *,
    approval_id: int,
    approve: bool,
    payload: ApprovalDecisionRequest,
    principal: AuthenticatedPrincipal,
    request: Request,
) -> object:
    with start_span("approval.decide", approval_id=approval_id, approval_approved=approve):
        approval = await _service(request).decide(
            approval_id=approval_id,
            approve=approve,
            principal_id=principal.principal_id,
            role=principal.role,
            reason=payload.reason,
        )
        bind_contextvars(run_id=approval.run_id, approval_id=approval.id)
        resume_agent_run.delay(approval.run_id, approval.id)
        logger.info("approval_decision_enqueued", approved=approve)
        return approval


@router.post("/{approval_id}/approve", response_model=ApprovalResponse)
async def approve(
    approval_id: int,
    payload: ApprovalDecisionRequest,
    request: Request,
    principal: Principal,
) -> object:
    return await _decide(
        approval_id=approval_id,
        approve=True,
        payload=payload,
        principal=principal,
        request=request,
    )


@router.post("/{approval_id}/reject", response_model=ApprovalResponse)
async def reject(
    approval_id: int,
    payload: ApprovalDecisionRequest,
    request: Request,
    principal: Principal,
) -> object:
    return await _decide(
        approval_id=approval_id,
        approve=False,
        payload=payload,
        principal=principal,
        request=request,
    )
