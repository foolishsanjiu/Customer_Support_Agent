from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import dlq
from app.core.errors import ObjectAccessDenied
from app.main import create_app
from app.models.enums import DeadLetterStatus, PrincipalRole
from app.security.principal import AuthenticatedPrincipal


def record(status=DeadLetterStatus.OPEN):
    now = datetime(2026, 9, 17, 16, 0)
    return SimpleNamespace(
        id=3,
        run_id=4,
        approval_id=None,
        task_name="resolvex.run_agent",
        task_id="task-1",
        status=status,
        reason_code="task_exception",
        error_type="RuntimeError",
        failure_count=1,
        replay_count=0,
        replayed_by=None,
        last_replayed_at=None,
        resolved_at=None,
        created_at=now,
        updated_at=now,
    )


def request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_session_factory="db")))


def admin():
    return AuthenticatedPrincipal("operator-1", PrincipalRole.ADMIN)


@pytest.mark.asyncio
async def test_dlq_listing_is_admin_only(monkeypatch) -> None:
    class Store:
        def __init__(self, sessions):
            assert sessions == "db"

        async def list(self, status, *, limit):
            assert (status, limit) == (DeadLetterStatus.OPEN, 25)
            return [record()]

    monkeypatch.setattr(dlq, "DeadLetterStore", Store)
    result = await dlq.list_dead_letters(admin(), request(), DeadLetterStatus.OPEN, 25)

    assert [item.id for item in result] == [3]
    with pytest.raises(ObjectAccessDenied, match="administrator"):
        await dlq.list_dead_letters(
            AuthenticatedPrincipal("customer-1", PrincipalRole.CUSTOMER, 1),
            request(),
            DeadLetterStatus.OPEN,
            25,
        )


@pytest.mark.asyncio
async def test_replay_claims_record_before_enqueue(monkeypatch) -> None:
    events = []
    replaying = record(DeadLetterStatus.REPLAYING)
    replaying.replay_count = 1
    replaying.replayed_by = "operator-1"
    replaying.last_replayed_at = datetime(2026, 9, 17, 16, 1)

    class Store:
        def __init__(self, sessions):
            pass

        async def request_replay(self, dead_letter_id, *, actor_id):
            events.append(("claim", dead_letter_id, actor_id))
            return replaying

        async def reopen(self, dead_letter_id, *, reason_code):
            events.append(("reopen", dead_letter_id, reason_code))

    class Task:
        def delay(self, dead_letter_id):
            events.append(("enqueue", dead_letter_id))

    monkeypatch.setattr(dlq, "DeadLetterStore", Store)
    monkeypatch.setattr(dlq, "replay_dead_letter", Task())

    response = await dlq.replay(3, admin(), request())

    assert response.status is DeadLetterStatus.REPLAYING
    assert events == [("claim", 3, "operator-1"), ("enqueue", 3)]


@pytest.mark.asyncio
async def test_enqueue_failure_reopens_dead_letter(monkeypatch) -> None:
    events = []
    replaying = record(DeadLetterStatus.REPLAYING)

    class Store:
        def __init__(self, sessions):
            pass

        async def request_replay(self, dead_letter_id, *, actor_id):
            return replaying

        async def reopen(self, dead_letter_id, *, reason_code):
            events.append((dead_letter_id, reason_code))

    class Task:
        def delay(self, dead_letter_id):
            raise RuntimeError("broker unavailable")

    monkeypatch.setattr(dlq, "DeadLetterStore", Store)
    monkeypatch.setattr(dlq, "replay_dead_letter", Task())

    with pytest.raises(HTTPException) as raised:
        await dlq.replay(3, admin(), request())

    assert raised.value.status_code == 503
    assert events == [(3, "replay_enqueue_failed")]


def test_dlq_routes_are_exposed() -> None:
    paths = create_app().openapi()["paths"]

    assert "/api/v1/dlq" in paths
    assert "/api/v1/dlq/{dead_letter_id}/replay" in paths
