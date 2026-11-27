"""The file deletion queue job without a database (notice #59): configuration refusals, a deleter
that can only delete, and what a run does with successes, failures and old rows."""
import pytest

from app.jobs import file_deletions as J

DB_URL = "postgresql+psycopg://dolab_file_cleaner@db.example/dolab"
KEY = "6f1c0d2e-0000-4000-8000-000000000001/6f1c0d2e-0000-4000-8000-0000000000aa_original.jpg"


def env(**values):
    base = {"DOLAB_ENV": "test", "DOLAB_FILE_CLEANER_DATABASE_URL": DB_URL}
    base.update(values)
    return {k: v for k, v in base.items() if v is not None}


@pytest.mark.parametrize("values, message", [
    ({"DOLAB_ENV": "prod"}, "DOLAB_ENV"),
    ({"DOLAB_LEDGER_SIGNING_KEY": "x"}, "SIGNING_KEY"),
    ({"DOLAB_JWT_SECRET": "x"}, "JWT_SECRET"),
    ({"DOLAB_FILE_CLEANER_DATABASE_URL": None}, "DATABASE_URL"),
    ({"DOLAB_FILE_CLEANER_DATABASE_URL": "sqlite:///x.db"}, "DATABASE_URL"),
    ({"DOLAB_ENV": "production"}, "must be 's3'"),
    ({"DOLAB_STORAGE": "s3"}, "S3_BUCKET"),
    ({"DOLAB_STORAGE": "s3", "DOLAB_S3_BUCKET": "same", "DOLAB_LEDGER_BUCKET": "same"}, "ledger bucket"),
    ({"DOLAB_ENV": "staging", "DOLAB_STORAGE": "s3", "DOLAB_S3_BUCKET": "p",
      "DOLAB_S3_ENDPOINT_URL": "http://minio:9000"}, "https"),
])
def test_configurations_it_refuses(values, message):
    with pytest.raises(J.Refused, match=message):
        J.load_config(env(**values))


def test_configurations_it_accepts():
    assert J.load_config(env()).storage == "local"
    prod = J.load_config(env(DOLAB_ENV="production", DOLAB_STORAGE="s3", DOLAB_S3_BUCKET="photos",
                             DOLAB_LEDGER_BUCKET="ledger"))
    assert (prod.storage, prod.bucket) == ("s3", "photos")


# ------------------------------------------------------------------ deleters

class RecordingS3:
    def __init__(self, error_code=None):
        self.calls, self.error_code = [], error_code

    def delete_object(self, **kwargs):
        self.calls.append(("delete_object", kwargs["Bucket"], kwargs["Key"]))
        if self.error_code:
            error = RuntimeError(self.error_code)  # shaped like botocore's ClientError
            error.response = {"Error": {"Code": self.error_code}}
            raise error
        return {}

    def __getattr__(self, name):
        raise AssertionError(f"the deleter called {name}")


def test_the_s3_deleter_only_deletes():
    client = RecordingS3()
    J.S3FileDeleter("photos", client).delete(KEY)
    assert client.calls == [("delete_object", "photos", KEY)]
    assert [m for m in dir(J.S3FileDeleter) if not m.startswith("_")] == ["delete"]
    assert [m for m in dir(J.LocalFileDeleter) if not m.startswith("_")] == ["delete"]


def test_a_missing_file_is_deleted_and_other_errors_are_failures():
    J.S3FileDeleter("photos", RecordingS3("NoSuchKey")).delete(KEY)
    with pytest.raises(Exception):
        J.S3FileDeleter("photos", RecordingS3("AccessDenied")).delete(KEY)


def test_the_local_deleter_stays_in_the_media_folder(tmp_path):
    media = tmp_path / "media"
    (media / "u").mkdir(parents=True)
    (media / "u" / "a.jpg").write_bytes(b"x")
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"keep")
    deleter = J.LocalFileDeleter(str(media))
    deleter.delete("u/a.jpg")
    deleter.delete("u/a.jpg")  # again: already gone, not an error
    assert not (media / "u" / "a.jpg").exists()
    for bad in ("../secret.txt", "/etc/passwd", "u/../../secret.txt", ""):
        with pytest.raises(ValueError):
            deleter.delete(bad)
    assert outside.read_bytes() == b"keep"


# ------------------------------------------------------------------ a run

class FakeQueue:
    def __init__(self, rows, stale=0):
        self.rows = dict(rows)  # key -> attempts
        self.claimed, self.calls, self.stale_rows = set(), [], stale

    def claim(self, limit):
        due = [(k, a) for k, a in sorted(self.rows.items()) if k not in self.claimed][:limit]
        self.claimed.update(k for k, _ in due)
        return due

    def done(self, key):
        self.calls.append(("done", key))
        del self.rows[key]

    def failed(self, key, attempts, error, wait=None):
        self.calls.append(("failed", key, attempts, error) + ((wait,) if wait is not None else ()))
        self.rows[key] = attempts + 1

    def commit(self):
        self.calls.append(("commit",))

    def rollback(self):
        self.calls.append(("rollback",))

    def due(self):
        return len(self.rows)

    def stale(self):
        return self.stale_rows


class FakeDeleter:
    def __init__(self, fail=()):
        self.fail, self.deleted = set(fail), []

    def delete(self, key):
        if key in self.fail:
            raise ConnectionError("storage down")
        J.check_key(key)
        self.deleted.append(key)


def keys(n):
    """Photo keys as the server makes them."""
    user = "6f1c0d2e-0000-4000-8000-000000000001"
    return [f"{user}/6f1c0d2e-0000-4000-8000-{i:012d}_{'original.jpg' if i % 2 else 'thumb.jpg'}"
            for i in range(n)]


def test_deleted_files_lose_their_rows():
    queue, deleter = FakeQueue({k: 0 for k in keys(5)}), FakeDeleter()
    report = J.run(queue, deleter, batch=2)
    assert (report.claimed, report.deleted, report.failed) == (5, 5, 0) and queue.rows == {}
    assert J.exit_code(report) == J.EXIT_OK


def test_a_failure_is_rescheduled_and_the_others_go_on():
    ks = keys(3)
    queue = FakeQueue({k: 0 for k in ks} | {ks[1]: 2})
    report = J.run(queue, FakeDeleter(fail={ks[1]}))
    assert report.deleted == 2 and report.failed == 1 and queue.rows == {ks[1]: 3}
    assert ("failed", ks[1], 2, "ConnectionError") in queue.calls
    assert J.exit_code(report) == J.EXIT_PROBLEMS


def test_a_batch_that_fails_entirely_stops_the_run():
    ks = keys(6)
    queue = FakeQueue({k: 0 for k in ks})
    report = J.run(queue, FakeDeleter(fail=set(ks)), batch=2)
    assert report.claimed == 2 and report.stopped_early and len(queue.rows) == 6


INVALID = ["../etc/passwd", "/etc/passwd", "2026/01/02/6f1c0d2e-0000-4000-8000-000000000001.json",
           "u/x.jpg", "6f1c0d2e-0000-4000-8000-000000000001/../../x_original.jpg", "", "a" * 600,
           "6f1c0d2e-0000-4000-8000-000000000001/6f1c0d2e-0000-4000-8000-0000000000aa_original.exe"]


class RecordingDeleter(FakeDeleter):
    def __init__(self, fail=()):
        super().__init__(fail)
        self.asked = []

    def delete(self, key):
        self.asked.append(key)
        super().delete(key)


@pytest.mark.parametrize("bad", INVALID)
def test_an_invalid_key_is_never_sent_to_storage(bad):
    queue, deleter = FakeQueue({bad: 0}), RecordingDeleter()
    report = J.run(queue, deleter)
    assert deleter.asked == [] and report.invalid == 1 and report.failed == 0
    assert ("failed", bad, 0, "invalid_key", J.MAX_WAIT) in queue.calls
    assert queue.rows == {bad: 1}  # kept, not dropped
    assert not report.stopped_early and J.exit_code(report) == J.EXIT_PROBLEMS


def test_a_mixed_batch_deletes_the_good_files():
    good = keys(4)
    rows = {k: 0 for k in good} | {k: 0 for k in INVALID[:4]}
    queue, deleter = FakeQueue(rows), RecordingDeleter()
    report = J.run(queue, deleter, batch=8)
    assert sorted(deleter.asked) == sorted(good) and report.deleted == 4
    assert report.invalid == 4 and set(queue.rows) == set(INVALID[:4])
    assert not report.stopped_early


def test_a_batch_of_invalid_keys_does_not_stop_the_run():
    rows = {k: 0 for k in INVALID[:3]} | {k: 0 for k in keys(3)}
    queue, deleter = FakeQueue(rows), RecordingDeleter()
    # Sorted, the invalid keys fill the first batch; the run must go on to the good ones.
    report = J.run(queue, deleter, batch=3)
    assert report.deleted == 3 and report.invalid == 3 and not report.stopped_early


def test_storage_down_still_stops_the_run_with_invalid_keys_around():
    good = keys(4)
    rows = {k: 0 for k in good} | {INVALID[0]: 0}
    queue = FakeQueue(rows)
    report = J.run(queue, RecordingDeleter(fail=set(good)), batch=10)
    assert report.failed == 4 and report.invalid == 1 and report.stopped_early


def test_rows_older_than_24_hours_alert():
    report = J.run(FakeQueue({}, stale=1), FakeDeleter())
    assert report.stale == 1 and J.exit_code(report) == J.EXIT_PROBLEMS


def test_a_dry_run_changes_nothing():
    queue, deleter = FakeQueue({k: 0 for k in keys(3)}, stale=1), FakeDeleter()
    report = J.run(queue, deleter, dry_run=True)
    assert (report.due, report.stale) == (3, 1) and deleter.deleted == [] and len(queue.rows) == 3
    assert not any(c[0] in ("done", "failed", "commit") for c in queue.calls)


def test_the_run_is_bounded():
    queue = FakeQueue({k: 0 for k in keys(30)})
    report = J.run(queue, FakeDeleter(), batch=4, max_batches=3)
    assert report.claimed == 12 and len(queue.rows) == 18


def test_waits_grow_and_are_capped():
    assert [J.wait_after(a) for a in range(7)] == [600, 1200, 2400, 4800, 9600, 19200, 21600]
    assert J.wait_after(1000) == J.MAX_WAIT


# ------------------------------------------------------------------ the database cannot be reached

class Unreachable(Exception):
    """Stands for psycopg.OperationalError and its kind."""


@pytest.mark.parametrize("error", [
    Unreachable('connection failed: FATAL: password authentication failed for user "dolab_file_cleaner"'),
    Unreachable('FATAL: database "nope" does not exist'),
    Unreachable("connection refused"),
    Unreachable('FATAL: too many connections for role "dolab_file_cleaner"'),
    TimeoutError("timed out"),
    OSError("network unreachable"),
])
def test_a_connection_failure_is_reported_not_raised(monkeypatch, caplog, error):
    monkeypatch.setenv("DOLAB_ENV", "test")
    monkeypatch.setenv("DOLAB_FILE_CLEANER_DATABASE_URL", "postgresql://dolab_file_cleaner:s3cret-pw@db.example/dolab")
    monkeypatch.setenv("DOLAB_STORAGE", "local")  # the configuration is valid: only the connection fails
    for name in ("DOLAB_JWT_SECRET", "DOLAB_LEDGER_SIGNING_KEY", "DOLAB_S3_BUCKET", "DOLAB_LEDGER_BUCKET"):
        monkeypatch.delenv(name, raising=False)
    built = []

    def failing_queue(url):
        raise error

    monkeypatch.setattr(J, "PgQueue", failing_queue)
    monkeypatch.setattr(J, "build_deleter", lambda config: built.append(config))
    caplog.set_level("INFO")
    assert J.main([]) == J.EXIT_NO_DATABASE
    assert built == []  # nothing else was started
    assert f"could not connect to the database ({type(error).__name__})" in caplog.text
    assert "s3cret-pw" not in caplog.text and "Traceback" not in caplog.text
    assert "db.example" not in caplog.text and str(error) not in caplog.text


def test_logs_carry_no_keys(caplog):
    caplog.set_level("INFO")
    ks = keys(2)
    J.run(FakeQueue({k: 0 for k in ks}), FakeDeleter(fail={ks[0]}))
    assert "deleted 1, failed 1" in caplog.text
    assert not any(k in caplog.text or k.split("/")[0] in caplog.text for k in ks)
