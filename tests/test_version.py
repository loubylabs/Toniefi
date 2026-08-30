import importlib

from app import version


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
