import json
from time import monotonic
from typing import Any, Protocol, TypeVar

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, ValidationError

from app.core.errors import MCPToolError
from app.mcp.models import DeliveryEstimateResponse, TrackingResponse
from app.observability import get_logger, start_span

logger = get_logger(__name__)

ResponseT = TypeVar("ResponseT", bound=BaseModel)


class LogisticsClient(Protocol):
    async def get_tracking(self, tracking_number: str) -> TrackingResponse: ...

    async def get_delivery_estimate(self, tracking_number: str) -> DeliveryEstimateResponse: ...


class LogisticsMCPClient:
    def __init__(self, url: str, timeout_seconds: float = 5) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds

    async def get_tracking(self, tracking_number: str) -> TrackingResponse:
        return await self._call(
            "get_tracking",
            {"tracking_number": tracking_number},
            TrackingResponse,
        )

    async def get_delivery_estimate(self, tracking_number: str) -> DeliveryEstimateResponse:
        return await self._call(
            "get_delivery_estimate",
            {"tracking_number": tracking_number},
            DeliveryEstimateResponse,
        )

    async def _call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        response_schema: type[ResponseT],
    ) -> ResponseT:
        started = monotonic()
        with start_span("mcp.call", mcp_tool=tool_name):
            try:
                payload = await self._invoke(tool_name, arguments)
                response = response_schema.model_validate(payload)
            except MCPToolError:
                logger.exception(
                    "mcp_call_failed",
                    tool=tool_name,
                    error_type=MCPToolError.__name__,
                    latency_ms=_elapsed_ms(started),
                )
                raise
            except (ValidationError, ValueError, OSError, TimeoutError) as exc:
                logger.exception(
                    "mcp_call_failed",
                    tool=tool_name,
                    error_type=type(exc).__name__,
                    latency_ms=_elapsed_ms(started),
                )
                raise MCPToolError(
                    f"invalid or unavailable logistics MCP result: {tool_name}"
                ) from exc
            except Exception as exc:
                logger.exception(
                    "mcp_call_failed",
                    tool=tool_name,
                    error_type=type(exc).__name__,
                    latency_ms=_elapsed_ms(started),
                )
                raise MCPToolError(f"logistics MCP call failed: {tool_name}") from exc
            logger.info(
                "mcp_call_completed",
                tool=tool_name,
                latency_ms=_elapsed_ms(started),
            )
            return response

    async def _invoke(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        async with streamable_http_client(self.url) as (read_stream, write_stream):
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=self.timeout_seconds,
            ) as session:
                await session.initialize()
                result = await session.call_tool(
                    tool_name,
                    arguments,
                    read_timeout_seconds=self.timeout_seconds,
                )
        if getattr(result, "is_error", False):
            raise MCPToolError(f"logistics MCP returned an error for {tool_name}")
        payload = getattr(result, "structured_content", None)
        return payload if payload is not None else _text_payload(result)


def _text_payload(result: Any) -> Any:
    content = getattr(result, "content", [])
    if len(content) != 1 or getattr(content[0], "type", None) != "text":
        raise MCPToolError("logistics MCP response has no structured payload")
    return json.loads(content[0].text)


def _elapsed_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))
