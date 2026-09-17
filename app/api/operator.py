from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.core.errors import ObjectAccessDenied
from app.models import AgentRun
from app.models.enums import AgentRunStatus, PrincipalRole
from app.schemas.operator import OperatorRunResponse
from app.security.principal import AuthenticatedPrincipal, get_principal

router = APIRouter(prefix="/api/v1/operator", tags=["operator"])
console_router = APIRouter(include_in_schema=False)
Principal = Annotated[AuthenticatedPrincipal, Depends(get_principal)]
OPERATOR_ROOT = Path(__file__).resolve().parents[1] / "operator"
OPERATOR_ASSET_DIR = OPERATOR_ROOT / "static"
OPERATOR_ROLES = {
    PrincipalRole.SUPPORT_AGENT,
    PrincipalRole.MANAGER,
    PrincipalRole.ADMIN,
}


@console_router.get("/operator", response_class=FileResponse)
async def operator_console() -> FileResponse:
    return FileResponse(
        OPERATOR_ROOT / "index.html",
        media_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/runs", response_model=list[OperatorRunResponse])
async def list_runs(
    principal: Principal,
    request: Request,
    status_filter: Annotated[AgentRunStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[OperatorRunResponse]:
    _require_operator(principal)
    statement = select(AgentRun)
    if status_filter is not None:
        statement = statement.where(AgentRun.status == status_filter)
    statement = statement.order_by(AgentRun.updated_at.desc(), AgentRun.id.desc()).limit(limit)
    async with request.app.state.db_session_factory() as session:
        runs = list(await session.scalars(statement))
    return [OperatorRunResponse.model_validate(run) for run in runs]


def _require_operator(principal: AuthenticatedPrincipal) -> None:
    if principal.role not in OPERATOR_ROLES:
        raise ObjectAccessDenied("operator role is required")
