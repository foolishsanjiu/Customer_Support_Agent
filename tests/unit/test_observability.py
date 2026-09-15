import json

from fastapi.testclient import TestClient

from app.main import create_app
from app.observability.logging import redact_sensitive


def test_redaction_is_recursive_and_preserves_safe_fields() -> None:
    event = {
        "event": "tool_called",
        "Authorization": "Bearer secret",
        "arguments": {
            "password": "unsafe",
            "order_id": 7,
            "items": [{"api-key": "unsafe"}],
        },
    }

    redacted = redact_sensitive(None, "info", event)

    assert redacted == {
        "event": "tool_called",
        "Authorization": "[REDACTED]",
        "arguments": {
            "password": "[REDACTED]",
            "order_id": 7,
            "items": [{"api-key": "[REDACTED]"}],
        },
    }


def test_request_id_is_propagated_and_untrusted_value_is_replaced() -> None:
    with TestClient(create_app()) as client:
        accepted = client.get("/health/live", headers={"X-Request-ID": "request-123"})
        replaced = client.get("/health/live", headers={"X-Request-ID": "unsafe value"})

    assert accepted.headers["X-Request-ID"] == "request-123"
    assert replaced.headers["X-Request-ID"] != "unsafe value"
    assert len(replaced.headers["X-Request-ID"]) == 32


def test_configured_log_is_json_and_redacted(capsys) -> None:
    from app.observability.logging import configure_logging, get_logger

    configure_logging("test-service")
    get_logger().info("credential_test", api_key="unsafe", run_id=4)

    payload = json.loads(capsys.readouterr().out)
    assert payload["event"] == "credential_test"
    assert payload["api_key"] == "[REDACTED]"
    assert payload["run_id"] == 4
    assert payload["service"] == "test-service"
