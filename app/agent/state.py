from typing import Any, TypedDict

from app.agent.models import ChatMessage


class AgentState(TypedDict, total=False):
    run_id: int
    ticket_id: int
    customer_id: int
    messages: list[ChatMessage]
    conversation_summary: str | None
    intent: dict[str, Any] | None
    order: dict[str, Any] | None
    shipment: dict[str, Any] | None
    plan: dict[str, Any] | None
    pending_tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    risk_level: str | None
    approval_status: str | None
    approval_id: int | None
    final_response: str | None
    errors: list[str]
    retry_count: int
    trace_id: str
    step_count: int
    verification_complete: bool
    needs_more_action: bool
    context: dict[str, Any] | None
    business_outcome: str | None
