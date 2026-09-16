import json

import pytest

from scripts.run_evaluation import _capture_identity, _model_version


def test_capture_identity_uses_single_provider_fingerprint(tmp_path) -> None:
    metadata = tmp_path / "capture.json"
    metadata.write_text(
        json.dumps(
            {
                "requested_model": "model-alias",
                "response_models": ["model-alias", "model-alias"],
                "system_fingerprints": ["fingerprint-1"],
            }
        ),
        encoding="utf-8",
    )

    response_models, fingerprints = _capture_identity(metadata, "model-alias")
    version = _model_version(None, response_models, fingerprints, "commit-1", "model-alias")

    assert response_models == ["model-alias"]
    assert fingerprints == ["fingerprint-1"]
    assert version == "model-alias@fp:fingerprint-1"


def test_capture_identity_rejects_requested_model_mismatch(tmp_path) -> None:
    metadata = tmp_path / "capture.json"
    metadata.write_text(
        json.dumps(
            {
                "requested_model": "different-model",
                "response_models": [],
                "system_fingerprints": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not match --model-id"):
        _capture_identity(metadata, "expected-model")


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("response_models", "cannot mix provider response models"),
        ("system_fingerprints", "cannot mix provider system fingerprints"),
    ],
)
def test_capture_identity_rejects_mixed_provider_identity(tmp_path, field, message) -> None:
    metadata = tmp_path / "capture.json"
    payload = {
        "requested_model": "model-alias",
        "response_models": ["model-alias"],
        "system_fingerprints": ["fingerprint-1"],
    }
    payload[field] = ["one", "two"]
    metadata.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        _capture_identity(metadata, "model-alias")


def test_unversioned_provider_starts_commit_specific_series() -> None:
    version = _model_version(None, ["model-alias"], [], "commit-1", "requested")

    assert version == "model-alias@unversioned:commit-1"
