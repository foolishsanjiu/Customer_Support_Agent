from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.agent.models import IntentType


class FunctionalCategory(StrEnum):
    ORDER = "order"
    SHIPPING = "shipping"
    REFUND = "refund"
    CANCELLATION = "cancellation"
    POLICY_FAQ = "policy_faq"
    MULTI_TURN = "multi_turn"
    MISSING_OR_FAILURE = "missing_or_failure"


class SecurityCategory(StrEnum):
    DIRECT_PROMPT_INJECTION = "direct_prompt_injection"
    INDIRECT_RAG_INJECTION = "indirect_rag_injection"
    MALICIOUS_MCP_OUTPUT = "malicious_mcp_output"
    CROSS_USER_ACCESS = "cross_user_access"
    UNAUTHORIZED_TOOL = "unauthorized_tool"
    APPROVAL_BYPASS = "approval_bypass"
    DUPLICATE_REPLAY_REFUND = "duplicate_replay_refund"
    TOOL_ARGUMENT_TAMPERING = "tool_argument_tampering"


class EvalMessage(BaseModel):
    role: str
    content: str


class FunctionalCase(BaseModel):
    id: str
    category: FunctionalCategory
    user_message: str
    conversation_history: list[EvalMessage] = Field(default_factory=list)
    initial_state: dict[str, Any] = Field(default_factory=dict)
    expected_intent: IntentType
    expected_entities: dict[str, Any] = Field(default_factory=dict)
    expected_tools: list[str] = Field(default_factory=list)
    expected_tool_arguments: dict[str, dict[str, Any]] = Field(default_factory=dict)
    forbidden_tools: list[str] = Field(default_factory=list)
    expected_outcome: str
    should_escalate: bool = False
    should_require_approval: bool = False

    @model_validator(mode="after")
    def arguments_reference_expected_tools(self) -> "FunctionalCase":
        unknown = set(self.expected_tool_arguments) - set(self.expected_tools)
        if unknown:
            raise ValueError(f"tool arguments reference unexpected tools: {sorted(unknown)}")
        return self


class FunctionalObservation(BaseModel):
    case_id: str
    actual_intent: IntentType
    actual_entities: dict[str, Any] = Field(default_factory=dict)
    actual_tools: list[str] = Field(default_factory=list)
    actual_tool_arguments: dict[str, dict[str, Any]] = Field(default_factory=dict)
    actual_outcome: str
    escalated: bool = False
    required_approval: bool = False
    agent_steps: int = Field(ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class SecurityCase(BaseModel):
    id: str
    category: SecurityCategory
    attack: str
    expected_control: str
    expected_outcome: str


class SecurityObservation(BaseModel):
    case_id: str
    control_held: bool
    policy_violation: bool = False
    unauthorized_execution: bool = False
    approval_bypass: bool = False
    cross_user_data_leakage: bool = False
    duplicate_business_action: bool = False


class EvalMetadata(BaseModel):
    git_commit: str
    dataset_version: str
    prompt_version: str
    eval_provider: str
    eval_model_id: str
    eval_model_version_or_snapshot: str
    eval_response_models: list[str] = Field(default_factory=list)
    eval_system_fingerprints: list[str] = Field(default_factory=list)
    temperature: float
    seed_if_supported: int | None = None
    eval_config_version: str
    timestamp: datetime


class EvalReport(BaseModel):
    metadata: EvalMetadata
    functional_metrics: dict[str, float | int] = Field(default_factory=dict)
    functional_category_metrics: dict[str, dict[str, float | int]] = Field(default_factory=dict)
    security_metrics: dict[str, float | int] = Field(default_factory=dict)


class GateResult(BaseModel):
    passed: bool
    comparable_to_baseline: bool
    failures: list[str] = Field(default_factory=list)
    absolute_quality_thresholds: dict[str, float] = Field(default_factory=dict)
    category_quality_thresholds: dict[str, float] = Field(default_factory=dict)
