import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import monotonic
from typing import Any
from uuid import uuid4

import httpx
import jwt
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.infrastructure.database.session import create_database_engine, create_session_factory
from app.models import (
    AgentRun,
    Approval,
    AuditLog,
    Customer,
    IdempotencyRecord,
    Order,
    Refund,
    Ticket,
    TicketMessage,
    ToolCall,
)
from app.models.enums import (
    AgentRunStatus,
    ApprovalStatus,
    CustomerLevel,
    CustomerStatus,
    IdempotencyStatus,
    OrderStatus,
    PrincipalRole,
    RefundStatus,
    SenderType,
    TicketStatus,
    ToolCallStatus,
)

TERMINAL_FAILURES = {
    AgentRunStatus.FAILED.value,
    AgentRunStatus.RECOVERY_REQUIRED.value,
}


@dataclass
class GoldenPathIds:
    customer_id: int | None = None
    order_id: int | None = None
    ticket_id: int | None = None
    run_id: int | None = None
    approval_id: int | None = None


class GoldenPathFailure(RuntimeError):
    def __init__(self, stage: str, ids: GoldenPathIds, cause: Exception) -> None:
        super().__init__(str(cause))
        self.stage = stage
        self.ids = ids


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify the deployed ResolveX refund golden path.")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--poll-interval", type=float, default=1)
    parser.add_argument("--keep-data", action="store_true")
    return parser.parse_args()


async def run_golden_path(
    *,
    api_url: str,
    max_wait_seconds: float,
    poll_interval: float,
    keep_data: bool,
) -> dict[str, Any]:
    settings = get_settings()
    _validate_settings(settings)
    engine = create_database_engine(settings.database_url)
    sessions = create_session_factory(engine)
    ids = GoldenPathIds()
    stage = "create_fixture"
    started = monotonic()
    succeeded = False
    try:
        ids.customer_id, ids.order_id = await _create_fixture(sessions)
        customer_headers = {
            "Authorization": "Bearer "
            + create_jwt(
                settings,
                subject=f"customer-{ids.customer_id}",
                role=PrincipalRole.CUSTOMER,
                customer_id=ids.customer_id,
            )
        }
        manager_headers = {
            "Authorization": "Bearer "
            + create_jwt(settings, subject="e2e-manager", role=PrincipalRole.MANAGER)
        }
        async with httpx.AsyncClient(base_url=api_url.rstrip("/"), timeout=10) as client:
            stage = "create_ticket"
            ticket = await request_json(
                client,
                "POST",
                "/api/v1/tickets",
                expected_status=201,
                json={
                    "customer_id": ids.customer_id,
                    "order_id": ids.order_id,
                    "category": "REFUND",
                    "subject": f"Refund order {ids.order_id} because it arrived damaged",
                },
            )
            ids.ticket_id = int(ticket["id"])
            await request_json(
                client,
                "POST",
                f"/api/v1/tickets/{ids.ticket_id}/messages",
                expected_status=201,
                json={
                    "sender_type": "CUSTOMER",
                    "content": "Please return the payment to the original method.",
                },
            )

            stage = "enqueue_agent"
            run = await request_json(
                client,
                "POST",
                "/api/v1/agent-runs",
                expected_status=202,
                headers=customer_headers,
                json={"ticket_id": ids.ticket_id},
            )
            ids.run_id = int(run["run_id"])

            stage = "wait_for_approval"
            await wait_for_run_status(
                client,
                ids.run_id,
                AgentRunStatus.WAITING_APPROVAL,
                headers=customer_headers,
                max_wait_seconds=max_wait_seconds,
                poll_interval=poll_interval,
            )
            await _wait_for_checkpoint(
                settings,
                ids.run_id,
                max_wait_seconds=max_wait_seconds,
                poll_interval=poll_interval,
            )

            stage = "approve"
            approvals = await request_json(
                client,
                "GET",
                "/api/v1/approvals?status=PENDING",
                expected_status=200,
                headers=manager_headers,
            )
            approval = next(
                (item for item in approvals if int(item["run_id"]) == ids.run_id),
                None,
            )
            if approval is None:
                raise RuntimeError("pending approval for the AgentRun was not found")
            ids.approval_id = int(approval["id"])
            decided = await request_json(
                client,
                "POST",
                f"/api/v1/approvals/{ids.approval_id}/approve",
                expected_status=200,
                headers=manager_headers,
                json={"reason": "E2E refund evidence accepted"},
            )
            if decided["status"] != ApprovalStatus.APPROVED.value:
                raise RuntimeError("approval API did not persist APPROVED")

            stage = "wait_for_resume"
            await wait_for_run_status(
                client,
                ids.run_id,
                AgentRunStatus.SUCCEEDED,
                headers=customer_headers,
                max_wait_seconds=max_wait_seconds,
                poll_interval=poll_interval,
            )

            stage = "verify_api_state"
            order = await request_json(
                client,
                "GET",
                f"/api/v1/orders/{ids.order_id}",
                expected_status=200,
            )
            ticket = await request_json(
                client,
                "GET",
                f"/api/v1/tickets/{ids.ticket_id}",
                expected_status=200,
            )
            if order["status"] != OrderStatus.REFUNDED.value:
                raise RuntimeError("order API did not return REFUNDED")
            if ticket["status"] != TicketStatus.RESOLVED.value:
                raise RuntimeError("ticket API did not return RESOLVED")

        stage = "verify_database_audit"
        evidence = await _verify_database(sessions, ids)
        succeeded = True
        return {
            "status": "ok",
            "elapsed_seconds": round(monotonic() - started, 3),
            "ids": asdict(ids),
            "evidence": evidence,
            "cleaned_up": not keep_data,
        }
    except Exception as exc:
        raise GoldenPathFailure(stage, ids, exc) from exc
    finally:
        if succeeded and not keep_data:
            await _cleanup(sessions, settings, ids)
        await engine.dispose()


def create_jwt(
    settings: Settings,
    *,
    subject: str,
    role: PrincipalRole,
    customer_id: int | None = None,
) -> str:
    if settings.jwt_secret is None or not settings.jwt_secret.get_secret_value():
        raise RuntimeError("JWT_SECRET is required")
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": subject,
        "role": role.value,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp()),
    }
    if customer_id is not None:
        claims["customer_id"] = customer_id
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm="HS256")


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    expected_status: int,
    **kwargs: Any,
) -> Any:
    response = await client.request(method, path, **kwargs)
    if response.status_code != expected_status:
        raise RuntimeError(
            f"{method} {path} returned {response.status_code}, expected {expected_status}: "
            f"{response.text[:500]}"
        )
    return response.json()


async def wait_for_run_status(
    client: httpx.AsyncClient,
    run_id: int,
    target: AgentRunStatus,
    *,
    headers: dict[str, str],
    max_wait_seconds: float,
    poll_interval: float,
) -> dict[str, Any]:
    deadline = monotonic() + max_wait_seconds
    last_status = "unknown"
    while monotonic() < deadline:
        run = await request_json(
            client,
            "GET",
            f"/api/v1/agent-runs/{run_id}",
            expected_status=200,
            headers=headers,
        )
        last_status = run["status"]
        if last_status == target.value:
            return run
        if last_status in TERMINAL_FAILURES:
            raise RuntimeError(f"AgentRun entered terminal failure state {last_status}")
        await asyncio.sleep(poll_interval)
    raise TimeoutError(
        f"AgentRun did not reach {target.value} within {max_wait_seconds:g}s; "
        f"last status={last_status}"
    )


def _validate_settings(settings: Settings) -> None:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required")
    if settings.jwt_secret is None or not settings.jwt_secret.get_secret_value():
        raise RuntimeError("JWT_SECRET is required")


async def _create_fixture(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[int, int]:
    marker = uuid4().hex
    now = datetime.now(UTC).replace(tzinfo=None)
    async with sessions.begin() as session:
        customer = Customer(
            name="Golden Path Customer",
            email=f"golden-path-{marker}@resolvex.example",
            level=CustomerLevel.NORMAL,
            status=CustomerStatus.ACTIVE,
        )
        session.add(customer)
        await session.flush()
        order = Order(
            customer_id=customer.id,
            status=OrderStatus.DELIVERED,
            total_amount=Decimal("88.00"),
            paid_at=now - timedelta(days=5),
            shipped_at=now - timedelta(days=4),
            delivered_at=now - timedelta(days=1),
        )
        session.add(order)
        await session.flush()
        return customer.id, order.id


async def _wait_for_checkpoint(
    settings: Settings,
    run_id: int,
    *,
    max_wait_seconds: float,
    poll_interval: float,
) -> None:
    deadline = monotonic() + max_wait_seconds
    async with AsyncRedisSaver.from_conn_string(settings.langgraph_redis_url) as saver:
        config = {"configurable": {"thread_id": str(run_id)}}
        while monotonic() < deadline:
            if await saver.aget_tuple(config) is not None:
                return
            await asyncio.sleep(poll_interval)
    raise TimeoutError("WAITING_APPROVAL run has no Redis checkpoint")


async def _verify_database(
    sessions: async_sessionmaker[AsyncSession], ids: GoldenPathIds
) -> dict[str, Any]:
    async with sessions() as session:
        run = await session.get(AgentRun, ids.run_id)
        approval = await session.get(Approval, ids.approval_id)
        order = await session.get(Order, ids.order_id)
        ticket = await session.get(Ticket, ids.ticket_id)
        refund = await session.scalar(select(Refund).where(Refund.order_id == ids.order_id))
        tool_call = await session.scalar(
            select(ToolCall).where(
                ToolCall.agent_run_id == ids.run_id,
                ToolCall.tool_name == "refund_order",
            )
        )
        audit_events = set(
            await session.scalars(select(AuditLog.event_type).where(AuditLog.run_id == ids.run_id))
        )
        agent_message = await session.scalar(
            select(TicketMessage)
            .where(
                TicketMessage.ticket_id == ids.ticket_id,
                TicketMessage.sender_type == SenderType.AGENT,
            )
            .order_by(TicketMessage.id.desc())
        )
        _require(run is not None and run.status is AgentRunStatus.SUCCEEDED, "run not SUCCEEDED")
        _require(run.success is True, "run success flag is not true")
        _require(
            approval is not None and approval.status is ApprovalStatus.APPROVED,
            "approval not APPROVED",
        )
        _require(order is not None and order.status is OrderStatus.REFUNDED, "order not REFUNDED")
        _require(
            ticket is not None and ticket.status is TicketStatus.RESOLVED, "ticket not RESOLVED"
        )
        _require(refund is not None and refund.status is RefundStatus.SUCCESS, "refund not SUCCESS")
        _require(
            tool_call is not None and tool_call.status is ToolCallStatus.SUCCEEDED,
            "refund tool call not SUCCEEDED",
        )
        idempotency = await session.get(IdempotencyRecord, f"{ids.run_id}:{tool_call.tool_call_id}")
        _require(
            idempotency is not None and idempotency.status is IdempotencyStatus.SUCCEEDED,
            "idempotency record not SUCCEEDED",
        )
        _require(
            {"approval_requested", "approval_approved"}.issubset(audit_events),
            "approval audit events are incomplete",
        )
        _require(
            agent_message is not None and bool(agent_message.content), "agent response missing"
        )
        return {
            "run_status": run.status.value,
            "approval_status": approval.status.value,
            "order_status": order.status.value,
            "refund_status": refund.status.value,
            "tool_call_status": tool_call.status.value,
            "idempotency_status": idempotency.status.value,
            "audit_events": sorted(audit_events),
            "agent_response_present": True,
        }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


async def _cleanup(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    ids: GoldenPathIds,
) -> None:
    async with sessions.begin() as session:
        if ids.run_id is not None:
            await session.execute(delete(AuditLog).where(AuditLog.run_id == ids.run_id))
            await session.execute(
                delete(IdempotencyRecord).where(IdempotencyRecord.key.like(f"{ids.run_id}:%"))
            )
            await session.execute(delete(ToolCall).where(ToolCall.agent_run_id == ids.run_id))
            await session.execute(delete(Approval).where(Approval.run_id == ids.run_id))
        if ids.order_id is not None:
            await session.execute(delete(Refund).where(Refund.order_id == ids.order_id))
        if ids.run_id is not None:
            await session.execute(delete(AgentRun).where(AgentRun.id == ids.run_id))
        if ids.ticket_id is not None:
            await session.execute(
                delete(TicketMessage).where(TicketMessage.ticket_id == ids.ticket_id)
            )
            await session.execute(delete(Ticket).where(Ticket.id == ids.ticket_id))
        if ids.order_id is not None:
            await session.execute(delete(Order).where(Order.id == ids.order_id))
        if ids.customer_id is not None:
            await session.execute(delete(Customer).where(Customer.id == ids.customer_id))
    if ids.run_id is not None:
        async with AsyncRedisSaver.from_conn_string(settings.langgraph_redis_url) as saver:
            await saver.adelete_thread(str(ids.run_id))


def main() -> int:
    args = parse_args()
    try:
        result = asyncio.run(
            run_golden_path(
                api_url=args.api_url,
                max_wait_seconds=args.timeout,
                poll_interval=args.poll_interval,
                keep_data=args.keep_data,
            )
        )
    except GoldenPathFailure as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "stage": exc.stage,
                    "ids": asdict(exc.ids),
                    "error": str(exc),
                    "data_retained": True,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "stage": "setup_or_cleanup",
                    "error": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
