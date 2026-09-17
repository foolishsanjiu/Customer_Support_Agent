from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_compose_separates_redis_responsibilities_and_persists_data() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert compose.count("CELERY_BROKER_URL: redis://redis:6379/0") == 2
    assert compose.count("LANGGRAPH_REDIS_URL: redis://checkpoint-redis:6379/0") == 2
    assert compose.count("CONTROL_REDIS_URL: redis://redis:6379/2") == 2
    assert "redis://redis:6379/1" not in compose
    assert compose.count("--appendonly") == 2
    assert compose.count("--appendfsync") == 2
    assert compose.count("noeviction") == 2
    assert "redis-data:/data" in compose
    assert "checkpoint-redis-data:/data" in compose
    assert '"127.0.0.1:6380:6379"' in compose

    assert "checkpoint-redis:" in ci
    assert "LANGGRAPH_REDIS_URL: redis://127.0.0.1:6380/0" in ci
