from time import time

import httpx
import jwt
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.models.enums import AgentRunStatus, PrincipalRole
from scripts.verify_golden_path import create_jwt, wait_for_run_status


def test_create_jwt_binds_customer_and_role() -> None:
    settings = Settings(
        _env_file=None,
        jwt_secret=SecretStr("a-secure-test-secret-with-32-bytes"),
        jwt_issuer="issuer",
        jwt_audience="audience",
    )

    token = create_jwt(
        settings,
        subject="customer-7",
        role=PrincipalRole.CUSTOMER,
        customer_id=7,
    )
    claims = jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=["HS256"],
        issuer="issuer",
        audience="audience",
    )

    assert claims["sub"] == "customer-7"
    assert claims["role"] == PrincipalRole.CUSTOMER.value
    assert claims["customer_id"] == 7
    assert claims["exp"] > time()


@pytest.mark.asyncio
async def test_wait_for_run_status_returns_target() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"run_id": 5, "status": "WAITING_APPROVAL"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
    ) as client:
        result = await wait_for_run_status(
            client,
            5,
            AgentRunStatus.WAITING_APPROVAL,
            headers={},
            max_wait_seconds=1,
            poll_interval=0,
        )

    assert result["status"] == AgentRunStatus.WAITING_APPROVAL.value


@pytest.mark.asyncio
async def test_wait_for_run_status_rejects_terminal_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"run_id": 5, "status": "RECOVERY_REQUIRED"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
    ) as client:
        with pytest.raises(RuntimeError, match="terminal failure state RECOVERY_REQUIRED"):
            await wait_for_run_status(
                client,
                5,
                AgentRunStatus.SUCCEEDED,
                headers={},
                max_wait_seconds=1,
                poll_interval=0,
            )
