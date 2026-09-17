from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import chat
from app.core.errors import ObjectAccessDenied
from app.main import create_app
from app.models.enums import PrincipalRole, SenderType, TicketCategory, TicketStatus
from app.schemas.tickets import CustomerMessageCreateRequest, CustomerTicketCreateRequest
from app.security.principal import AuthenticatedPrincipal
from app.services.tickets import TicketService


def customer(customer_id: int = 4) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(str(customer_id), PrincipalRole.CUSTOMER, customer_id)


def test_chat_page_and_assets_use_browser_security_headers() -> None:
    client = TestClient(create_app())

    response = client.get("/chat")
    script = client.get("/chat/assets/app.js")
    stylesheet = client.get("/chat/assets/app.css")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "/chat/assets/app.js" in response.text
    assert "/chat/assets/app.css" in response.text
    assert script.status_code == 200
    assert stylesheet.status_code == 200


def test_chat_does_not_persist_token_or_render_untrusted_html() -> None:
    source = (chat.CHAT_ASSET_DIR / "app.js").read_text(encoding="utf-8")
    stylesheet = (chat.CHAT_ASSET_DIR / "app.css").read_text(encoding="utf-8")

    assert "localStorage" not in source
    assert "sessionStorage" not in source
    assert "document.cookie" not in source
    assert "innerHTML" not in source
    assert "/api/v1/chat/tickets" in source
    assert "/api/v1/agent-runs" in source
    assert "TextDecoder" in source
    assert "[hidden] { display: none !important; }" in stylesheet


@pytest.mark.asyncio
async def test_customer_chat_routes_bind_authenticated_customer(monkeypatch) -> None:
    calls: list[tuple] = []
    timestamp = datetime.now(UTC)
    ticket = SimpleNamespace(
        id=8,
        customer_id=4,
        order_id=7,
        status=TicketStatus.OPEN,
        category=TicketCategory.CANCELLATION,
        subject="Cancel order 7",
        created_at=timestamp,
        updated_at=timestamp,
        resolved_at=None,
    )
    message = SimpleNamespace(id=9, sender_type=SenderType.CUSTOMER)

    class FakeService:
        def __init__(self, session) -> None:
            assert session == "session"

        async def list_customer_tickets(self, customer_id, limit):
            calls.append(("list", customer_id, limit))
            return [ticket]

        async def create_ticket(self, **values):
            calls.append(("create", values))
            return ticket

        async def get_customer_ticket(self, ticket_id, customer_id):
            calls.append(("get", ticket_id, customer_id))
            return ticket, []

        async def add_customer_message(self, ticket_id, customer_id, content):
            calls.append(("message", ticket_id, customer_id, content))
            return message

    monkeypatch.setattr(chat, "TicketService", FakeService)
    principal = customer()
    create_payload = CustomerTicketCreateRequest(
        order_id=7,
        category=TicketCategory.CANCELLATION,
        subject="Cancel order 7",
    )

    assert await chat.list_tickets(principal, "session", 20) == [ticket]
    assert await chat.create_ticket(create_payload, principal, "session") is ticket
    detail = await chat.get_ticket(8, principal, "session")
    assert detail.id == 8
    added = await chat.add_message(
        8,
        CustomerMessageCreateRequest(content="Please cancel it."),
        principal,
        "session",
    )
    assert added is message
    assert calls == [
        ("list", 4, 20),
        (
            "create",
            {
                "customer_id": 4,
                "order_id": 7,
                "category": TicketCategory.CANCELLATION,
                "subject": "Cancel order 7",
            },
        ),
        ("get", 8, 4),
        ("message", 8, 4, "Please cancel it."),
    ]


@pytest.mark.asyncio
async def test_customer_ticket_service_enforces_ownership() -> None:
    owned = SimpleNamespace(id=8, customer_id=4)

    class Repository:
        async def get_ticket(self, ticket_id):
            assert ticket_id == 8
            return owned

        async def list_messages(self, ticket_id):
            assert ticket_id == 8
            return []

    service = TicketService.__new__(TicketService)
    service.repository = Repository()

    assert await service.get_customer_ticket(8, 4) == (owned, [])
    with pytest.raises(ObjectAccessDenied, match="ticket does not belong to customer"):
        await service.get_customer_ticket(8, 5)


@pytest.mark.asyncio
async def test_customer_message_forces_customer_sender_and_owner() -> None:
    owned = SimpleNamespace(id=8, customer_id=4)
    added = []

    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class Session:
        def begin(self):
            return Transaction()

        async def flush(self):
            return None

        async def refresh(self, message):
            message.id = 9

    class Repository:
        async def get_ticket(self, ticket_id):
            assert ticket_id == 8
            return owned

        def add_message(self, message):
            added.append(message)

    service = TicketService.__new__(TicketService)
    service.session = Session()
    service.repository = Repository()

    message = await service.add_customer_message(8, 4, "  Please cancel it.  ")
    assert message.sender_type is SenderType.CUSTOMER
    assert message.content == "Please cancel it."
    assert added == [message]

    with pytest.raises(ObjectAccessDenied, match="ticket does not belong to customer"):
        await service.add_customer_message(8, 5, "Do not accept this")


@pytest.mark.asyncio
async def test_customer_chat_rejects_non_customer_principal() -> None:
    manager = AuthenticatedPrincipal("7", PrincipalRole.MANAGER, None)

    with pytest.raises(ObjectAccessDenied, match="customer identity is required"):
        await chat.list_tickets(manager, "unused", 20)


def test_chat_routes_and_assets_are_packaged() -> None:
    application = create_app()
    paths = application.openapi()["paths"]
    project = Path(__file__).resolve().parents[2]
    configuration = (project / "pyproject.toml").read_text(encoding="utf-8")

    assert "/api/v1/chat/tickets" in paths
    assert "/api/v1/chat/tickets/{ticket_id}" in paths
    assert "/api/v1/chat/tickets/{ticket_id}/messages" in paths
    assert '"chat/*.html"' in configuration
    assert '"chat/static/*.js"' in configuration
