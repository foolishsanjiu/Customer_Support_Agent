from types import SimpleNamespace

import pytest

from app.api import agent_runs as agent_runs_api
from app.api import approvals as approvals_api
from app.core.errors import ObjectAccessDenied
from app.models.enums import ApprovalStatus, PrincipalRole
from app.schemas.approvals import AgentRunCreateRequest, ApprovalDecisionRequest
from app.security.principal import AuthenticatedPrincipal


class FakeApprovalService:
    def __init__(self) -> None:
        self.approval = SimpleNamespace(id=7, run_id=9, status=ApprovalStatus.PENDING)
        self.decisions: list[bool] = []

    async def list(self, status):
        return [self.approval]

    async def get(self, approval_id):
        assert approval_id == 7
        return self.approval

    async def decide(self, **values):
        self.decisions.append(values["approve"])
        self.approval.status = (
            ApprovalStatus.APPROVED if values["approve"] else ApprovalStatus.REJECTED
        )
        return self.approval


class FakeTask:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def delay(self, *values) -> None:
        self.calls.append(values)


@pytest.mark.asyncio
async def test_approval_endpoints_require_manager_and_enqueue_resume(monkeypatch) -> None:
    service = FakeApprovalService()
    task = FakeTask()
    monkeypatch.setattr(approvals_api, "_service", lambda request: service)
    monkeypatch.setattr(approvals_api, "resume_agent_run", task)
    request = SimpleNamespace()
    manager = AuthenticatedPrincipal("manager-1", PrincipalRole.MANAGER)

    assert await approvals_api.list_approvals(request, manager) == [service.approval]
    assert await approvals_api.get_approval(7, request, manager) is service.approval
    payload = ApprovalDecisionRequest(reason="evidence accepted")
    await approvals_api.approve(7, payload, request, manager)
    await approvals_api.reject(7, payload, request, manager)
    assert service.decisions == [True, False]
    assert task.calls == [(9, 7), (9, 7)]

    with pytest.raises(ObjectAccessDenied):
        await approvals_api.list_approvals(
            request,
            AuthenticatedPrincipal("customer-1", PrincipalRole.CUSTOMER, 1),
        )


@pytest.mark.asyncio
async def test_agent_run_endpoint_validates_customer_and_enqueues(monkeypatch) -> None:
    task = FakeTask()

    class FakeStore:
        def __init__(self, session_factory) -> None:
            pass

        async def create_run(self, ticket_id, customer_id):
            assert (ticket_id, customer_id) == (3, 4)
            return SimpleNamespace(id=5, status=SimpleNamespace(value="PENDING"))

    monkeypatch.setattr(agent_runs_api, "DatabaseAgentStore", FakeStore)
    monkeypatch.setattr(agent_runs_api, "run_agent", task)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_session_factory=None)))
    customer = AuthenticatedPrincipal("4", PrincipalRole.CUSTOMER, 4)
    response = await agent_runs_api.create_agent_run(
        AgentRunCreateRequest(ticket_id=3), customer, request
    )
    assert (response.run_id, response.status) == (5, "PENDING")
    assert task.calls == [(5,)]

    with pytest.raises(ObjectAccessDenied):
        await agent_runs_api.create_agent_run(
            AgentRunCreateRequest(ticket_id=3),
            AuthenticatedPrincipal("manager", PrincipalRole.MANAGER),
            request,
        )
