import os

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1",
    reason="set RUN_INTEGRATION_TESTS=1 with MySQL and Redis running",
)
def test_readiness_with_real_infrastructure() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"mysql": "ok", "redis": "ok"},
    }
