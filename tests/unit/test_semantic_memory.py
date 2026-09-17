from dataclasses import dataclass

import pytest

from app.agent.models import ChatMessage
from app.memory.models import MemoryCandidate, MemoryExtraction, MemoryToStore, StoredMemory
from app.memory.service import SemanticMemoryService


class ExtractionLLM:
    def __init__(self, extraction: MemoryExtraction) -> None:
        self.extraction = extraction
        self.messages = None

    async def structured_output(self, messages, schema):
        assert schema is MemoryExtraction
        self.messages = messages
        return self.extraction


class Embeddings:
    async def embed_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]

    async def embed_query(self, text):
        return [1.0, 0.0]


@dataclass
class Store:
    stored: list[MemoryToStore]
    available: list[StoredMemory]

    async def add(self, *, customer_id, source_ticket_id, memories):
        assert (customer_id, source_ticket_id) == (7, 11)
        self.stored.extend(memories)
        return len(memories)

    async def list_for_customer(self, customer_id):
        assert customer_id == 7
        return self.available


@pytest.mark.asyncio
async def test_memory_capture_keeps_only_explicit_safe_preferences() -> None:
    store = Store(stored=[], available=[])
    llm = ExtractionLLM(
        MemoryExtraction(
            memories=[
                MemoryCandidate(content=" Please answer in concise English. "),
                MemoryCandidate(content="Email me at private@example.com"),
                MemoryCandidate(content="Use account 123456 for future refunds"),
            ]
        )
    )
    service = SemanticMemoryService(store=store, embeddings=Embeddings(), llm=llm)

    count = await service.remember(
        customer_id=7,
        ticket_id=11,
        messages=[ChatMessage(role="user", content="I prefer concise English replies.")],
        response="Understood.",
    )

    assert count == 1
    assert [memory.content for memory in store.stored] == ["Please answer in concise English."]
    assert llm.messages[0].role == "system"
    assert "untrusted data" in llm.messages[0].content


@pytest.mark.asyncio
async def test_memory_search_returns_only_semantically_close_matches() -> None:
    store = Store(
        stored=[],
        available=[
            StoredMemory(content="Prefers concise English replies.", embedding=[1.0, 0.0]),
            StoredMemory(content="Prefers delivery updates.", embedding=[0.0, 1.0]),
        ],
    )
    service = SemanticMemoryService(
        store=store,
        embeddings=Embeddings(),
        llm=ExtractionLLM(MemoryExtraction()),
    )

    matches = await service.search(customer_id=7, query="How should I respond?")

    assert [match.content for match in matches] == ["Prefers concise English replies."]
    assert matches[0].distance == 0
