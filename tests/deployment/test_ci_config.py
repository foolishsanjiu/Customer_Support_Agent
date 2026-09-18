from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_ci_invokes_pytest_through_the_active_python_environment() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "python -m pytest \\" in workflow
    assert "python -m pytest -m p2 -q" in workflow
    assert "p2-guardrails.xml" in workflow
