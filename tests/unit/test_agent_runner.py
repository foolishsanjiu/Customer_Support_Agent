from types import SimpleNamespace

import pytest

from app.agent.runner import AgentRunner


@pytest.mark.asyncio
async def test_recover_continues_checkpointed_graph() -> None:
    calls: list[tuple] = []

    class Graph:
        async def ainvoke(self, value, *, config):
            calls.append((value, config))
            return {"run_id": 4}

    runner = AgentRunner.__new__(AgentRunner)
    runner.max_steps = 12
    runner.workflow = SimpleNamespace(graph=Graph())
    runner.store = SimpleNamespace()

    result = await runner.recover(4)

    assert result == {"run_id": 4}
    assert calls == [
        (
            None,
            {
                "recursion_limit": 20,
                "configurable": {"thread_id": "4"},
            },
        )
    ]


@pytest.mark.asyncio
async def test_recover_records_failure() -> None:
    failures: list[tuple[int, Exception]] = []

    class Graph:
        async def ainvoke(self, value, *, config):
            raise RuntimeError("checkpoint failed")

    class Store:
        async def fail_run(self, run_id, error):
            failures.append((run_id, error))

    runner = AgentRunner.__new__(AgentRunner)
    runner.max_steps = 12
    runner.workflow = SimpleNamespace(graph=Graph())
    runner.store = Store()

    with pytest.raises(RuntimeError, match="checkpoint failed"):
        await runner.recover(4)

    assert failures[0][0] == 4
    assert str(failures[0][1]) == "checkpoint failed"
