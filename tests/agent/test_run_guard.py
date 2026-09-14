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
