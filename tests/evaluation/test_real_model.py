import json
from collections import deque

import httpx
import pytest

from app.agent.llm import OpenAICompatibleClient
from app.evaluation.loader import load_functional_cases
from app.evaluation.models import FunctionalCase
from app.evaluation.real_model import capture_functional_case


@pytest.mark.asyncio
async def test_capture_runs_production_workflow_and_collects_usage() -> None:
    replies = deque(
        [
            {
                "content": json.dumps(
                    {
                        "intent": "ORDER_QUERY",
                        "order_id": 1,
                        "reason": None,
                        "confidence": 1,
                    }
                ),
                "tokens": (20, 8),
            },
            {
                "content": json.dumps(
                    {"action": "tool_call", "tool_name": "get_order", "arguments": {}}
                ),
                "tokens": (30, 6),
            },
            {"content": "Order status returned.", "tokens": (40, 5)},
        ]
    )

    def handler(_: httpx.Request) -> httpx.Response:
        reply = replies.popleft()
        return httpx.Response(
            200,
            json={
                "model": "snapshot-1",
                "system_fingerprint": "fp_1",
                "usage": {
                    "prompt_tokens": reply["tokens"][0],
                    "completion_tokens": reply["tokens"][1],
                },
                "choices": [{"message": {"content": reply["content"]}}],
            },
        )

    case = load_functional_cases("evals/datasets/functional_v1.json")[0]
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://model.example/v1",
        model="alias",
        transport=httpx.MockTransport(handler),
    )

    capture = await capture_functional_case(case, client, run_id=1)

    assert capture.observation.case_id == "order-01"
    assert capture.observation.actual_tools == ["get_order"]
    assert capture.observation.actual_outcome == "order_status_returned"
    assert capture.observation.input_tokens == 90
    assert capture.observation.output_tokens == 19
    assert {record.system_fingerprint for record in capture.call_records} == {"fp_1"}
    assert not replies


@pytest.mark.asyncio
async def test_capture_supports_order_list_without_order_id() -> None:
    replies = deque(
        [
            json.dumps(
                {
                    "intent": "ORDER_LIST",
                    "order_id": None,
                    "reason": None,
                    "confidence": 1,
                }
            ),
            json.dumps(
                {
                    "action": "tool_call",
                    "tool_name": "list_customer_orders",
                    "arguments": {},
                }
            ),
            "You have two orders.",
        ]
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": replies.popleft()}}]},
        )

    case = FunctionalCase.model_validate(
        {
            "id": "order-list-01",
            "category": "order",
            "user_message": "How many orders do I have?",
            "expected_intent": "ORDER_LIST",
            "expected_tools": ["list_customer_orders"],
            "expected_outcome": "order_list_returned",
        }
    )
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://model.example/v1",
        model="alias",
        transport=httpx.MockTransport(handler),
    )

    capture = await capture_functional_case(case, client, run_id=1)

    assert capture.observation.actual_tools == ["list_customer_orders"]
    assert capture.observation.actual_tool_arguments == {"list_customer_orders": {}}
    assert capture.observation.actual_outcome == "order_list_returned"
    assert not replies


@pytest.mark.asyncio
async def test_capture_supports_latest_refund_status_without_order_id() -> None:
    replies = deque(
        [
            json.dumps(
                {
                    "intent": "REFUND_STATUS",
                    "order_id": None,
                    "reason": None,
                    "confidence": 1,
                }
            ),
            json.dumps(
                {
                    "action": "tool_call",
                    "tool_name": "get_refund_status",
                    "arguments": {},
                }
            ),
            "Your latest refund succeeded.",
        ]
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": replies.popleft()}}]},
        )

    case = FunctionalCase.model_validate(
        {
            "id": "refund-status-01",
            "category": "refund",
            "user_message": "Did my latest refund succeed?",
            "expected_intent": "REFUND_STATUS",
            "expected_tools": ["get_refund_status"],
            "expected_outcome": "refund_status_returned",
        }
    )
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://model.example/v1",
        model="alias",
        transport=httpx.MockTransport(handler),
    )

    capture = await capture_functional_case(case, client, run_id=1)

    assert capture.observation.actual_tools == ["get_refund_status"]
    assert capture.observation.actual_tool_arguments == {"get_refund_status": {}}
    assert capture.observation.actual_outcome == "refund_status_returned"
    assert not replies


@pytest.mark.asyncio
async def test_capture_social_thanks_uses_deterministic_response_without_model_call() -> None:
    def unexpected_call(_: httpx.Request) -> httpx.Response:
        raise AssertionError("social acknowledgement must not call the model")

    case = FunctionalCase.model_validate(
        {
            "id": "social-01",
            "category": "multi_turn",
            "user_message": "谢谢你",
            "conversation_history": [{"role": "assistant", "content": "订单 #2 已退款成功。"}],
            "expected_intent": "SOCIAL",
            "expected_outcome": "social_response",
        }
    )
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://model.example/v1",
        model="alias",
        transport=httpx.MockTransport(unexpected_call),
    )

    capture = await capture_functional_case(case, client, run_id=1)

    assert capture.observation.actual_intent.value == "SOCIAL"
    assert capture.observation.actual_tools == []
    assert capture.observation.actual_outcome == "social_response"
    assert capture.call_records == ()


@pytest.mark.asyncio
async def test_capture_records_pending_refund_approval_without_execution() -> None:
    replies = deque(
        [
            json.dumps(
                {
                    "intent": "REFUND",
                    "order_id": 9,
                    "reason": "item arrived damaged",
                    "confidence": 1,
                }
            ),
            json.dumps({"action": "tool_call", "tool_name": "refund_order", "arguments": {}}),
        ]
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": replies.popleft()}}]},
        )

    case = next(
        case
        for case in load_functional_cases("evals/datasets/functional_v1.json")
        if case.id == "refund-01"
    )
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://model.example/v1",
        model="alias",
        transport=httpx.MockTransport(handler),
    )

    capture = await capture_functional_case(case, client, run_id=1)

    assert capture.observation.actual_tools == ["refund_order"]
    assert capture.observation.actual_outcome == "waiting_for_approval"
    assert capture.observation.required_approval is True
    assert not replies
