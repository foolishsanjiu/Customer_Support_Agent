from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_compose_runs_independent_fulfillment_mcp() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "mcp-fulfillment:" in compose
    assert 'command: ["python", "-m", "app.mcp.fulfillment_server"]' in compose
    assert "FULFILLMENT_MCP_URL: http://mcp-fulfillment:8002/mcp" in compose
    assert '"8002:8002"' in compose
