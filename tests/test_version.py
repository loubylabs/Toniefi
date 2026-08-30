from app import version


def test_build_label_shortens_a_commit():
    assert version.build_label("bd9d91b4f5bed02dd5111d3e41dacbe27849760d") == "bd9d91b"


def test_build_label_names_a_source_checkout():
    assert version.build_label("") == "development"
    assert version.build_label("   ") == "development"
