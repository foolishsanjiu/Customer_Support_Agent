from time import time
from types import SimpleNamespace

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import SecretStr

from app.core.config import Settings
from app.models.enums import PrincipalRole
from app.security import principal as principal_module


class FakeBearer:
    def __init__(self, credentials: HTTPAuthorizationCredentials | None) -> None:
        self.credentials = credentials

    async def __call__(self, request):
        return self.credentials


def settings(secret: str | None = "test-secret") -> Settings:
    return Settings(
        _env_file=None,
        jwt_secret=SecretStr(secret) if secret is not None else None,
        jwt_issuer="issuer",
        jwt_audience="audience",
    )


@pytest.mark.asyncio
async def test_valid_jwt_returns_typed_principal(monkeypatch) -> None:
    token = jwt.encode(
        {
            "sub": "manager-1",
            "role": PrincipalRole.MANAGER.value,
            "customer_id": 42,
            "exp": int(time()) + 60,
            "iss": "issuer",
            "aud": "audience",
        },
        "test-secret",
        algorithm="HS256",
    )
    monkeypatch.setattr(principal_module, "get_settings", settings)
    monkeypatch.setattr(
        principal_module,
        "bearer",
        FakeBearer(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)),
    )
    principal = await principal_module.get_principal(SimpleNamespace())
    assert principal.principal_id == "manager-1"
    assert principal.role is PrincipalRole.MANAGER
    assert principal.customer_id == 42


@pytest.mark.asyncio
async def test_jwt_configuration_and_invalid_token_are_rejected(monkeypatch) -> None:
    monkeypatch.setattr(principal_module, "get_settings", lambda: settings(None))
    monkeypatch.setattr(principal_module, "bearer", FakeBearer(None))
    with pytest.raises(HTTPException) as missing_config:
        await principal_module.get_principal(SimpleNamespace())
    assert missing_config.value.status_code == 503

    monkeypatch.setattr(principal_module, "get_settings", settings)
    monkeypatch.setattr(
        principal_module,
        "bearer",
        FakeBearer(HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid")),
    )
    with pytest.raises(HTTPException) as invalid:
        await principal_module.get_principal(SimpleNamespace())
    assert invalid.value.status_code == 401
