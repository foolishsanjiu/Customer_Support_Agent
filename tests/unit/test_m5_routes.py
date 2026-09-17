from app.main import create_app


def test_public_api_has_agent_and_approval_routes_without_direct_refund_write() -> None:
    paths = create_app().openapi()["paths"]
    assert "post" in paths["/api/v1/agent-runs"]
    assert "get" in paths["/api/v1/agent-runs/{run_id}"]
    assert "get" in paths["/api/v1/agent-runs/{run_id}/events"]
    assert "post" in paths["/api/v1/approvals/{approval_id}/approve"]
    assert "post" in paths["/api/v1/approvals/{approval_id}/reject"]
    assert "post" not in paths.get("/api/v1/orders/{order_id}/refund", {})
