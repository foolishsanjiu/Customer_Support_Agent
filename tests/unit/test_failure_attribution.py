from app.agent.failures import (
    FailureCategory,
    FailureStage,
    RepairAction,
    attribute_execution_failure,
    attribute_verification_failure,
    decide_repair,
)
from app.core.errors import MCPToolError, ToolPermissionDenied


def test_read_only_dependency_failure_is_repairable_within_budget() -> None:
    failure = attribute_execution_failure(
        MCPToolError("logistics unavailable"),
        tool_name="get_tracking",
        read_only=True,
    )

    decision = decide_repair(failure, attempts=0, max_attempts=1)

    assert decision.stage is FailureStage.EXECUTION
    assert decision.category is FailureCategory.DEPENDENCY_UNAVAILABLE
    assert decision.repair_action is RepairAction.REPLAN
    assert decision.attempt == 1


def test_authorization_failure_cannot_be_repaired() -> None:
    failure = attribute_execution_failure(
        ToolPermissionDenied("permission denied"),
        tool_name="get_order",
        read_only=True,
    )

    decision = decide_repair(failure, attempts=0, max_attempts=1)

    assert decision.category is FailureCategory.AUTHORIZATION_DENIED
    assert decision.repairable is False
    assert decision.repair_action is RepairAction.STOP


def test_write_verification_mismatch_cannot_replay_business_action() -> None:
    failure = attribute_verification_failure(
        tool_name="refund_order",
        read_only=False,
    )

    decision = decide_repair(failure, attempts=0, max_attempts=1)

    assert decision.category is FailureCategory.VERIFICATION_MISMATCH
    assert decision.repair_action is RepairAction.STOP
