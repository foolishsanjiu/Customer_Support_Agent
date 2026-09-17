from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dependency_audit_covers_production_and_development_locks() -> None:
    workflow = (ROOT / ".github" / "workflows" / "dependency-audit.yml").read_text(encoding="utf-8")

    assert "pypa/gh-action-pip-audit@v1.1.0" in workflow
    assert "requirements.lock" in workflow
    assert "requirements-dev.lock" in workflow
    assert "matrix.lockfile" in workflow
    assert "no-deps: true" in workflow
    assert "disable-pip: true" in workflow
    assert "schedule:" in workflow

    accepted_chromadb_advisories = {
        "PYSEC-2026-311",
        "PYSEC-2026-3813",
        "PYSEC-2026-3814",
        "PYSEC-2026-3815",
    }
    assert accepted_chromadb_advisories <= set(workflow.split())
    assert "internal-be-careful-allow-failure" not in workflow
