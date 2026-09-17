from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_compose_separates_redis_responsibilities_and_persists_data() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert compose.count("CELERY_BROKER_URL: redis://redis:6379/0") == 2
    assert compose.count("LANGGRAPH_REDIS_URL: redis://redis:6379/1") == 2
    assert compose.count("CONTROL_REDIS_URL: redis://redis:6379/2") == 2
    assert "--appendonly" in compose
    assert "--appendfsync" in compose
    assert "noeviction" in compose
    assert "redis-data:/data" in compose
