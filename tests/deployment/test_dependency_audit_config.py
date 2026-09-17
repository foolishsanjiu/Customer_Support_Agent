from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dependency_audit_covers_production_and_development_locks() -> None:
    workflow = (ROOT / ".github" / "workflows" / "dependency-audit.yml").read_text(encoding="utf-8")

    assert "pypa/gh-action-pip-audit@v1.1.0" in workflow
    assert "requirements.lock" in workflow
    assert "requirements-dev.lock" in workflow
    assert "matrix.lockfile" in workflow
    assert "no-deps: true" in workflow
    assert "schedule:" in workflow
