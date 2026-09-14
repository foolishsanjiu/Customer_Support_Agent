import json

import httpx
import pytest

from app.agent.llm import OpenAICompatibleClient
from app.agent.models import ChatMessage, IntentType, TicketIntent


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
                ]
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
