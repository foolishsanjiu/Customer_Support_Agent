from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dependency_layer_only_depends_on_lock_file() -> None:
    lines = (ROOT / "docker" / "api.Dockerfile").read_text(encoding="utf-8").splitlines()

    requirements_copy = lines.index("COPY requirements.lock ./")
    dependency_install = next(
        index
        for index, line in enumerate(lines)
        if line.startswith("RUN python -m pip install --no-cache-dir")
    )
    metadata_copy = lines.index("COPY pyproject.toml README.md ./")
    application_copy = lines.index("COPY app ./app")

    assert requirements_copy < dependency_install < metadata_copy < application_copy
    assert all(
        "pyproject.toml" not in line and "README.md" not in line and "app" not in line
        for line in lines[:dependency_install]
        if line.startswith("COPY ")
    )
    assert "RUN python -m pip install --no-cache-dir --no-deps --no-build-isolation ." in lines


def test_runtime_uses_offline_model_cache_and_current_agent_budgets() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert 'HF_HUB_OFFLINE: "1"' in compose
    assert 'TRANSFORMERS_OFFLINE: "1"' in compose
    assert ':/models:ro"' in compose
    assert "MAX_AGENT_STEPS: ${MAX_AGENT_STEPS:-18}" in compose
    assert "MAX_AGENT_REPAIR_ATTEMPTS: ${MAX_AGENT_REPAIR_ATTEMPTS:-1}" in compose
    assert "CONTEXT_MAX_ESTIMATED_TOKENS: ${CONTEXT_MAX_ESTIMATED_TOKENS:-6000}" in compose
