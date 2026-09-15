from types import SimpleNamespace

import pytest

from app.agent.store import DatabaseAgentStore
from app.core.errors import ObjectAccessDenied
from app.models import AgentRun, Ticket


class SessionContext:
    def __init__(self, session) -> None:
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *args) -> None:
        pass


@pytest.mark.asyncio
async def test_get_run_enforces_ticket_ownership() -> None:
    run = SimpleNamespace(id=4, ticket_id=8)

    class Session:
        async def get(self, model, object_id):
            if model is AgentRun:
                return run
            assert (model, object_id) == (Ticket, 8)
            return SimpleNamespace(customer_id=9)

    store = DatabaseAgentStore(lambda: SessionContext(Session()))

    assert await store.get_run(4, 9) is run
    with pytest.raises(ObjectAccessDenied, match="does not belong"):
        await store.get_run(4, 10)
