import pytest

from app.agent.run_guard import RunAction, RunTrigger, decide_run_action
from app.models.enums import AgentRunStatus


@pytest.mark.parametrize(
    ("status", "trigger", "checkpoint", "attempts", "expected"),
    [
        (AgentRunStatus.PENDING, RunTrigger.START, False, 0, RunAction.START),
        (AgentRunStatus.PENDING, RunTrigger.RECOVERY, False, 0, RunAction.START),
        (AgentRunStatus.RUNNING, RunTrigger.START, True, 0, RunAction.NOOP),
        (AgentRunStatus.PENDING, RunTrigger.APPROVAL_RESUME, False, 0, RunAction.NOOP),
        (
            AgentRunStatus.RESUME_PENDING,
            RunTrigger.APPROVAL_RESUME,
            True,
            0,
            RunAction.RESUME,
        ),
        (
            AgentRunStatus.RESUME_PENDING,
            RunTrigger.APPROVAL_RESUME,
            False,
            0,
            RunAction.RECOVERY_REQUIRED,
        ),
        (
            AgentRunStatus.RUNNING,
            RunTrigger.RECOVERY,
            True,
            0,
            RunAction.RESUME,
        ),
        (
            AgentRunStatus.RUNNING,
            RunTrigger.RECOVERY,
            True,
            3,
            RunAction.RECOVERY_REQUIRED,
        ),
        (
            AgentRunStatus.WAITING_APPROVAL,
            RunTrigger.RECOVERY,
            True,
            0,
            RunAction.NOOP,
        ),
        (AgentRunStatus.SUCCEEDED, RunTrigger.RECOVERY, True, 0, RunAction.NOOP),
        (AgentRunStatus.FAILED, RunTrigger.APPROVAL_RESUME, True, 0, RunAction.NOOP),
        (
            AgentRunStatus.CANCEL_REQUESTED,
            RunTrigger.RECOVERY,
            True,
            0,
            RunAction.CANCEL,
        ),
        (AgentRunStatus.CANCELLED, RunTrigger.START, False, 0, RunAction.NOOP),
        (
            AgentRunStatus.RECOVERY_REQUIRED,
            RunTrigger.RECOVERY,
            True,
            0,
            RunAction.NOOP,
        ),
    ],
)
def test_run_state_matrix(
    status: AgentRunStatus,
    trigger: RunTrigger,
    checkpoint: bool,
    attempts: int,
    expected: RunAction,
) -> None:
    assert (
        decide_run_action(
            status,
            trigger,
            checkpoint_exists=checkpoint,
            recovery_attempts=attempts,
            max_recovery_attempts=3,
        )
        is expected
    )


@pytest.mark.parametrize(
    ("status", "checkpoint", "current_node", "expected"),
    [
        (AgentRunStatus.FAILED, True, "execute_tool", RunAction.RESUME),
        (AgentRunStatus.FAILED, False, "START", RunAction.START),
        (
            AgentRunStatus.FAILED,
            False,
            "execute_tool",
            RunAction.RECOVERY_REQUIRED,
        ),
        (
            AgentRunStatus.RECOVERY_REQUIRED,
            True,
            "execute_tool",
            RunAction.RESUME,
        ),
    ],
)
def test_dlq_replay_requires_checkpoint_after_execution_started(
    status: AgentRunStatus,
    checkpoint: bool,
    current_node: str,
    expected: RunAction,
) -> None:
    assert (
        decide_run_action(
            status,
            RunTrigger.DLQ_REPLAY,
            checkpoint_exists=checkpoint,
            recovery_attempts=3,
            max_recovery_attempts=3,
            current_node=current_node,
        )
        is expected
    )


def test_busy_conversation_keeps_new_run_queued() -> None:
    assert (
        decide_run_action(
            AgentRunStatus.PENDING,
            RunTrigger.START,
            checkpoint_exists=False,
            recovery_attempts=0,
            max_recovery_attempts=3,
            ticket_busy=True,
        )
        is RunAction.NOOP
    )
