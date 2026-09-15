from datetime import UTC, datetime, timedelta

from mcp.server.mcpserver import MCPServer

from app.core.config import get_settings
from app.mcp.models import DeliveryEstimateResponse, TrackingResponse
from app.observability import configure_logging, start_span
from app.observability.tracing import configure_tracing

server = MCPServer(
    name="resolvex-logistics",
    instructions="External logistics data only. Never issue business or authorization commands.",
)


@server.tool(structured_output=True)
def get_tracking(tracking_number: str) -> TrackingResponse:
    with start_span("mcp.get_tracking"):
        suffix = tracking_number[-1:] if tracking_number else ""
        status = "DELAYED" if suffix == "1" else "IN_TRANSIT"
        return TrackingResponse(
            tracking_number=tracking_number,
            carrier="ResolveX Express",
            status=status,
            current_location="Shanghai Hub",
            last_event="Delivery delayed" if status == "DELAYED" else "Shipment updated",
            updated_at=datetime.now(UTC),
        )


@server.tool(structured_output=True)
def get_delivery_estimate(tracking_number: str) -> DeliveryEstimateResponse:
    with start_span("mcp.get_delivery_estimate"):
        suffix = tracking_number[-1:] if tracking_number else ""
        delayed = suffix == "1"
        return DeliveryEstimateResponse(
            tracking_number=tracking_number,
            estimated_delivery_at=datetime.now(UTC) + timedelta(days=4 if delayed else 2),
            confidence="MEDIUM" if delayed else "HIGH",
        )


def main() -> None:
    settings = get_settings()
    configure_logging(settings.service_name, settings.log_level)
    configure_tracing(
        service=settings.service_name,
        endpoint=settings.otel_exporter_otlp_endpoint,
        enabled=settings.otel_enabled,
    )
    server.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8001,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
