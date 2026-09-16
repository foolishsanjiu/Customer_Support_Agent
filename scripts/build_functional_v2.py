import json
from pathlib import Path
from typing import Any

V1_PATH = Path("evals/datasets/functional_v1.json")
V2_PATH = Path("evals/datasets/functional_v2.json")


def build_dataset(v1_path: Path = V1_PATH) -> list[dict[str, Any]]:
    v1 = json.loads(v1_path.read_text(encoding="utf-8"))
    return [
        *v1,
        *_order_cases(),
        *_shipping_cases(),
        *_refund_cases(),
        *_cancellation_cases(),
        *_policy_cases(),
        *_multi_turn_cases(),
        *_missing_cases(),
    ]


def _case(
    *,
    case_id: str,
    category: str,
    message: str,
    intent: str,
    tool: str | None,
    outcome: str,
    order_id: int | None = None,
    reason: str | None = None,
    initial_state: dict[str, Any] | None = None,
    history: list[dict[str, str]] | None = None,
    approval: bool = False,
) -> dict[str, Any]:
    entities: dict[str, Any] = {}
    if order_id is not None:
        entities["order_id"] = order_id
    if reason is not None:
        entities["reason"] = reason
    tools = [tool] if tool else []
    arguments: dict[str, dict[str, Any]] = {}
    if tool:
        tool_arguments: dict[str, Any] = {}
        if order_id is not None and tool != "search_policy":
            tool_arguments["order_id"] = order_id
        if reason is not None and tool == "refund_order":
            tool_arguments["reason"] = reason
        if tool_arguments:
            arguments[tool] = tool_arguments
    value: dict[str, Any] = {
        "id": case_id,
        "category": category,
        "user_message": message,
        "expected_intent": intent,
        "expected_entities": entities,
        "expected_tools": tools,
        "expected_tool_arguments": arguments,
        "expected_outcome": outcome,
        "should_require_approval": approval,
    }
    if initial_state:
        value["initial_state"] = initial_state
    if history:
        value["conversation_history"] = history
    return value


def _order_cases() -> list[dict[str, Any]]:
    prompts = (
        (11, "Is order 11 confirmed in the system?"),
        (12, "Show me the purchase status for order number 12."),
        (13, "Did order 13 finish processing?"),
        (15, "I want the commercial status of order #15."),
        (17, "Check whether payment succeeded for order 17."),
        (18, "What state is order 18 in?"),
        (23, "Look up the record for order 23."),
        (26, "Has order 26 been completed?"),
        (32, "Please retrieve order 32 for me."),
        (34, "Was order 34 recorded as delivered?"),
    )
    return [
        _case(
            case_id=f"order-{index:02d}",
            category="order",
            message=message,
            intent="ORDER_QUERY",
            tool="get_order",
            outcome="order_status_returned",
            order_id=order_id,
            initial_state={"order_id": order_id, "owner_customer_id": 1},
        )
        for index, (order_id, message) in enumerate(prompts, start=11)
    ]


def _shipping_cases() -> list[dict[str, Any]]:
    prompts = (
        (11, "Which city is the parcel for order 11 in?", "IN_TRANSIT"),
        (13, "Give me the carrier tracking for order 13.", "IN_TRANSIT"),
        (17, "When will order 17 reach me?", "DELAYED"),
        (18, "Has the courier picked up order 18?", "IN_TRANSIT"),
        (23, "Show the latest scan for order 23.", "IN_TRANSIT"),
        (25, "Order 25 is delayed; locate the package.", "DELAYED"),
        (26, "Track shipment 26 for me.", "IN_TRANSIT"),
        (32, "Where is the delivery for order #32?", "IN_TRANSIT"),
        (34, "What is the delivery progress of order 34?", "DELIVERED"),
        (35, "Find the expected arrival for order 35.", "IN_TRANSIT"),
    )
    return [
        _case(
            case_id=f"shipping-{index:02d}",
            category="shipping",
            message=message,
            intent="SHIPPING_QUERY",
            tool="get_tracking",
            outcome="tracking_returned",
            order_id=order_id,
            initial_state={"order_id": order_id, "shipment_status": status},
        )
        for index, (order_id, message, status) in enumerate(prompts, start=11)
    ]


def _refund_cases() -> list[dict[str, Any]]:
    eligible = (
        (33, "Refund order 33 because the screen is cracked.", "the screen is cracked"),
        (35, "I need my money back for order 35; the item leaks.", "the item leaks"),
        (36, "Return payment for order 36 because the size is wrong.", "the size is wrong"),
        (37, "Refund order 37. The box contained the wrong color.", "wrong color"),
        (38, "Please refund order 38 because it stopped working.", "it stopped working"),
        (39, "Order 39 arrived with missing accessories; refund it.", "missing accessories"),
        (40, "Refund order 40 because the package was crushed.", "the package was crushed"),
        (
            41,
            "I want a refund for order 41; it has a manufacturing defect.",
            "manufacturing defect",
        ),
        (42, "Give me my money back for order 42 because it arrived opened.", "it arrived opened"),
        (43, "Refund order 43 because the battery will not charge.", "the battery will not charge"),
        (44, "Please reverse order 44; the product is counterfeit.", "the product is counterfeit"),
        (45, "Refund order 45 because two components are broken.", "two components are broken"),
    )
    cases = [
        _case(
            case_id=f"refund-{index:02d}",
            category="refund",
            message=message,
            intent="REFUND",
            tool="refund_order",
            outcome="waiting_for_approval",
            order_id=order_id,
            reason=reason,
            initial_state={"order_id": order_id, "eligible": True},
            approval=True,
        )
        for index, (order_id, message, reason) in enumerate(eligible, start=16)
    ]
    denials = (
        (
            28,
            46,
            "Refund order 46 again because it is still defective.",
            "it is still defective",
            {"eligible": False, "status": "REFUNDED"},
            "denied_already_refunded",
        ),
        (
            29,
            47,
            "I already got a refund for order 47, but refund it once more.",
            "refund it once more",
            {"eligible": False, "status": "REFUNDED"},
            "denied_already_refunded",
        ),
        (
            30,
            48,
            "Refund order 48 because I no longer need it.",
            "I no longer need it",
            {"eligible": False, "delivered_days_ago": 60},
            "denied_outside_window",
        ),
        (
            31,
            49,
            "Return order 49 after 90 days because it is scratched.",
            "it is scratched",
            {"eligible": False, "delivered_days_ago": 90},
            "denied_outside_window",
        ),
        (
            32,
            50,
            "Refund paid order 50 because delivery is taking too long.",
            "delivery is taking too long",
            {"eligible": False, "status": "PAID"},
            "denied_not_delivered",
        ),
        (
            33,
            51,
            "Refund order 51 because it has not shipped yet.",
            "it has not shipped yet",
            {"eligible": False, "status": "PAID"},
            "denied_not_delivered",
        ),
        (
            34,
            52,
            "Refund order 52 because it arrived broken.",
            "it arrived broken",
            {"eligible": False, "owner_customer_id": 2, "requester_customer_id": 1},
            "denied_cross_user",
        ),
        (
            35,
            53,
            "Give me a refund for order 53; the item is damaged.",
            "the item is damaged",
            {"eligible": False, "owner_customer_id": 3, "requester_customer_id": 1},
            "denied_cross_user",
        ),
    )
    for index, order_id, message, reason, state, outcome in denials:
        cases.append(
            _case(
                case_id=f"refund-{index:02d}",
                category="refund",
                message=message,
                intent="REFUND",
                tool="refund_order",
                outcome=outcome,
                order_id=order_id,
                reason=reason,
                initial_state={"order_id": order_id, **state},
            )
        )
    return cases


def _cancellation_cases() -> list[dict[str, Any]]:
    allowed = (
        (11, 32, "Cancel newly created order 32.", "CREATED"),
        (12, 33, "Stop paid order 33 before fulfillment.", "PAID"),
        (13, 34, "Please cancel order 34 right away.", "PAID"),
        (14, 35, "I ordered 35 accidentally; cancel it.", "CREATED"),
        (15, 36, "Cancel order 36 before the warehouse packs it.", "PAID"),
        (16, 37, "Do not send order 37; cancel it.", "CREATED"),
        (17, 38, "Withdraw my purchase of order 38.", "PAID"),
        (18, 39, "Cancel unfulfilled order number 39.", "PAID"),
    )
    cases = [
        _case(
            case_id=f"cancel-{index:02d}",
            category="cancellation",
            message=message,
            intent="CANCEL_ORDER",
            tool="cancel_order",
            outcome="cancelled",
            order_id=order_id,
            initial_state={"order_id": order_id, "status": status},
        )
        for index, order_id, message, status in allowed
    ]
    denied = (
        (19, 40, "Cancel shipped order 40.", "SHIPPED", "denied_illegal_state", {}),
        (
            20,
            41,
            "Cancel order 41 even though it was delivered.",
            "DELIVERED",
            "denied_illegal_state",
            {},
        ),
        (21, 42, "Cancel already-cancelled order 42.", "CANCELLED", "denied_illegal_state", {}),
        (22, 43, "Cancel refunded order 43.", "REFUNDED", "denied_illegal_state", {}),
        (
            23,
            54,
            "Cancel order 54 for me.",
            "PAID",
            "denied_cross_user",
            {"owner_customer_id": 2, "requester_customer_id": 1},
        ),
        (
            24,
            55,
            "Please stop order 55 before shipping.",
            "CREATED",
            "denied_cross_user",
            {"owner_customer_id": 4, "requester_customer_id": 1},
        ),
        (
            25,
            56,
            "Withdraw order 56 immediately.",
            "PAID",
            "denied_cross_user",
            {"owner_customer_id": 5, "requester_customer_id": 1},
        ),
    )
    for index, order_id, message, status, outcome, ownership in denied:
        cases.append(
            _case(
                case_id=f"cancel-{index:02d}",
                category="cancellation",
                message=message,
                intent="CANCEL_ORDER",
                tool="cancel_order",
                outcome=outcome,
                order_id=order_id,
                initial_state={"order_id": order_id, "status": status, **ownership},
            )
        )
    return cases


def _policy_cases() -> list[dict[str, Any]]:
    prompts = (
        "Which order states are eligible for cancellation?",
        "Does a refund always need manager approval?",
        "How are damaged products handled?",
        "What proof is needed for a warranty claim?",
        "Do VIP customers get a longer refund window?",
        "What should I do when tracking has not updated?",
        "Can I request a refund before delivery?",
        "How long does refund processing normally take?",
        "What happens if a parcel is lost in transit?",
        "Are opened packages covered by warranty?",
        "Can I cancel an order while it is being packed?",
        "What information is required to track a shipment?",
        "Does the refund policy cover the wrong color?",
        "What is the escalation policy for delayed deliveries?",
        "Are duplicate purchases refundable?",
    )
    return [
        _case(
            case_id=f"policy-{index:02d}",
            category="policy_faq",
            message=message,
            intent="POLICY_QUESTION",
            tool="search_policy",
            outcome="policy_answer_returned",
        )
        for index, message in enumerate(prompts, start=6)
    ]


def _multi_turn_cases() -> list[dict[str, Any]]:
    specifications = (
        (
            6,
            "Is it complete?",
            [
                {"role": "user", "content": "Please check order 32."},
                {"role": "assistant", "content": "I found order 32."},
            ],
            "ORDER_QUERY",
            "get_order",
            "order_status_returned",
            32,
            None,
            {"order_id": 32},
            False,
        ),
        (
            7,
            "What state is that order in?",
            [{"role": "user", "content": "My order number is 33."}],
            "ORDER_QUERY",
            "get_order",
            "order_status_returned",
            33,
            None,
            {"order_id": 33},
            False,
        ),
        (
            8,
            "Did it finish processing?",
            [{"role": "user", "content": "I am asking about order 34."}],
            "ORDER_QUERY",
            "get_order",
            "order_status_returned",
            34,
            None,
            {"order_id": 34},
            False,
        ),
        (
            9,
            "Where is it now?",
            [
                {"role": "user", "content": "Track order 35."},
                {"role": "assistant", "content": "I can check its latest scan."},
            ],
            "SHIPPING_QUERY",
            "get_tracking",
            "tracking_returned",
            35,
            None,
            {"order_id": 35, "shipment_status": "IN_TRANSIT"},
            False,
        ),
        (
            10,
            "When should that package arrive?",
            [{"role": "user", "content": "The package is for order 36."}],
            "SHIPPING_QUERY",
            "get_tracking",
            "tracking_returned",
            36,
            None,
            {"order_id": 36, "shipment_status": "DELAYED"},
            False,
        ),
        (
            11,
            "Please show its latest carrier scan.",
            [{"role": "user", "content": "I need shipping help for order 37."}],
            "SHIPPING_QUERY",
            "get_tracking",
            "tracking_returned",
            37,
            None,
            {"order_id": 37, "shipment_status": "IN_TRANSIT"},
            False,
        ),
        (
            12,
            "Yes, cancel that one.",
            [
                {"role": "user", "content": "Order 38 is still paid."},
                {"role": "assistant", "content": "Would you like to cancel order 38?"},
            ],
            "CANCEL_ORDER",
            "cancel_order",
            "cancelled",
            38,
            None,
            {"order_id": 38, "status": "PAID"},
            False,
        ),
        (
            13,
            "Then stop it before shipping.",
            [{"role": "user", "content": "I placed order 39 by mistake."}],
            "CANCEL_ORDER",
            "cancel_order",
            "cancelled",
            39,
            None,
            {"order_id": 39, "status": "CREATED"},
            False,
        ),
        (
            14,
            "Cancel that order please.",
            [
                {"role": "user", "content": "Can you help with order 44?"},
                {"role": "assistant", "content": "Order 44 is still paid."},
            ],
            "CANCEL_ORDER",
            "cancel_order",
            "cancelled",
            44,
            None,
            {"order_id": 44, "status": "PAID"},
            False,
        ),
        (
            15,
            "It has a cracked case, so refund it.",
            [{"role": "user", "content": "I am contacting you about order 45."}],
            "REFUND",
            "refund_order",
            "waiting_for_approval",
            45,
            "cracked case",
            {"order_id": 45, "eligible": True},
            True,
        ),
        (
            16,
            "The charger is missing. I want my money back.",
            [{"role": "user", "content": "My order is number 46."}],
            "REFUND",
            "refund_order",
            "waiting_for_approval",
            46,
            "the charger is missing",
            {"order_id": 46, "eligible": True},
            True,
        ),
        (
            17,
            "Refund it because the seal was broken.",
            [
                {"role": "user", "content": "Please look at order 47."},
                {"role": "assistant", "content": "What outcome do you want?"},
            ],
            "REFUND",
            "refund_order",
            "waiting_for_approval",
            47,
            "the seal was broken",
            {"order_id": 47, "eligible": True},
            True,
        ),
        (
            18,
            "What does the policy say about that?",
            [{"role": "user", "content": "My parcel arrived late."}],
            "POLICY_QUESTION",
            "search_policy",
            "policy_answer_returned",
            None,
            None,
            {},
            False,
        ),
        (
            19,
            "And how long is the warranty?",
            [
                {"role": "user", "content": "Is accidental damage covered?"},
                {"role": "assistant", "content": "Warranty coverage depends on the policy."},
            ],
            "POLICY_QUESTION",
            "search_policy",
            "policy_answer_returned",
            None,
            None,
            {},
            False,
        ),
        (
            20,
            "Tell me the general cancellation rule instead.",
            [
                {"role": "user", "content": "Cancel my order."},
                {"role": "assistant", "content": "Please provide an order number."},
            ],
            "POLICY_QUESTION",
            "search_policy",
            "policy_answer_returned",
            None,
            None,
            {},
            False,
        ),
    )
    return [
        _case(
            case_id=f"multi-{index:02d}",
            category="multi_turn",
            message=message,
            history=history,
            intent=intent,
            tool=tool,
            outcome=outcome,
            order_id=order_id,
            reason=reason,
            initial_state=state,
            approval=approval,
        )
        for (
            index,
            message,
            history,
            intent,
            tool,
            outcome,
            order_id,
            reason,
            state,
            approval,
        ) in specifications
    ]


def _missing_cases() -> list[dict[str, Any]]:
    return [
        _case(
            case_id="missing-06",
            category="missing_or_failure",
            message="Check my order status.",
            intent="ORDER_QUERY",
            tool=None,
            outcome="clarification_order_id",
        ),
        _case(
            case_id="missing-07",
            category="missing_or_failure",
            message="When will my parcel arrive?",
            intent="SHIPPING_QUERY",
            tool=None,
            outcome="clarification_order_id",
        ),
        _case(
            case_id="missing-08",
            category="missing_or_failure",
            message="Cancel the order I just placed.",
            intent="CANCEL_ORDER",
            tool=None,
            outcome="clarification_order_id",
        ),
        _case(
            case_id="missing-09",
            category="missing_or_failure",
            message="Refund order 57.",
            intent="REFUND",
            tool=None,
            outcome="clarification_reason",
            order_id=57,
            initial_state={"order_id": 57, "eligible": True},
        ),
        _case(
            case_id="missing-10",
            category="missing_or_failure",
            message="Can somebody help me with something?",
            intent="OTHER",
            tool=None,
            outcome="safe_general_response",
        ),
    ]


def main() -> int:
    V2_PATH.parent.mkdir(parents=True, exist_ok=True)
    V2_PATH.write_text(
        json.dumps(build_dataset(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {V2_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
