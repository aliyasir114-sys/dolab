"""The file deletion queue on PostgreSQL (notice #59): dolab_file_cleaner's privileges, tried for
real, and the queue job end to end (F2 completed, F3, F5, F6 completed, the reconciliation path)."""
import uuid
from datetime import timedelta

import pytest

from tests.test_pg_roles import ADMIN_URL, E, PRIVILEGES, _conninfo, connect, denied, rows, scratch_db, value  # noqa: F401

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="needs PostgreSQL (DOLAB_TEST_PG_ADMIN_URL)")

from app import deletion, ledger_store  # noqa: E402
from app.config import settings  # noqa: E402
from app.jobs import file_deletions as J  # noqa: E402
from app.jobs import reconcile as R  # noqa: E402
from app.storage import StorageUnavailable, storage  # noqa: E402
from tests.test_file_deletion_queue import photo_keys, queued  # noqa: E402
from tests.test_pg_deleter_functions import db_client, wardrobe  # noqa: E402,F401
from tests.test_photos import new_item, stored_files, upload  # noqa: E402
from tests.test_reconcile_job import KEYS  # noqa: E402

QUEUE = "pending_file_deletions"


def cleaner_queue() -> J.PgQueue:
    return J.PgQueue(_conninfo(E.FILE_CLEANER))


def run_job(deleter=None, **kwargs) -> J.Report:
    queue = cleaner_queue()
    try:
        return J.run(queue, deleter or J.LocalFileDeleter(settings.media_dir), **kwargs)
    finally:
        queue.close()


def queue_rows():
    with connect() as admin:
        return rows(admin, "SELECT key, attempts, last_error FROM dolab.pending_file_deletions ORDER BY key")


def photo_key(kind: str = "original.jpg") -> str:
    """A key shaped like the server's photo keys (the job touches no other)."""
    return f"{uuid.uuid4()}/{uuid.uuid4()}_{kind}"


def make_due():
    """Time passes: every queued file is due now (rows start five minutes ahead, for the server)."""
    with connect() as admin:
        admin.execute("UPDATE dolab.pending_file_deletions SET next_try_at = now()")


def admin_enqueue(key, created=timedelta(0), due=timedelta(0)):
    with connect() as admin:
        admin.execute("INSERT INTO dolab.pending_file_deletions (key, attempts, next_try_at, created_at) "
                      "VALUES (%s, 0, now() + %s, now() - %s)", (key, due, created))


class StorageSwitch:
    def __init__(self, monkeypatch):
        self.monkeypatch, self.real = monkeypatch, storage.delete

    def undo(self):
        """Storage is back."""
        self.monkeypatch.setattr(storage, "delete", self.real)


@pytest.fixture
def storage_down(monkeypatch):
    switch = StorageSwitch(monkeypatch)

    def down(_key):
        raise StorageUnavailable("down")

    monkeypatch.setattr(storage, "delete", down)
    monkeypatch.setattr("app.services._sleep", lambda _s: None)
    return switch


# ------------------------------------------------------------------ what dolab_file_cleaner may do

def test_the_file_cleaner_has_the_queue_and_nothing_else():
    with connect() as admin:
        tables = [t for (t,) in rows(admin, "SELECT tablename FROM pg_tables WHERE schemaname = 'dolab'")]
        assert QUEUE in tables
        for table in tables:
            have = {p for p in PRIVILEGES
                    if value(admin, "SELECT has_table_privilege(%s, %s, %s)", E.FILE_CLEANER, f"dolab.{table}", p)}
            assert have == ({"SELECT", "DELETE"} if table == QUEUE else set()), table
            if table != QUEUE:
                for p in ("SELECT", "INSERT", "UPDATE"):
                    assert value(admin, "SELECT has_any_column_privilege(%s, %s, %s)",
                                 E.FILE_CLEANER, f"dolab.{table}", p) is False, table
        # UPDATE on three columns of the queue only (review of #60, M3).
        updatable = {c for (c,) in rows(admin, "SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass "
                                               "AND attnum > 0 AND NOT attisdropped", f"dolab.{QUEUE}")
                     if value(admin, "SELECT has_column_privilege(%s, %s, %s, 'UPDATE')", E.FILE_CLEANER,
                              f"dolab.{QUEUE}", c)}
        assert updatable == {"attempts", "next_try_at", "last_error"}
        functions = [f for (f,) in rows(admin, "SELECT p.oid::regprocedure::text FROM pg_proc p "
                                               "JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'dolab'")]
        assert functions
        for function in functions:
            assert value(admin, "SELECT has_function_privilege(%s, %s, 'EXECUTE')", E.FILE_CLEANER, function) is False


def test_the_file_cleaner_is_refused_everything_else_for_real():
    with connect(E.FILE_CLEANER) as conn:
        for statement in ("SELECT 1 FROM dolab.users", "SELECT 1 FROM dolab.item_photos", "DELETE FROM dolab.items",
                          "UPDATE dolab.deletion_intents SET entity_type = entity_type",
                          "INSERT INTO dolab.pending_file_deletions (key, attempts, next_try_at, created_at) "
                          "VALUES ('x', 0, now(), now())",
                          "TRUNCATE dolab.pending_file_deletions",
                          "UPDATE dolab.pending_file_deletions SET key = 'other-user/x.jpg'",
                          "UPDATE dolab.pending_file_deletions SET created_at = now()",
                          "SELECT 1 FROM dolab.audit_log", "SELECT 1 FROM dolab.alembic_version",
                          "SELECT dolab.apply_deletion('item', gen_random_uuid(), gen_random_uuid())",
                          "SELECT dolab.reconcile_purge_intents(true)",
                          f"SET ROLE {E.APP}", f"SET ROLE {E.RECONCILER}", f"SET ROLE {E.DELETER}"):
            assert denied(conn, statement), statement


def test_the_file_cleaner_can_work_the_queue(db_client):
    admin_enqueue("u/one.jpg")
    with connect(E.FILE_CLEANER) as conn:
        conn.execute("SELECT key, attempts, created_at FROM dolab.pending_file_deletions FOR UPDATE SKIP LOCKED")
        conn.execute("UPDATE dolab.pending_file_deletions SET attempts = attempts + 1, next_try_at = now(), "
                     "last_error = 'ConnectionError' WHERE key = 'u/one.jpg'")
        conn.execute("DELETE FROM dolab.pending_file_deletions WHERE key = 'u/one.jpg'")
    assert queue_rows() == []


def test_others_get_only_their_part_of_the_queue():
    with connect(E.APP) as conn:
        assert denied(conn, "INSERT INTO dolab.pending_file_deletions (key, attempts, next_try_at, created_at) "
                            "VALUES ('x', 0, now(), now())")
        assert denied(conn, "UPDATE dolab.pending_file_deletions SET attempts = 0")
        assert denied(conn, "SELECT attempts FROM dolab.pending_file_deletions")
        conn.execute("SELECT key FROM dolab.pending_file_deletions")  # allowed: to remove its rows
    with connect(E.RECONCILER) as conn:
        assert denied(conn, "SELECT key FROM dolab.pending_file_deletions")
        assert denied(conn, "DELETE FROM dolab.pending_file_deletions")


def test_the_job_will_only_connect_as_the_file_cleaner():
    for role in (E.APP, E.RECONCILER, E.DEPLOY):
        with pytest.raises(J.Refused, match="dolab_file_cleaner"):
            J.PgQueue(_conninfo(role))


# ------------------------------------------------------------------ the job end to end

def test_f2_completed_the_job_deletes_what_the_server_could_not(db_client, signup, storage_down):
    headers = signup()
    client = db_client
    item_id = new_item(client, headers)
    photo = upload(client, headers, item_id).json()
    keys = photo_keys(photo["id"])
    assert client.delete(f"/items/{item_id}", headers=headers).status_code == 204
    assert queued() == keys and len(stored_files(client, headers)) == 2
    storage_down.undo()  # storage is back
    assert run_job().claimed == 0  # first due five minutes after the deletion (M1)
    make_due()

    report = run_job()
    assert (report.claimed, report.deleted, report.failed) == (2, 2, 0) and J.exit_code(report) == J.EXIT_OK
    assert stored_files(client, headers) == [] and queued() == []


def test_f5_running_twice_and_a_file_already_gone(db_client):
    admin_enqueue(photo_key())  # no such file: a 404 is a success
    first = run_job()
    second = run_job()
    assert (first.deleted, second.claimed) == (1, 0) and queue_rows() == []
    assert J.exit_code(first) == J.exit_code(second) == J.EXIT_OK


def test_two_runs_never_take_the_same_row(db_client):
    for n in range(4):
        admin_enqueue(f"u/{n}.jpg")
    one, two = cleaner_queue(), cleaner_queue()
    try:
        taken = one.claim(10)
        assert len(taken) == 4 and two.claim(10) == []  # leased by the first run
        # The first run stops without finishing: its rows come back when the lease ends.
        one.close()
        assert two.claim(10) == []
        make_due()  # the lease has ended
        assert sorted(k for k, _ in two.claim(10)) == [f"u/{n}.jpg" for n in range(4)]
    finally:
        two.close()


def test_two_runs_at_the_same_moment_split_the_rows(db_client):
    """SKIP LOCKED inside the lease statement: two runs leasing at once never get the same row."""
    for n in range(40):
        admin_enqueue(f"u/{n:02d}.jpg")
    import threading

    results: dict[int, list] = {}
    start = threading.Barrier(2)

    def lease(n):
        queue = cleaner_queue()
        try:
            start.wait()
            results[n] = [k for k, _ in queue.claim(25)]
        finally:
            queue.close()

    threads = [threading.Thread(target=lease, args=(n,)) for n in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not set(results[0]) & set(results[1])
    assert len(results[0]) + len(results[1]) == 40


def test_the_job_holds_no_row_lock_while_it_deletes(db_client):
    admin_enqueue(photo_key())
    seen: list[bool] = []

    class Checking:
        def delete(self, key):
            # While the job is deleting this file, someone else can lock its row at once.
            with connect() as admin:
                admin.execute("SET lock_timeout = '100ms'")
                seen.append(bool(rows(admin, "SELECT key FROM dolab.pending_file_deletions "
                                             "WHERE key = %s FOR UPDATE", key)))
            with connect() as admin:
                assert value(admin, "SELECT count(*) FROM pg_stat_activity WHERE usename = %s "
                                    "AND state LIKE 'idle in transaction%%'", E.FILE_CLEANER) == 0

    report = run_job(Checking())
    assert seen == [True] and report.deleted == 1 and queue_rows() == []


def test_m1_the_server_never_waits_long_on_a_locked_queue_row(db_client, signup, monkeypatch, caplog):
    """If something holds the queue rows, the server's removal gives up after its lock timeout:
    the request still succeeds quickly, the files are deleted and the rows wait for the job."""
    import threading
    import time

    import psycopg

    from tests.test_pg_roles import _conninfo as conninfo

    headers = signup()
    item_id = new_item(db_client, headers)
    photo = upload(db_client, headers, item_id).json()
    keys = photo_keys(photo["id"])
    holder = psycopg.connect(conninfo(None))  # not autocommit: keeps its locks until rollback
    real = storage.delete
    locked = threading.Event()

    def lock_rows_then_delete(key):
        if not locked.is_set():
            holder.execute("SELECT key FROM dolab.pending_file_deletions WHERE key = ANY(%s) FOR UPDATE", (keys,))
            locked.set()
        real(key)

    monkeypatch.setattr(storage, "delete", lock_rows_then_delete)
    try:
        started = time.monotonic()
        assert db_client.delete(f"/items/{item_id}", headers=headers).status_code == 204
        took = time.monotonic() - started
    finally:
        holder.rollback()
        holder.close()
    assert took < 10, took  # 2 s lock timeout, not an open-ended wait
    assert stored_files(db_client, headers) == [] and queued() == keys
    assert "queue rows of 2 deleted photo files not removed (OperationalError)" in caplog.text
    make_due()
    assert run_job().deleted == 2 and queued() == []  # the job finds the files gone


def test_a_failure_is_rescheduled_with_a_wait(db_client):
    stuck = photo_key()
    admin_enqueue(stuck)

    class Down:
        def delete(self, key):
            raise ConnectionError("storage down")

    report = run_job(Down())
    assert report.failed == 1 and J.exit_code(report) == J.EXIT_PROBLEMS
    assert queue_rows() == [(stuck, 1, "ConnectionError")]
    with connect() as admin:
        wait = value(admin, "SELECT extract(epoch FROM next_try_at - now()) FROM dolab.pending_file_deletions")
    assert 590 <= float(wait) <= 610
    assert run_job().claimed == 0  # not due again yet


def test_f3_a_row_queued_for_25_hours_alerts(db_client, tmp_path, monkeypatch, caplog):
    admin_enqueue("u/old.jpg", created=timedelta(hours=25), due=timedelta(hours=1))
    for name, value_ in {"DOLAB_ENV": "test", "DOLAB_FILE_CLEANER_DATABASE_URL": _conninfo(E.FILE_CLEANER),
                         "DOLAB_STORAGE": "local", "DOLAB_MEDIA_DIR": str(tmp_path)}.items():
        monkeypatch.setenv(name, value_)
    for name in ("DOLAB_JWT_SECRET", "DOLAB_LEDGER_SIGNING_KEY", "DOLAB_S3_BUCKET", "DOLAB_LEDGER_BUCKET"):
        monkeypatch.delenv(name, raising=False)
    assert J.main(["--dry-run"]) == J.EXIT_PROBLEMS
    assert J.main([]) == J.EXIT_PROBLEMS
    assert "ALERT 1 photo files queued for more than 24 hours" in caplog.text
    assert "u/old.jpg" not in caplog.text
    assert len(queue_rows()) == 1  # not due: left as it is


def test_f6_completed_an_account_of_50_photos(db_client, signup, storage_down):
    headers = signup()
    for start in range(0, 50, 6):
        item_id = new_item(db_client, headers)
        for _ in range(min(6, 50 - start)):
            upload(db_client, headers, item_id)
    user_id = db_client.get("/me", headers=headers).json()["id"]
    assert db_client.delete("/me", headers=headers).status_code == 204
    assert len(queued()) == 100 and len(storage.list_keys(f"{user_id}/")) == 100
    storage_down.undo()
    make_due()
    report = run_job(batch=30)
    assert (report.claimed, report.deleted) == (100, 100) and queued() == []
    assert storage.list_keys(f"{user_id}/") == []


def test_a_deletion_completed_by_reconciliation_has_its_files_queued(db_client, signup, monkeypatch, tmp_path):
    headers, user_id, item_id, photo_id = wardrobe(db_client, signup)
    keys = [k for p in db_client.get(f"/items/{item_id}/photos", headers=headers).json()
            for k in photo_keys(p["id"])]
    monkeypatch.setattr(ledger_store, "writer", ledger_store.LocalLedger(str(tmp_path)))
    real = deletion.apply_deletion

    def apply_then_fail(db, *args):
        real(db, *args)
        raise RuntimeError("connection lost before commit")

    monkeypatch.setattr(deletion, "apply_deletion", apply_then_fail)
    with pytest.raises(RuntimeError):
        db_client.delete(f"/items/{item_id}", headers=headers)
    monkeypatch.setattr(deletion, "apply_deletion", real)
    assert queued() == []

    db = R.PgDatabase(_conninfo(E.RECONCILER))
    try:
        assert db.lock()
        report = R.run(R.LocalLedgerReader(str(tmp_path)), db, KEYS)
    finally:
        db.close()
    assert report.outcomes["deleted"] == 1
    assert queued() == sorted(keys)  # queued by apply_deletion, inside reconcile_apply's transaction
    assert all(k in storage.list_keys(f"{user_id}/") for k in keys)
    make_due()
    assert run_job().deleted == len(keys)
    assert not any(k in storage.list_keys(f"{user_id}/") for k in keys) and queued() == []


# ------------------------------------------------------------------ invalid keys (review of #60)

def test_invalid_keys_are_kept_and_do_not_stop_good_files(db_client, signup, tmp_path):
    media = tmp_path / "media"
    good = [photo_key(), photo_key("thumb.jpg")]
    for key in good:
        (media / key).parent.mkdir(parents=True, exist_ok=True)
        (media / key).write_bytes(b"jpeg")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"keep")
    bad = ["../outside.txt", "2026/01/02/entry.json", "u/x.jpg", f"{uuid.uuid4()}/../../outside.txt"]
    for key in good + bad:
        admin_enqueue(key)
    report = run_job(J.LocalFileDeleter(str(media)), batch=3)
    assert (report.deleted, report.invalid, report.failed) == (2, 4, 0) and not report.stopped_early
    assert J.exit_code(report) == J.EXIT_PROBLEMS
    assert not any((media / k).exists() for k in good) and outside.read_bytes() == b"keep"
    assert sorted(queue_rows()) == sorted((k, 1, "invalid_key") for k in bad)  # kept, not dropped
    with connect() as admin:
        waits = [float(w) for (w,) in rows(admin, "SELECT extract(epoch FROM next_try_at - now()) "
                                                  "FROM dolab.pending_file_deletions")]
    assert all(J.MAX_WAIT - 10 <= w <= J.MAX_WAIT + 10 for w in waits)


# ------------------------------------------------------------------ the downgrade (review of #60)

def test_the_downgrade_refuses_while_files_are_queued(scratch_db, monkeypatch):
    from alembic import command

    from tests.test_pg_roles import _alembic

    config = _alembic(scratch_db, monkeypatch)
    command.upgrade(config, "head")
    key = photo_key()
    with connect(database=scratch_db) as admin:
        admin.execute("INSERT INTO dolab.pending_file_deletions (key, attempts, next_try_at, created_at) "
                      "VALUES (%s, 0, now(), now())", (key,))
    with pytest.raises(Exception, match="refusing to downgrade 0007: 1 photo files are still queued"):
        command.downgrade(config, "0006_reconcile_purge")
    with connect(database=scratch_db) as admin:
        assert value(admin, "SELECT version_num FROM dolab.alembic_version") == "0007_file_deletion_queue"
        assert rows(admin, "SELECT key FROM dolab.pending_file_deletions") == [(key,)]
        assert "pending_file_deletions" in value(
            admin, "SELECT prosrc FROM pg_proc WHERE oid = 'dolab.apply_deletion(text,uuid,uuid)'::regprocedure")
        admin.execute("DELETE FROM dolab.pending_file_deletions")  # the files were dealt with
    command.downgrade(config, "0006_reconcile_purge")
    with connect(database=scratch_db) as admin:
        assert value(admin, "SELECT version_num FROM dolab.alembic_version") == "0006_reconcile_purge"
        assert value(admin, "SELECT to_regclass('dolab.pending_file_deletions')") is None
    command.upgrade(config, "head")


# ------------------------------------------------------------------ the database cannot be reached (review of #60)

def _job_env(monkeypatch, url, tmp_path):
    for name, value_ in {"DOLAB_ENV": "test", "DOLAB_FILE_CLEANER_DATABASE_URL": url,
                         "DOLAB_STORAGE": "local", "DOLAB_MEDIA_DIR": str(tmp_path)}.items():
        monkeypatch.setenv(name, value_)
    for name in ("DOLAB_JWT_SECRET", "DOLAB_LEDGER_SIGNING_KEY", "DOLAB_S3_BUCKET", "DOLAB_LEDGER_BUCKET"):
        monkeypatch.delenv(name, raising=False)


def _with(url: str, **parts) -> str:
    from sqlalchemy.engine import make_url

    return make_url(url).set(**parts).render_as_string(hide_password=False)


@pytest.mark.parametrize("case", ["wrong password", "no such database", "no server", "no such role"])
def test_a_connection_failure_exits_5_and_changes_nothing(db_client, monkeypatch, tmp_path, caplog, case):
    key = photo_key()
    admin_enqueue(key)
    before = queue_rows()
    good = _conninfo(E.FILE_CLEANER)
    url = {"wrong password": _with(good, password="not-the-password-x9"),
           "no such database": _with(good, database="dolab_does_not_exist"),
           "no server": _with(good, host="127.0.0.1", port=1),
           "no such role": _with(good, username="dolab_nobody")}[case]
    _job_env(monkeypatch, url, tmp_path)
    assert J.main([]) == J.EXIT_NO_DATABASE
    assert "could not connect to the database (OperationalError)" in caplog.text
    assert "Traceback" not in caplog.text and "not-the-password-x9" not in caplog.text
    assert queue_rows() == before


def test_the_connection_limit_is_a_connection_failure(db_client, monkeypatch, tmp_path, caplog):
    admin_enqueue(photo_key())
    before = queue_rows()
    _job_env(monkeypatch, _conninfo(E.FILE_CLEANER), tmp_path)
    holders = [cleaner_queue(), cleaner_queue()]  # CONNECTION LIMIT 2
    try:
        assert J.main([]) == J.EXIT_NO_DATABASE
    finally:
        for holder in holders:
            holder.close()
    assert "could not connect to the database (OperationalError)" in caplog.text
    assert queue_rows() == before
    assert J.main([]) == J.EXIT_OK and queue_rows() == []  # with a free connection it works
