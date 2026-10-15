import sys

import pytest

from app import audio

BAD_TAG = "import sys; sys.stderr.buffer.write(b'Friends\\xc2(\\n'); sys.exit({code})"


def test_run_survives_undecodable_stderr_on_success():
    proc = audio._run([sys.executable, "-c", BAD_TAG.format(code=0)])
    assert "Friends�(" in proc.stderr


def test_run_failure_reports_replaced_text_not_a_decode_error():
    with pytest.raises(audio.AudioError) as err:
        audio._run([sys.executable, "-c", BAD_TAG.format(code=1)])
    assert "Friends�(" in str(err.value)
