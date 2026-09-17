import json
from asyncio import CancelledError
from time import monotonic
from typing import Any, Protocol, TypeVar

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, ValidationError

from app.core.errors import MCPToolError
from app.mcp.models import (
    DeliveryEstimateResponse,
    FulfillmentStatusResponse,
    TrackingResponse,
)
from app.observability import get_logger, start_span
from app.observability.metrics import record_circuit_rejection, record_external_call
from app.resilience import CircuitBreaker, CircuitBreakerOpen

logger = get_logger(__name__)

ResponseT = TypeVar("ResponseT", bound=BaseModel)


class LogisticsClient(Protocol):
    async def get_tracking(self, tracking_number: str) -> TrackingResponse: ...

    async def get_delivery_estimate(self, tracking_number: str) -> DeliveryEstimateResponse: ...


class FulfillmentClient(Protocol):
    async def get_fulfillment_status(self, order_reference: str) -> FulfillmentStatusResponse: ...


class _ValidatedMCPClient:
    def __init__(
        self,
        url: str,
        *,
        dependency_name: str,
        service_label: str,
        timeout_seconds: float = 5,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.url = url
        self.dependency_name = dependency_name
        self.service_label = service_label
        self.timeout_seconds = timeout_seconds
        self.circuit_breaker = circuit_breaker or CircuitBreaker(dependency_name)

    async def _call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        response_schema: type[ResponseT],
    ) -> ResponseT:
        started = monotonic()
        try:
            permit = self.circuit_breaker.acquire()
        except CircuitBreakerOpen as exc:
            record_circuit_rejection(self.dependency_name)
            record_external_call(
                dependency=self.dependency_name,
                outcome="circuit_open",
                duration_seconds=monotonic() - started,
            )
            logger.warning(
                "mcp_circuit_open",
                tool=tool_name,
                retry_after_seconds=round(exc.retry_after_seconds, 3),
            )
            raise MCPToolError(
                f"{self.service_label} service is temporarily unavailable; "
                f"retry after {exc.retry_after_seconds:.3f} seconds"
            ) from exc
        with start_span("mcp.call", mcp_tool=tool_name):
            try:
                payload = await self._invoke(tool_name, arguments)
                response = response_schema.model_validate(payload)
            except CancelledError:
                self.circuit_breaker.record_failure(permit)
                record_external_call(
                    dependency=self.dependency_name,
                    outcome="cancelled",
                    duration_seconds=monotonic() - started,
                )
                raise
            except MCPToolError:
                self.circuit_breaker.record_failure(permit)
                record_external_call(
                    dependency=self.dependency_name,
                    outcome="failure",
                    duration_seconds=monotonic() - started,
                )
                logger.exception(
                    "mcp_call_failed",
                    tool=tool_name,
                    error_type=MCPToolError.__name__,
                    latency_ms=_elapsed_ms(started),
                    circuit_state=self.circuit_breaker.snapshot().state.value,
                )
                raise
            except (ValidationError, ValueError, OSError, TimeoutError) as exc:
                self.circuit_breaker.record_failure(permit)
                record_external_call(
                    dependency=self.dependency_name,
                    outcome="failure",
                    duration_seconds=monotonic() - started,
                )
                logger.exception(
                    "mcp_call_failed",
                    tool=tool_name,
                    error_type=type(exc).__name__,
                    latency_ms=_elapsed_ms(started),
                    circuit_state=self.circuit_breaker.snapshot().state.value,
                )
                raise MCPToolError(
                    f"invalid or unavailable {self.service_label} MCP result: {tool_name}"
                ) from exc
            except Exception as exc:
                self.circuit_breaker.record_failure(permit)
                record_external_call(
                    dependency=self.dependency_name,
                    outcome="failure",
                    duration_seconds=monotonic() - started,
                )
                logger.exception(
                    "mcp_call_failed",
                    tool=tool_name,
                    error_type=type(exc).__name__,
                    latency_ms=_elapsed_ms(started),
                    circuit_state=self.circuit_breaker.snapshot().state.value,
                )
                raise MCPToolError(f"{self.service_label} MCP call failed: {tool_name}") from exc
            self.circuit_breaker.record_success(permit)
            record_external_call(
                dependency=self.dependency_name,
                outcome="success",
                duration_seconds=monotonic() - started,
            )
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
            raise MCPToolError(f"{self.service_label} MCP returned an error for {tool_name}")
        payload = getattr(result, "structured_content", None)
        return payload if payload is not None else _text_payload(result)


class LogisticsMCPClient(_ValidatedMCPClient):
    def __init__(
        self,
        url: str,
        timeout_seconds: float = 5,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        super().__init__(
            url,
            dependency_name="logistics_mcp",
            service_label="logistics",
            timeout_seconds=timeout_seconds,
            circuit_breaker=circuit_breaker,
        )

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


class FulfillmentMCPClient(_ValidatedMCPClient):
    def __init__(
        self,
        url: str,
        timeout_seconds: float = 5,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        super().__init__(
            url,
            dependency_name="fulfillment_mcp",
            service_label="fulfillment",
            timeout_seconds=timeout_seconds,
            circuit_breaker=circuit_breaker,
        )

    async def get_fulfillment_status(self, order_reference: str) -> FulfillmentStatusResponse:
        return await self._call(
            "get_fulfillment_status",
            {"order_reference": order_reference},
            FulfillmentStatusResponse,
        )


def _text_payload(result: Any) -> Any:
    content = getattr(result, "content", [])
    if len(content) != 1 or getattr(content[0], "type", None) != "text":
        raise MCPToolError("MCP response has no structured payload")
    return json.loads(content[0].text)


def _elapsed_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))
