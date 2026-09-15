import json
from collections import deque
from time import monotonic
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel

from app.agent.models import ChatMessage, TicketIntent, ToolDecision
from app.observability import get_logger, start_span

logger = get_logger(__name__)

StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


class LLMClient(Protocol):
    async def structured_output(
        self, messages: list[ChatMessage], schema: type[StructuredModel]
    ) -> StructuredModel: ...

    async def tool_decision(
        self,
        messages: list[ChatMessage],
        intent: TicketIntent,
        available_tools: tuple[str, ...],
    ) -> ToolDecision: ...

    async def generate(self, messages: list[ChatMessage]) -> str: ...


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def structured_output(
        self, messages: list[ChatMessage], schema: type[StructuredModel]
    ) -> StructuredModel:
        schema_instruction = ChatMessage(
            role="system",
            content=(
                "Return only one JSON object matching this JSON Schema: "
                + json.dumps(schema.model_json_schema(), separators=(",", ":"))
            ),
        )
        content = await self._chat(
            [schema_instruction, *messages], response_format={"type": "json_object"}
        )
        return schema.model_validate_json(self._strip_code_fence(content))

    async def tool_decision(
        self,
        messages: list[ChatMessage],
        intent: TicketIntent,
        available_tools: tuple[str, ...],
    ) -> ToolDecision:
        instruction = ChatMessage(
            role="system",
            content=(
                f"Choose answer_directly or one tool from {available_tools}. "
                f"Validated intent: {intent.model_dump_json()}."
            ),
        )
        return await self.structured_output([instruction, *messages], ToolDecision)

    async def generate(self, messages: list[ChatMessage]) -> str:
        return await self._chat(messages)

    async def _chat(
        self,
        messages: list[ChatMessage],
        *,
        response_format: dict[str, str] | None = None,
    ) -> str:
        started = monotonic()
        with start_span("llm.call", llm_model=self.model):
            try:
                payload: dict[str, Any] = {
                    "model": self.model,
                    "messages": [message.model_dump() for message in messages],
                    "temperature": 0,
                }
                if response_format is not None:
                    payload["response_format"] = response_format
                headers = {"Authorization": f"Bearer {self.api_key}"}
                async with httpx.AsyncClient(
                    timeout=self.timeout_seconds, transport=self.transport
                ) as client:
                    response = await client.post(self.endpoint, headers=headers, json=payload)
                    response.raise_for_status()
                body = response.json()
                content = str(body["choices"][0]["message"]["content"])
            except Exception as exc:
                logger.exception(
                    "llm_call_failed",
                    model=self.model,
                    error_type=type(exc).__name__,
                    latency_ms=_elapsed_ms(started),
                )
                raise
            logger.info(
                "llm_call_completed",
                model=self.model,
                latency_ms=_elapsed_ms(started),
            )
            return content

    @staticmethod
    def _strip_code_fence(content: str) -> str:
        stripped = content.strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            first_newline = stripped.find("\n")
            return stripped[first_newline + 1 : -3].strip()
        return stripped


def _elapsed_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))


class MockLLMClient:
    def __init__(
        self,
        *,
        intents: list[TicketIntent],
        decisions: list[ToolDecision] | None = None,
        responses: list[str] | None = None,
    ) -> None:
        self.intents = deque(intents)
        self.decisions = deque(decisions or [])
        self.responses = deque(responses or [])
        self.calls: list[str] = []

    async def structured_output(
        self, messages: list[ChatMessage], schema: type[StructuredModel]
    ) -> StructuredModel:
        self.calls.append("structured_output")
        if schema is not TicketIntent or not self.intents:
            raise AssertionError("unexpected structured_output call")
        return schema.model_validate(self.intents.popleft())

    async def tool_decision(
        self,
        messages: list[ChatMessage],
        intent: TicketIntent,
        available_tools: tuple[str, ...],
    ) -> ToolDecision:
        self.calls.append("tool_decision")
        if not self.decisions:
            raise AssertionError("unexpected tool_decision call")
        return self.decisions.popleft()

    async def generate(self, messages: list[ChatMessage]) -> str:
        self.calls.append("generate")
        if not self.responses:
            raise AssertionError("unexpected generate call")
        return self.responses.popleft()
