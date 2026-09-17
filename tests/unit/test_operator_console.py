from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import operator
from app.core.errors import ObjectAccessDenied
from app.main import create_app
from app.models.enums import AgentRunStatus, PrincipalRole
from app.security.principal import AuthenticatedPrincipal


def principal(role: PrincipalRole) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal("operator-1", role)


def test_console_and_assets_are_served_with_browser_security_headers() -> None:
    client = TestClient(create_app())

    response = client.get("/operator")
    script = client.get("/operator/assets/app.js")
    stylesheet = client.get("/operator/assets/app.css")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "/operator/assets/app.js" in response.text
    assert "/operator/assets/app.css" in response.text
    assert script.status_code == 200
    assert stylesheet.status_code == 200


def test_console_does_not_persist_tokens_or_render_approval_arguments() -> None:
    source = (operator.OPERATOR_ASSET_DIR / "app.js").read_text(encoding="utf-8")

    assert "localStorage" not in source
    assert "sessionStorage" not in source
    assert "document.cookie" not in source
    assert "arguments_snapshot" not in source
    assert "innerHTML" not in source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    [PrincipalRole.SUPPORT_AGENT, PrincipalRole.MANAGER, PrincipalRole.ADMIN],
)
async def test_operator_roles_can_list_recent_runs(role: PrincipalRole) -> None:
    now = datetime(2026, 9, 17, 18, 0)
    run = SimpleNamespace(
        id=9,
        ticket_id=12,
        status=AgentRunStatus.RUNNING,
        intent="shipping_status",
        current_node="execute_tool",
        success=None,
        error_code=None,
        recovery_attempts=0,
        created_at=now,
        updated_at=now,
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def scalars(self, statement):
            assert statement is not None
            return [run]

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(db_session_factory=lambda: Session()),
        ),
    )

    result = await operator.list_runs(principal(role), request, None, 50)

    assert [item.id for item in result] == [9]
    assert result[0].status is AgentRunStatus.RUNNING


@pytest.mark.asyncio
async def test_customer_cannot_list_operator_runs() -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    with pytest.raises(ObjectAccessDenied, match="operator role"):
        await operator.list_runs(principal(PrincipalRole.CUSTOMER), request, None, 50)


def test_operator_run_route_is_documented() -> None:
    assert "/api/v1/operator/runs" in create_app().openapi()["paths"]


def test_operator_assets_are_in_package_data() -> None:
    project = Path(__file__).resolve().parents[2]
    configuration = (project / "pyproject.toml").read_text(encoding="utf-8")

    assert '"operator/*.html"' in configuration
    assert '"operator/static/*.js"' in configuration
