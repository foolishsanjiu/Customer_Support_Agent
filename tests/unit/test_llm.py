import json

import httpx
import pytest

from app.agent.llm import MockLLMClient, OpenAICompatibleClient
from app.agent.models import ChatMessage, IntentType, TicketIntent
from app.core.errors import ExternalServiceUnavailable
from app.resilience import CircuitBreaker, CircuitState


@pytest.mark.asyncio
async def test_openai_compatible_structured_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://model.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "test-model"
        assert payload["temperature"] == 0
        assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "model": "test-model-snapshot",
                "system_fingerprint": "fp_test",
                "usage": {"prompt_tokens": 12, "completion_tokens": 5},
                "choices": [
                    {
                        "message": {
                            "content": (
                                "```json\n"
                                '{"intent":"ORDER_QUERY","order_id":2,'
                                '"reason":null,"confidence":1}\n'
                                "```"
                            )
                        }
                    }
                ],
            },
        )

    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://model.example/v1",
        model="test-model",
        transport=httpx.MockTransport(handler),
    )

    intent = await client.structured_output(
        [ChatMessage(role="user", content="Where is order 2?")], TicketIntent
    )

    assert intent.intent is IntentType.ORDER_QUERY
    assert intent.order_id == 2
    assert client.call_records[0].requested_model == "test-model"
    assert client.call_records[0].response_model == "test-model-snapshot"
    assert client.call_records[0].system_fingerprint == "fp_test"
    assert client.call_records[0].input_tokens == 12
    assert client.call_records[0].output_tokens == 5


@pytest.mark.asyncio
async def test_openai_compatible_generate() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "verified response"}}]}
        )

    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://model.example/v1",
        model="test-model",
        transport=httpx.MockTransport(handler),
    )

    response = await client.generate([ChatMessage(role="user", content="hello")])

    assert response == "verified response"


@pytest.mark.asyncio
async def test_tool_decision_requires_business_validation_at_tool_boundary() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        instruction = " ".join(
            message["content"] for message in payload["messages"] if message["role"] == "system"
        )
        assert "Do not pre-judge" in instruction
        assert "refund_order" in instruction
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "action": "tool_call",
                                    "tool_name": "refund_order",
                                    "arguments": {},
                                }
                            )
                        }
                    }
                ]
            },
        )

    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://model.example/v1",
        model="test-model",
        transport=httpx.MockTransport(handler),
    )

    decision = await client.tool_decision(
        [ChatMessage(role="user", content="Refund already-refunded order 4 again.")],
        TicketIntent(intent=IntentType.REFUND, order_id=4, reason="refund again", confidence=1),
        ("refund_order",),
    )

    assert decision.tool_name == "refund_order"


@pytest.mark.asyncio
async def test_mock_llm_rejects_unconfigured_calls() -> None:
    client = MockLLMClient(intents=[])
    messages = [ChatMessage(role="user", content="hello")]
    with pytest.raises(AssertionError, match="structured_output"):
        await client.structured_output(messages, TicketIntent)
    with pytest.raises(AssertionError, match="tool_decision"):
        await client.tool_decision(
            messages,
            TicketIntent(intent=IntentType.OTHER, confidence=1),
            (),
        )
    with pytest.raises(AssertionError, match="generate"):
        await client.generate(messages)


@pytest.mark.asyncio
async def test_llm_circuit_breaker_fast_fails_after_repeated_outage() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    breaker = CircuitBreaker("llm", failure_threshold=2, recovery_timeout_seconds=30)
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://model.example/v1",
        model="test-model",
        transport=httpx.MockTransport(handler),
        circuit_breaker=breaker,
    )

    with pytest.raises(ExternalServiceUnavailable) as first:
        await client.generate([ChatMessage(role="user", content="hello")])
    with pytest.raises(ExternalServiceUnavailable):
        await client.generate([ChatMessage(role="user", content="hello")])
    with pytest.raises(ExternalServiceUnavailable) as fast_failure:
        await client.generate([ChatMessage(role="user", content="hello")])

    assert first.value.retry_after_seconds is None
    assert fast_failure.value.retry_after_seconds is not None
    assert calls == 2
    assert breaker.snapshot().state is CircuitState.OPEN
