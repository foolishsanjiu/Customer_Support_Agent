import asyncio
from pathlib import Path
from typing import Any

from app.policy.embeddings import EmbeddingClient
from app.policy.loader import load_policy_directory
from app.policy.models import PolicyMatch


class ChromaPolicyRetriever:
    def __init__(
        self,
        *,
        path: str,
        policy_directory: str,
        embeddings: EmbeddingClient,
        top_k: int = 3,
        collection_name: str = "resolvex-policies",
        client: Any | None = None,
    ) -> None:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        import chromadb

        chroma_client = client or chromadb.PersistentClient(path=path)
        self.collection = chroma_client.get_or_create_collection(
            collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        self.policy_directory = Path(policy_directory)
        self.embeddings = embeddings
        self.top_k = top_k
        self._indexed = False
        self._index_lock = asyncio.Lock()

    async def ensure_indexed(self) -> None:
        if self._indexed:
            return
        async with self._index_lock:
            if self._indexed:
                return
            chunks = load_policy_directory(self.policy_directory)
            vectors = await self.embeddings.embed_documents([chunk.content for chunk in chunks])
            current_ids = [chunk.id for chunk in chunks]
            existing = await asyncio.to_thread(self.collection.get)
            stale_ids = list(set(existing.get("ids", [])) - set(current_ids))
            if stale_ids:
                await asyncio.to_thread(self.collection.delete, ids=stale_ids)
            await asyncio.to_thread(
                self.collection.upsert,
                ids=current_ids,
                documents=[chunk.content for chunk in chunks],
                metadatas=[chunk.metadata() for chunk in chunks],
                embeddings=vectors,
            )
            self._indexed = True

    async def search(
        self, query: str, *, policy_type: str | None = None, top_k: int | None = None
    ) -> list[PolicyMatch]:
        if not query.strip():
            raise ValueError("policy query must not be empty")
        await self.ensure_indexed()
        vector = await self.embeddings.embed_query(query)
        result = await asyncio.to_thread(
            self.collection.query,
            query_embeddings=[vector],
            n_results=top_k or self.top_k,
            where={"policy_type": policy_type} if policy_type else None,
            include=["documents", "metadatas", "distances"],
        )
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        return [
            PolicyMatch(content=document, distance=distance, **metadata)
            for document, metadata, distance in zip(documents, metadatas, distances, strict=True)
        ]
