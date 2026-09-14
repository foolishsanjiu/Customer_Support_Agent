from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class PolicyChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    policy_id: str
    policy_type: str
    version: str
    section: str
    updated_at: date
    content: str = Field(min_length=1)

    def metadata(self) -> dict[str, str]:
        return {
            "policy_id": self.policy_id,
            "policy_type": self.policy_type,
            "version": self.version,
            "section": self.section,
            "updated_at": self.updated_at.isoformat(),
        }


class PolicyMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: str
    policy_type: str
    version: str
    section: str
    updated_at: date
    content: str
    distance: float
