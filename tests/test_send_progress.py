from app import jobs, push


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, message, percent=None):
        self.calls.append((message, percent))


def test_throttle_reports_the_first_and_the_final_value():
    recorder = Recorder()
    throttle = push.ProgressThrottle(recorder, min_interval=1000.0, min_delta=1.0)
    throttle.report("Uploading", 0.0)
    throttle.report("Uploading", 0.4)
    throttle.report("Uploading", 0.9)
    throttle.flush("Uploading", 100.0)
    assert recorder.calls == [("Uploading", 0.0), ("Uploading", 100.0)]


def test_throttle_lets_a_full_percent_through():
    recorder = Recorder()
    throttle = push.ProgressThrottle(recorder, min_interval=1000.0, min_delta=1.0)
    throttle.report("Uploading", 0.0)
    throttle.report("Uploading", 1.5)
    assert recorder.calls == [("Uploading", 0.0), ("Uploading", 1.5)]


def test_percentage_is_weighted_by_bytes_not_by_chapter_count():
    tracks = [
        {"name": "a.mp3", "size": 1000, "seconds": 10},
        {"name": "b.mp3", "size": 9000, "seconds": 600},
    ]
    assert push.upload_percent(tracks, done_index=0, position=0) == 0.0
    assert push.upload_percent(tracks, done_index=0, position=1000) == 10.0
    assert push.upload_percent(tracks, done_index=1, position=0) == 10.0
    assert push.upload_percent(tracks, done_index=1, position=9000) == 100.0


def test_percentage_is_unknown_when_a_size_is_missing():
    tracks = [{"name": "a.mp3"}, {"name": "b.mp3"}]
    assert push.upload_percent(tracks, done_index=0, position=0) is None


def test_present_names_a_running_push_sending():
    sending = jobs.present({
        "kind": "push", "status": "running", "progress": "Uploading 3/10: Intro",
        "payload": {}, "result": {},
    })
    assert sending["phase"] == "sending"
    finished = jobs.present({
        "kind": "push", "status": "done", "progress": "Finished",
        "payload": {}, "result": {},
    })
    assert finished["phase"] == "sent"


def test_counting_reader_reports_absolute_position(tmp_path):
    from app.tonies import _CountingReader

    path = tmp_path / "a.bin"
    path.write_bytes(b"0123456789")
    seen = []
    with path.open("rb") as fh:
        reader = _CountingReader(fh, seen.append)
        reader.read(4)
        reader.read(4)
        reader.seek(0)
        reader.read(10)
    # Positions, not deltas, so a rewind before the POST cannot double count.
    assert seen == [4, 8, 10]
