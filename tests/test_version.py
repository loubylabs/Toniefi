import importlib
from pathlib import Path

from app import main, version


def test_build_label_shortens_a_commit():
    assert version.build_label("bd9d91b4f5bed02dd5111d3e41dacbe27849760d") == "bd9d91b"


def test_build_label_names_a_source_checkout():
    assert version.build_label("") == "development"
    assert version.build_label("   ") == "development"


def test_build_uses_development_when_the_commit_environment_is_empty(monkeypatch):
    try:
        with monkeypatch.context() as environment:
            environment.setenv("TONIEFI_BUILD_COMMIT", "")
            importlib.reload(version)
            assert version.BUILD == "development"
    finally:
        importlib.reload(version)


def test_fastapi_metadata_uses_the_canonical_application_version():
    assert main.app.version == version.APP_VERSION


def test_publish_workflow_only_creates_sha_image_tags():
    workflow = (Path(__file__).parents[1] / ".github/workflows/publish.yml").read_text()

    assert "type=sha,prefix=sha-" in workflow
    assert "type=semver" not in workflow
    assert 'tags: ["v*"]' not in workflow
