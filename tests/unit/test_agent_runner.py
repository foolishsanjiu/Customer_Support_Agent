from types import SimpleNamespace

import pytest

from app.agent import runner as runner_module
from app.agent.cancellation import AgentRunCancellation
from app.agent.runner import AgentRunner


@pytest.mark.asyncio
async def test_recover_continues_checkpointed_graph(monkeypatch) -> None:
    calls: list[tuple] = []
    metrics = []
    monkeypatch.setattr(runner_module, "record_agent_run", lambda **values: metrics.append(values))

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
    assert metrics[0]["trigger"] == "recovery"
    assert metrics[0]["outcome"] == "succeeded"


@pytest.mark.asyncio
async def test_recover_records_failure(monkeypatch) -> None:
    failures: list[tuple[int, Exception]] = []
    metrics = []
    monkeypatch.setattr(runner_module, "record_agent_run", lambda **values: metrics.append(values))

    class Graph:
        async def ainvoke(self, value, *, config):
            raise RuntimeError("checkpoint failed")

    class Store:
        async def cancellation_requested(self, run_id):
            return False

        async def fail_run(self, run_id, error):
            failures.append((run_id, error))
            return True

    runner = AgentRunner.__new__(AgentRunner)
    runner.max_steps = 12
    runner.workflow = SimpleNamespace(graph=Graph())
    runner.store = Store()

    with pytest.raises(RuntimeError, match="checkpoint failed"):
        await runner.recover(4)

    assert failures[0][0] == 4
    assert str(failures[0][1]) == "checkpoint failed"
    assert metrics[0]["trigger"] == "recovery"
    assert metrics[0]["outcome"] == "failed"


@pytest.mark.asyncio
async def test_recover_finalizes_cooperative_cancellation(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(
        runner_module,
        "record_agent_run",
        lambda **values: events.append(("metric", values["outcome"])),
    )

    class Graph:
        async def ainvoke(self, value, *, config):
            raise AgentRunCancellation()

    class Store:
        async def finalize_cancellation(self, run_id):
            events.append(("finalize", run_id))
            return True

    runner = AgentRunner.__new__(AgentRunner)
    runner.max_steps = 12
    runner.workflow = SimpleNamespace(graph=Graph())
    runner.store = Store()

    result = await runner.recover(4)

    assert result["business_outcome"] == "cancelled"
    assert events == [("finalize", 4), ("metric", "cancelled")]


@pytest.mark.asyncio
async def test_failure_race_prefers_accepted_cancellation(monkeypatch) -> None:
    events = []
    monkeypatch.setattr(
        runner_module,
        "record_agent_run",
        lambda **values: events.append(("metric", values["outcome"])),
    )

    class Graph:
        async def ainvoke(self, value, *, config):
            raise RuntimeError("dependency failed during cancellation race")

    class Store:
        async def cancellation_requested(self, run_id):
            return False

        async def fail_run(self, run_id, error):
            events.append(("fail_or_cancel", run_id))
            return False

        async def finalize_cancellation(self, run_id):
            events.append(("finalize", run_id))
            return False

    runner = AgentRunner.__new__(AgentRunner)
    runner.max_steps = 12
    runner.workflow = SimpleNamespace(graph=Graph())
    runner.store = Store()

    result = await runner.recover(4)

    assert result["business_outcome"] == "cancelled"
    assert events == [
        ("fail_or_cancel", 4),
        ("finalize", 4),
        ("metric", "cancelled"),
    ]
