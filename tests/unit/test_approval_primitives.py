import pytest

from app.approvals.service import ApprovalService, action_fingerprint


def test_approval_service_rejects_invalid_ttl() -> None:
    with pytest.raises(ValueError, match="positive"):
        ApprovalService(None, ttl_minutes=0)  # type: ignore[arg-type]


def test_action_fingerprint_is_canonical() -> None:
    assert action_fingerprint({"b": 2, "a": 1}) == action_fingerprint({"a": 1, "b": 2})
