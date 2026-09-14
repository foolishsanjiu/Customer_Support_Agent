import asyncio
import hashlib
import math
import re
import threading
from typing import Protocol

_MODEL_CACHE: dict[tuple[str, str | None], object] = {}
_MODEL_CACHE_LOCK = threading.Lock()


def _load_sentence_transformer(model_name: str, cache_folder: str | None):
    key = (model_name, cache_folder)
    with _MODEL_CACHE_LOCK:
        model = _MODEL_CACHE.get(key)
        if model is None:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(
                model_name,
                cache_folder=cache_folder,
                local_files_only=True,
            )
            _MODEL_CACHE[key] = model
        return model


class EmbeddingClient(Protocol):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class BGEEmbeddingClient:
    def __init__(self, model_name: str, cache_folder: str | None = None) -> None:
        self.model_name = model_name
        self.cache_folder = cache_folder
        self._model = None
        self._lock = asyncio.Lock()

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        model = await self._get_model()
        vectors = await asyncio.to_thread(
            model.encode,
            texts,
            batch_size=8,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        return vectors.tolist()

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]

    async def _get_model(self):
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is None:
                self._model = await asyncio.to_thread(
                    _load_sentence_transformer,
                    self.model_name,
                    self.cache_folder,
                )
        return self._model


class DeterministicEmbeddingClient:
    """Small lexical embedding for deterministic tests; not used in production."""

    def __init__(self, dimensions: int = 64) -> None:
        self.dimensions = dimensions

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in re.findall(r"[\w-]+", text.lower()):
            digest = hashlib.sha256(token.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += 1.0 if digest[4] % 2 else -1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]
