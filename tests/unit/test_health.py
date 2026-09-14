from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.api import health
from app.main import app


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client


def test_liveness(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness_when_dependencies_are_healthy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def healthy(_: object) -> bool:
        return True

    monkeypatch.setattr(health, "check_database", healthy)
    monkeypatch.setattr(health, "check_redis", healthy)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_readiness_fails_closed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def unhealthy(_: object) -> bool:
        return False

    monkeypatch.setattr(health, "check_database", unhealthy)
    monkeypatch.setattr(health, "check_redis", unhealthy)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
