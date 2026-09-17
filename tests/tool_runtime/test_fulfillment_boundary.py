from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.errors import BusinessConflict, ToolUnavailable
from app.mcp.models import FulfillmentStatusResponse
from app.models.enums import OrderStatus
from app.tool_runtime import catalog as catalog_module
from app.tool_runtime.catalog import BusinessToolCatalog
from app.tool_runtime.models import OrderInput


class Sessions:
    def __init__(self) -> None:
        self.entered = False

    def __call__(self):
        return self

    async def __aenter__(self):
        self.entered = True
        return object()

    async def __aexit__(self, *_):
        return None


class Fulfillment:
    def __init__(self, *, reference="7", status="READY_TO_PICK", cancellable=True) -> None:
        self.result = FulfillmentStatusResponse(
            order_reference=reference,
            status=status,
            cancellable=cancellable,
            updated_at=datetime.now(UTC),
        )

    async def get_fulfillment_status(self, order_reference):
        return self.result


@pytest.mark.asyncio
async def test_cancellation_fails_closed_when_fulfillment_is_not_configured() -> None:
    sessions = Sessions()
    catalog = BusinessToolCatalog(sessions)

    with pytest.raises(ToolUnavailable, match="fulfillment MCP"):
        await catalog.cancel_order(OrderInput(order_id=7), SimpleNamespace(customer_id=1))

    assert sessions.entered is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fulfillment",
    [
        Fulfillment(reference="8"),
        Fulfillment(status="PACKED", cancellable=False),
    ],
)
async def test_cancellation_rejects_mismatched_or_non_cancellable_fulfillment(
    fulfillment,
) -> None:
    sessions = Sessions()
    catalog = BusinessToolCatalog(sessions, fulfillment_client=fulfillment)

    with pytest.raises(BusinessConflict):
        await catalog.cancel_order(OrderInput(order_id=7), SimpleNamespace(customer_id=1))

    assert sessions.entered is False


@pytest.mark.asyncio
async def test_cancellation_proceeds_only_after_cancellable_fulfillment(monkeypatch) -> None:
    calls = []

    class CommerceService:
        def __init__(self, session):
            pass

        async def cancel_order(self, order_id, customer_id):
            calls.append((order_id, customer_id))
            now = datetime.now(UTC).replace(tzinfo=None)
            return SimpleNamespace(
                id=order_id,
                customer_id=customer_id,
                status=OrderStatus.CANCELLED,
                total_amount=Decimal("10.00"),
                created_at=now,
                paid_at=now,
                shipped_at=None,
                delivered_at=None,
                updated_at=now,
            )

    monkeypatch.setattr(catalog_module, "CommerceService", CommerceService)
    catalog = BusinessToolCatalog(Sessions(), fulfillment_client=Fulfillment())

    result = await catalog.cancel_order(OrderInput(order_id=7), SimpleNamespace(customer_id=1))

    assert result["status"] == "CANCELLED"
    assert calls == [(7, 1)]
