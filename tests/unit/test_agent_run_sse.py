import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.agent_runs import _agent_run_events, _run_event_id
from app.models.enums import AgentRunStatus


def run(status, *, node, updated_at):
    return SimpleNamespace(id=7, status=status, current_node=node, updated_at=updated_at)


class Request:
    def __init__(self, last_event_id=None):
        self.headers = {} if last_event_id is None else {"last-event-id": last_event_id}

    async def is_disconnected(self):
        return False


class Store:
    def __init__(self, *runs):
        self.runs = list(runs)
        self.calls = []

    async def get_run(self, run_id, customer_id):
        self.calls.append((run_id, customer_id))
        return self.runs.pop(0)


@pytest.mark.asyncio
async def test_sse_emits_changes_and_stops_at_terminal_state() -> None:
    now = datetime(2026, 9, 17, 10, 0, 0)
    pending = run(AgentRunStatus.PENDING, node="START", updated_at=now)
    duplicate = run(AgentRunStatus.PENDING, node="START", updated_at=now)
    succeeded = run(
        AgentRunStatus.SUCCEEDED,
        node="persist",
        updated_at=now + timedelta(seconds=1),
    )
    store = Store(duplicate, succeeded)

    events = [
        event
        async for event in _agent_run_events(
            Request(), store, 7, 4, pending, poll_interval_seconds=0
        )
    ]

    assert [json.loads(event["data"])["status"] for event in events] == [
        "PENDING",
        "SUCCEEDED",
    ]
    assert json.loads(events[-1]["data"])["terminal"] is True
    assert store.calls == [(7, 4), (7, 4)]


@pytest.mark.asyncio
async def test_sse_last_event_id_suppresses_replayed_snapshot() -> None:
    now = datetime(2026, 9, 17, 10, 0, 0)
    pending = run(AgentRunStatus.PENDING, node="START", updated_at=now)
    failed = run(
        AgentRunStatus.FAILED,
        node="execute",
        updated_at=now + timedelta(seconds=1),
    )

    events = [
        event
        async for event in _agent_run_events(
            Request(_run_event_id(pending)),
            Store(failed),
            7,
            4,
            pending,
            poll_interval_seconds=0,
        )
    ]

    assert len(events) == 1
    assert json.loads(events[0]["data"]) == {
        "run_id": 7,
        "status": "FAILED",
        "current_node": "execute",
        "updated_at": "2026-09-17T10:00:01",
        "terminal": True,
    }
