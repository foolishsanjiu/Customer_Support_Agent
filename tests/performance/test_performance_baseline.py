import json
from pathlib import Path

BASELINE_PATH = Path("performance/baselines/p1-local-docker-9c2a41c.json")


def test_p1_local_performance_baseline_is_complete() -> None:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    assert baseline["schema_version"] == "performance-baseline-v1"
    assert baseline["environment"]["git_commit"] == ("9c2a41c4d9cb8771cfab7c5ad351d50cba6cfbc8")
    assert baseline["source_report"]["sha256"] == (
        "047e8bcdc106d5b3de11bfc5f7d981a5d3dbd967f1c5bce782fe39b5638e7ad6"
    )
    assert [profile["name"] for profile in baseline["profiles"]] == [
        "readiness-c10",
        "business-reads-c1",
        "business-reads-c10",
        "business-reads-c25",
    ]
    assert all(
        profile["aggregate"]["minimum_success_rate"] == 1.0 for profile in baseline["profiles"]
    )
    assert baseline["gate"]["passed"] is True
    assert baseline["interpretation"]["cache_justified"] is False
