import sqlite3

from app import config, db, jobs


def isolate(tmp_path, monkeypatch, db_name="toniefi.db"):
    monkeypatch.setattr(config, "LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / db_name)
    config.ensure_dirs()
    db._local.__dict__.clear()


def fresh(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    db.init()


def test_new_databases_carry_the_column(tmp_path, monkeypatch):
    fresh(tmp_path, monkeypatch)
    columns = {row[1] for row in db.connect().execute("PRAGMA table_info(jobs)")}
    assert "progress_percent" in columns


def test_the_migration_adds_it_to_an_older_database(tmp_path, monkeypatch):
    isolate(tmp_path, monkeypatch)
    path = config.DB_PATH
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            label TEXT NOT NULL DEFAULT '',
            payload TEXT NOT NULL DEFAULT '{}',
            progress TEXT NOT NULL DEFAULT '',
            result TEXT NOT NULL DEFAULT '{}',
            error TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        """
    )
    old.execute(
        "INSERT INTO jobs(kind,payload,created_at,updated_at) VALUES('push','{}',1,1)"
    )
    old.commit()
    old.close()

    db._local.__dict__.clear()
    db.init()
    columns = {row[1] for row in db.connect().execute("PRAGMA table_info(jobs)")}
    assert "progress_percent" in columns
    row = db.get_job(1)
    assert row["progress_percent"] is None


def test_update_job_round_trips_a_percentage(tmp_path, monkeypatch):
    fresh(tmp_path, monkeypatch)
    job_id = db.create_job("push", "Send", {})
    db.update_job(job_id, progress="Uploading 3/10: Intro", progress_percent=31.25)
    assert db.get_job(job_id)["progress_percent"] == 31.25
    db.update_job(job_id, progress="Confirming", progress_percent=None)
    assert db.get_job(job_id)["progress_percent"] is None


def test_present_passes_the_percentage_through(tmp_path, monkeypatch):
    fresh(tmp_path, monkeypatch)
    job_id = db.create_job("push", "Send", {})
    db.update_job(job_id, status="running", progress_percent=42.0)
    assert jobs.present(db.get_job(job_id))["progress_percent"] == 42.0


def test_step_percent_is_index_over_total():
    from app import audio

    assert audio.step_percent(0, 4) == 0.0
    assert audio.step_percent(1, 4) == 25.0
    assert audio.step_percent(4, 4) == 100.0
    assert audio.step_percent(1, 0) is None
    assert audio.step_percent(5, 4) == 100.0
