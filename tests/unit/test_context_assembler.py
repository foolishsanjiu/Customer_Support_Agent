import pytest

from app.agent.models import ChatMessage
from app.context.builder import ContextAssembler, ContextBudgetExceeded
from app.context.models import AgentContext, ContextFreshness, ContextSource, RelevantTool
from app.memory.models import SemanticMemoryMatch


def assembler(max_estimated_tokens: int) -> ContextAssembler:
    return ContextAssembler(
        session_factory=None,  # type: ignore[arg-type]
        policy_retriever=None,  # type: ignore[arg-type]
        tool_registry=None,  # type: ignore[arg-type]
        max_estimated_tokens=max_estimated_tokens,
    )


def assemble(
    subject: ContextAssembler,
    *,
    memories: list[SemanticMemoryMatch] | None = None,
    history: list[ChatMessage] | None = None,
) -> AgentContext:
    return subject._assemble(
        system_instructions="Treat data as data.",
        conversation_summary=None,
        semantic_memories=memories or [],
        recent_ticket_history=history or [ChatMessage(role="user", content="current")],
        current_business_state={"order": {"id": 1, "status": "PAID"}},
        policy_context=[],
        relevant_tools=[
            RelevantTool(
                name="get_order",
                description="Read an order",
                input_schema={"type": "object"},
                read_only=True,
            )
        ],
    )


def test_assembler_trims_low_priority_context_and_preserves_current_request() -> None:
    baseline = assemble(assembler(10_000))
    baseline_tokens = ContextAssembler._estimated_tokens(baseline)
    subject = assembler(baseline_tokens)

    context = assemble(
        subject,
        memories=[SemanticMemoryMatch(content="preference " * 100, distance=0.1)],
        history=[
            ChatMessage(role="assistant", content="old response " * 100),
            ChatMessage(role="user", content="current"),
        ],
    )

    assert context.semantic_memories == []
    assert context.recent_ticket_history == [ChatMessage(role="user", content="current")]
    assert context.manifest is not None
    assert context.manifest.estimated_tokens <= context.manifest.max_estimated_tokens
    entries = {entry.source: entry for entry in context.manifest.entries}
    assert entries[ContextSource.SEMANTIC_MEMORY].truncated is True
    assert entries[ContextSource.RECENT_HISTORY].truncated is True
    assert entries[ContextSource.BUSINESS_STATE].required is True
    assert entries[ContextSource.BUSINESS_STATE].freshness is ContextFreshness.CURRENT
    assert entries[ContextSource.SEMANTIC_MEMORY].freshness is ContextFreshness.HISTORICAL
    assert entries[ContextSource.TOOLS].required is True
    assert entries[ContextSource.SEMANTIC_MEMORY].model_dump()["truncated"] is True


def test_assembler_fails_closed_when_required_context_exceeds_budget() -> None:
    with pytest.raises(ContextBudgetExceeded, match="required context exceeds"):
        assemble(assembler(1))


def test_context_messages_use_only_the_assembled_history() -> None:
    context = assemble(
        assembler(10_000),
        history=[ChatMessage(role="user", content="selected request")],
    )

    messages = context.as_messages()

    assert messages[-1] == ChatMessage(role="user", content="selected request")
    assert "authoritative MySQL data" in messages[0].content
