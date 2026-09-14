from pydantic import BaseModel

from app.models.enums import PolicyDecision, PrincipalRole, ToolRiskLevel


class RiskDecision(BaseModel):
    decision: PolicyDecision
    risk_level: ToolRiskLevel
    reason: str
    required_role: PrincipalRole | None = None
