from dataclasses import replace

import pytest

from app.models.enums import PrincipalRole
from app.tool_runtime.catalog import BusinessToolCatalog
from app.tool_runtime.models import RetryPolicy, ToolDefinition
from app.tool_runtime.registry import ToolRegistry


def test_p0_catalog_and_filters() -> None:
    registry = BusinessToolCatalog(None).build_registry()  # type: ignore[arg-type]
    assert {tool.name for tool in registry.list_tools()} == {
        "get_customer",
        "get_order",
        "list_customer_orders",
        "get_refund_status",
        "get_shipping",
        "get_tracking",
        "get_delivery_estimate",
        "get_refund",
        "search_policy",
        "cancel_order",
        "refund_order",
        "update_ticket",
        "escalate_ticket",
        "request_human_approval",
    }
    assert {tool.name for tool in registry.get_tools_for_intent("SHIPPING_QUERY")} == {
        "get_tracking"
    }
    assert {tool.name for tool in registry.get_tools_for_intent("ORDER_LIST")} == {
        "list_customer_orders"
    }
    assert {tool.name for tool in registry.get_tools_for_intent("REFUND_STATUS")} == {
        "get_refund_status"
    }
    assert "refund_order" in {
        tool.name for tool in registry.get_tools_for_role(PrincipalRole.CUSTOMER)
    }
    assert "refund_order" in {
        tool.name for tool in registry.get_tools_for_role(PrincipalRole.MANAGER)
    }
    assert [
        tool.name
        for tool in registry.select_tools(intent="CANCEL_ORDER", role=PrincipalRole.CUSTOMER)
    ] == ["cancel_order"]

    registry.register(
        replace(
            registry.get("get_order"),
            name="manager_only_read",
            required_permission="refund:approve",
            intents=frozenset({"REFUND"}),
        )
    )
    customer_refund_tools = {
        tool.name for tool in registry.select_tools(intent="REFUND", role=PrincipalRole.CUSTOMER)
    }
    manager_refund_tools = {
        tool.name for tool in registry.select_tools(intent="REFUND", role=PrincipalRole.MANAGER)
    }
    assert "manager_only_read" not in customer_refund_tools
    assert "manager_only_read" in manager_refund_tools


def test_duplicate_registration_and_invalid_definition_are_rejected() -> None:
    async def handler(arguments, context):
        return {}

    tool = BusinessToolCatalog(None).build_registry().get("get_order")  # type: ignore[arg-type]
    registry = ToolRegistry()
    registry.register(tool)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(tool)
    with pytest.raises(ValueError, match="write tools must be idempotent"):
        ToolDefinition(
            name="unsafe_write",
            description="unsafe",
            input_schema=tool.input_schema,
            handler=handler,
            risk_level=tool.risk_level,
            required_permission="order:cancel",
            timeout_seconds=1,
            retry_policy=RetryPolicy(),
            idempotent=False,
            read_only=False,
        )
