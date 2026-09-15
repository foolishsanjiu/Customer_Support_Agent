import re
from time import monotonic
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.contextvars import bind_contextvars, clear_contextvars

from app.observability.logging import get_logger

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
logger = get_logger(__name__)


class CorrelationMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        clear_contextvars()
        request_id = _request_id(scope)
        bind_contextvars(request_id=request_id)
        started = monotonic()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
            logger.info(
                "http_request_completed",
                method=scope["method"],
                path=scope["path"],
                status_code=status_code,
                latency_ms=_elapsed_ms(started),
            )
        except Exception:
            logger.exception(
                "http_request_failed",
                method=scope["method"],
                path=scope["path"],
                latency_ms=_elapsed_ms(started),
            )
            raise
        finally:
            clear_contextvars()


def _request_id(scope: Scope) -> str:
    headers = dict(scope.get("headers", []))
    candidate = headers.get(b"x-request-id", b"").decode("ascii", errors="ignore")
    return candidate if REQUEST_ID_PATTERN.fullmatch(candidate) else uuid4().hex


def _elapsed_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))
