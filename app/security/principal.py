from dataclasses import dataclass

import jwt
from fastapi import HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.models.enums import PrincipalRole

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    principal_id: str
    role: PrincipalRole
    customer_id: int | None = None


async def get_principal(request: Request) -> AuthenticatedPrincipal:
    credentials: HTTPAuthorizationCredentials | None = await bearer(request)
    settings = get_settings()
    if settings.jwt_secret is None or not settings.jwt_secret.get_secret_value():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "JWT is not configured")
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bearer token is required")
    try:
        claims = jwt.decode(
            credentials.credentials,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["sub", "role", "exp", "iss", "aud"]},
        )
        principal_id = str(claims["sub"]).strip()
        role = PrincipalRole(claims["role"])
        customer_id = int(claims["customer_id"]) if "customer_id" in claims else None
        if not principal_id:
            raise ValueError("empty subject")
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token") from exc
    return AuthenticatedPrincipal(principal_id, role, customer_id)
