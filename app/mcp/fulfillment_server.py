from datetime import UTC, datetime

from mcp.server.mcpserver import MCPServer

from app.core.config import get_settings
from app.mcp.models import FulfillmentStatusResponse
from app.observability import configure_logging, start_span
from app.observability.tracing import configure_tracing

server = MCPServer(
    name="resolvex-fulfillment",
    instructions=(
        "External fulfillment status only. Never grant authorization or issue business commands."
    ),
)


@server.tool(structured_output=True)
def get_fulfillment_status(order_reference: str) -> FulfillmentStatusResponse:
    with start_span("mcp.get_fulfillment_status"):
        packed = order_reference.upper().startswith("PACKED-")
        return FulfillmentStatusResponse(
            order_reference=order_reference,
            status="PACKED" if packed else "READY_TO_PICK",
            cancellable=not packed,
            updated_at=datetime.now(UTC),
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
        port=8002,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
