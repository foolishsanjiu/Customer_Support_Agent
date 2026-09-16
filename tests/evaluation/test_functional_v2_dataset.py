import json
from collections import Counter
from pathlib import Path

import pytest

from app.agent.models import PlanAction, TicketIntent, ToolDecision
from app.evaluation.loader import load_functional_cases
from app.evaluation.real_model import capture_functional_case
from app.evaluation.scoring import score_functional
from scripts.build_functional_v2 import build_dataset

V1_PATH = Path("evals/datasets/functional_v1.json")
V2_PATH = Path("evals/datasets/functional_v2.json")


class OracleLLM:
    def __init__(self, case) -> None:
        self.case = case
        self.call_records = []

    async def structured_output(self, messages, schema):
        return TicketIntent(
            intent=self.case.expected_intent,
            order_id=self.case.expected_entities.get("order_id"),
            reason=self.case.expected_entities.get("reason"),
            confidence=1,
        )

    async def tool_decision(self, messages, intent, available_tools):
        if not self.case.expected_tools:
            return ToolDecision(action=PlanAction.ANSWER_DIRECTLY)
        tool = self.case.expected_tools[0]
        assert available_tools == (tool,)
        return ToolDecision(action=PlanAction.TOOL_CALL, tool_name=tool)

    async def generate(self, messages):
        return "Oracle evaluation response."


def test_functional_v2_is_reproducible_and_preserves_v1() -> None:
    v1 = json.loads(V1_PATH.read_text(encoding="utf-8"))
    v2 = json.loads(V2_PATH.read_text(encoding="utf-8"))

    assert v2 == build_dataset()
    assert v2[: len(v1)] == v1


def test_functional_v2_has_balanced_unique_coverage() -> None:
    cases = load_functional_cases(V2_PATH)
    ids = [case.id for case in cases]
    conversations = [
        (
            *((message.role, message.content) for message in case.conversation_history),
            case.user_message,
        )
        for case in cases
    ]

    assert len(cases) == 150
    assert len(set(ids)) == 150
    assert len(set(conversations)) == 150
    assert Counter(case.category.value for case in cases) == {
        "order": 20,
        "shipping": 20,
        "refund": 35,
        "cancellation": 25,
        "policy_faq": 20,
        "multi_turn": 20,
        "missing_or_failure": 10,
    }
    assert {tool for case in cases for tool in case.expected_tools} == {
        "get_order",
        "get_tracking",
        "refund_order",
        "cancel_order",
        "search_policy",
    }
    assert {
        "waiting_for_approval",
        "denied_already_refunded",
        "denied_outside_window",
        "denied_not_delivered",
        "denied_cross_user",
        "denied_illegal_state",
        "clarification_order_id",
        "clarification_reason",
        "not_found",
        "safe_general_response",
    } <= {case.expected_outcome for case in cases}


@pytest.mark.asyncio
async def test_functional_v2_executes_through_evaluation_fixture() -> None:
    cases = load_functional_cases(V2_PATH)
    observations = []

    for run_id, case in enumerate(cases, start=1):
        capture = await capture_functional_case(case, OracleLLM(case), run_id=run_id)  # type: ignore[arg-type]
        observations.append(capture.observation)

    metrics = score_functional(cases, observations)

    assert metrics["case_count"] == 150
    assert metrics["task_success_rate"] == 1.0
    assert metrics["tool_selection_accuracy"] == 1.0
