from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


class MemoryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=500)


class MemoryExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memories: list[MemoryCandidate] = Field(default_factory=list, max_length=3)


class SemanticMemoryMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    distance: float = Field(ge=0)


@dataclass(frozen=True)
class MemoryToStore:
    content: str
    content_hash: str
    embedding: list[float]


@dataclass(frozen=True)
class StoredMemory:
    content: str
    embedding: list[float]
