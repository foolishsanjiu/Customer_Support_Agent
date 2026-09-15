from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.approvals.service import ApprovalService
from app.core.errors import BusinessError
from app.models.enums import PolicyDecision, PrincipalRole, ToolRiskLevel
from app.policy.decisions import RiskDecision
from app.tool_runtime.models import RefundOrderInput, ToolExecutionContext

TOOL_RISKS = {
    "cancel_order": ToolRiskLevel.L2,
    "refund_order": ToolRiskLevel.L3,
    "update_ticket": ToolRiskLevel.L2,
    "escalate_ticket": ToolRiskLevel.L2,
}


class RiskPolicyEngine:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.approvals = ApprovalService(session_factory)

    async def evaluate(
        self,
        tool_name: str,
        arguments: dict,
        context: ToolExecutionContext,
    ) -> RiskDecision:
        risk = TOOL_RISKS.get(tool_name, ToolRiskLevel.L1)
        if risk is not ToolRiskLevel.L3:
            return RiskDecision(decision=PolicyDecision.ALLOW, risk_level=risk, reason="allowed")
        if tool_name != "refund_order":
            return RiskDecision(
                decision=PolicyDecision.DENY,
                risk_level=risk,
                reason="unknown L3 tool",
            )
        try:
            validated = RefundOrderInput.model_validate(arguments)
            async with self.approvals.session_factory() as session:
                await self.approvals.validate_refund_scope(session, validated, context, lock=False)
        except BusinessError as exc:
            return RiskDecision(
                decision=PolicyDecision.DENY,
                risk_level=risk,
                reason=str(exc),
            )
        return RiskDecision(
            decision=PolicyDecision.REQUIRE_MANAGER_APPROVAL,
            risk_level=risk,
            reason="L3 refund requires a manager decision",
            required_role=PrincipalRole.MANAGER,
        )
