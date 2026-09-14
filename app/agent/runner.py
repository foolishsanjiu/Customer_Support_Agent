from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.llm import LLMClient
from app.agent.state import AgentState
from app.agent.store import DatabaseAgentStore
from app.agent.tools import RuntimeToolAdapter
from app.agent.workflow import AgentWorkflow


class AgentRunner:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        llm: LLMClient,
        max_steps: int = 12,
    ) -> None:
        self.store = DatabaseAgentStore(session_factory)
        self.workflow = AgentWorkflow(
            llm=llm,
            store=self.store,
            tools=RuntimeToolAdapter(session_factory),
            max_steps=max_steps,
        )
        self.max_steps = max_steps

    async def run_ticket(self, ticket_id: int, customer_id: int) -> AgentState:
        run = await self.store.create_run(ticket_id)
        initial: AgentState = {
            "run_id": run.id,
            "ticket_id": ticket_id,
            "customer_id": customer_id,
            "messages": [],
            "intent": None,
            "order": None,
            "shipment": None,
            "plan": None,
            "pending_tool_calls": [],
            "tool_results": [],
            "risk_level": None,
            "approval_status": None,
            "final_response": None,
            "errors": [],
            "retry_count": 0,
            "trace_id": uuid4().hex,
            "step_count": 0,
            "verification_complete": False,
            "needs_more_action": False,
        }
        try:
            return await self.workflow.graph.ainvoke(
                initial, config={"recursion_limit": self.max_steps + 5}
            )
        except Exception as exc:
            await self.store.fail_run(run.id, exc)
            raise
