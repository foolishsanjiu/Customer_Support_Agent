import json
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.memory.models import MemoryToStore, StoredMemory
from app.models import SemanticMemory


class MemoryStore(Protocol):
    async def add(
        self,
        *,
        customer_id: int,
        source_ticket_id: int,
        memories: list[MemoryToStore],
    ) -> int: ...

    async def list_for_customer(self, customer_id: int) -> list[StoredMemory]: ...


class DatabaseMemoryStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def add(
        self,
        *,
        customer_id: int,
        source_ticket_id: int,
        memories: list[MemoryToStore],
    ) -> int:
        if not memories:
            return 0
        hashes = [memory.content_hash for memory in memories]
        async with self.session_factory.begin() as session:
            existing = set(
                await session.scalars(
                    select(SemanticMemory.content_hash).where(
                        SemanticMemory.customer_id == customer_id,
                        SemanticMemory.content_hash.in_(hashes),
                    )
                )
            )
            additions = [memory for memory in memories if memory.content_hash not in existing]
            session.add_all(
                SemanticMemory(
                    customer_id=customer_id,
                    source_ticket_id=source_ticket_id,
                    content=memory.content,
                    content_hash=memory.content_hash,
                    embedding=json.dumps(memory.embedding, separators=(",", ":")),
                )
                for memory in additions
            )
            return len(additions)

    async def list_for_customer(self, customer_id: int) -> list[StoredMemory]:
        async with self.session_factory() as session:
            records = list(
                await session.scalars(
                    select(SemanticMemory)
                    .where(SemanticMemory.customer_id == customer_id)
                    .order_by(SemanticMemory.updated_at.desc(), SemanticMemory.id.desc())
                    .limit(100)
                )
            )
        return [
            StoredMemory(content=record.content, embedding=json.loads(record.embedding))
            for record in records
        ]
