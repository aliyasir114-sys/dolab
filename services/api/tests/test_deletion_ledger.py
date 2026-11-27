"""Deletion ledger, part 2 (plan 4.10.1 and 4.10.1د; Ali's decisions 2026-11-25): every deletion
of an item, a photo or an account writes one signed entry, confirmed before anything is deleted;
a ledger failure deletes nothing. Also DOLAB_ENV and the ledger settings (SIG10)."""
import base64
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app import clock, deletion, ledger, ledger_store
from app.config import settings
from app.db import SessionLocal
from app.models import DeletionIntent, Item, ItemPhoto, User
from app.storage import storage
from tests.test_photos import new_item, upload

API_DIR = Path(__file__).resolve().parents[1]
PUBLIC = {settings.ledger_key_id: deletion.signing_key.public_key()}


# ------------------------------------------------------------------ reading the ledger back (tests only)

def entries() -> dict[str, bytes]:
    """Every entry written so far, by object name. The server itself can never read the ledger."""
    if settings.ledger_storage == "s3":
        import boto3.session

        client = boto3.session.Session().client("s3", endpoint_url=settings.s3_endpoint_url,
                                                region_name=settings.s3_region)
        found = {}
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=settings.ledger_bucket):
            for item in page.get("Contents", []):
                found[item["Key"]] = client.get_object(Bucket=settings.ledger_bucket, Key=item["Key"])["Body"].read()
        return found
    root = Path(settings.ledger_dir)
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*.json")} if root.exists() else {}


def new_entries(before: dict) -> list[ledger.Entry]:
    """Entries added since `before`, each verified with the public key (as the job and the
    restore script will)."""
    return [ledger.verify_entry(body, name, PUBLIC) for name, body in entries().items() if name not in before]


def intents() -> list[DeletionIntent]:
    with SessionLocal() as db:
        return list(db.scalars(select(DeletionIntent)))


def count(model, **where) -> int:
    with SessionLocal() as db:
        statement = select(func.count()).select_from(model)
        for column, value in where.items():
            statement = statement.where(getattr(model, column) == value)
        return db.scalar(statement)


def me(client, headers) -> str:
    return client.get("/me", headers=headers).json()["id"]


# ------------------------------------------------------------------ one entry per deletion

def test_deleting_an_item_writes_one_verified_entry(client, signup):
    headers = signup()
    user_id, item_id = me(client, headers), new_item(client, headers)
    before = entries()
    assert client.delete(f"/items/{item_id}", headers=headers).status_code == 204
    (entry,) = new_entries(before)
    assert (entry.entity_type, entry.entity_id, entry.user_id) == ("item", item_id, user_id)
    assert entry.key_id == settings.ledger_key_id
    assert intents() == []  # removed with the deletion


def test_deleting_a_photo_writes_one_photo_entry(client, signup):
    headers = signup()
    item_id = new_item(client, headers)
    photo = upload(client, headers, item_id).json()
    before = entries()
    assert client.delete(f"/items/{item_id}/photos/{photo['id']}", headers=headers).status_code == 204
    (entry,) = new_entries(before)
    assert (entry.entity_type, entry.entity_id) == ("photo", photo["id"])


def test_deleting_an_account_is_one_user_entry_with_ids_only(client, signup):
    # B5: identifiers only, no email or name; one entry however much the account holds.
    headers = signup("someone.private@example.com")
    user_id = me(client, headers)
    for _ in range(3):
        item_id = new_item(client, headers)
        upload(client, headers, item_id)
        upload(client, headers, item_id)
    before = entries()
    assert client.delete("/me", headers=headers).status_code == 204
    (entry,) = new_entries(before)
    assert (entry.entity_type, entry.entity_id, entry.user_id) == ("user", user_id, user_id)
    stored = entries()[entry.object_name]
    assert b"someone" not in stored and b"example.com" not in stored
    assert set(json.loads(stored)) == set(ledger.FIELDS)
    assert count(User, id=user_id) == 0 and storage.list_keys(f"{user_id}/") == []


def test_the_entry_is_confirmed_before_anything_is_deleted(client, signup, monkeypatch):
    headers = signup()
    item_id = new_item(client, headers)
    photo = upload(client, headers, item_id).json()
    seen = {}
    real = ledger_store.writer

    class Watching:
        def put(self, name, body):
            # At the moment the entry is written, the intent is committed and nothing is deleted.
            seen["intent"] = [i.entity_id for i in intents()]
            seen["item"] = count(Item, id=item_id)
            seen["photo"] = count(ItemPhoto, id=photo["id"])
            real.put(name, body)

    monkeypatch.setattr(ledger_store, "writer", Watching())
    assert client.delete(f"/items/{item_id}", headers=headers).status_code == 204
    assert seen == {"intent": [item_id], "item": 1, "photo": 1}
    assert count(Item, id=item_id) == 0


# ------------------------------------------------------------------ the ledger fails: nothing is deleted

class Down:
    def __init__(self):
        self.calls = 0

    def put(self, name, body):
        self.calls += 1
        raise ledger_store.LedgerUnavailable("connection refused")


@pytest.mark.parametrize("what", ["item", "photo", "account"])
def test_db12_a_ledger_failure_deletes_nothing_and_removes_the_intent(client, signup, monkeypatch, what):
    headers = signup()
    user_id = me(client, headers)
    item_id = new_item(client, headers)
    photo = upload(client, headers, item_id).json()
    down = Down()
    monkeypatch.setattr(ledger_store, "writer", down)
    path = {"item": f"/items/{item_id}", "photo": f"/items/{item_id}/photos/{photo['id']}", "account": "/me"}[what]
    response = client.delete(path, headers=headers)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"
    assert down.calls == 3
    assert intents() == []
    assert count(User, id=user_id) == 1 and count(Item, id=item_id) == 1 and count(ItemPhoto, id=photo["id"]) == 1
    assert len(storage.list_keys(f"{user_id}/")) == 2  # the photo files too
    assert client.get(photo["thumb_url"].removeprefix("/v1")).status_code == 200


def test_if_the_intent_cannot_be_removed_either_still_nothing_is_deleted(client, signup, monkeypatch):
    headers = signup()
    item_id = new_item(client, headers)
    monkeypatch.setattr(ledger_store, "writer", Down())

    def broken(_entry_id):
        raise RuntimeError("database went away")

    monkeypatch.setattr(deletion, "_remove_intent", broken)
    assert client.delete(f"/items/{item_id}", headers=headers).status_code == 503
    assert count(Item, id=item_id) == 1
    # An intent without an entry: the reconciliation job finds no signed entry and does nothing;
    # the clean-up of intents older than 31 days removes it (next part).
    assert [i.entity_id for i in intents()] == [item_id]


def test_if_the_deletion_fails_after_the_entry_both_entry_and_intent_remain(client, signup, monkeypatch):
    # The reverse case: entry written, deletion not committed. dolab.reconcile_apply completes it
    # from the matching intent (test_pg_deleter_functions.py).
    headers = signup()
    item_id = new_item(client, headers)
    before = entries()
    real = deletion.apply_deletion

    def apply_then_fail(db, *args):
        real(db, *args)
        raise RuntimeError("connection lost before commit")

    monkeypatch.setattr(deletion, "apply_deletion", apply_then_fail)
    with pytest.raises(RuntimeError):
        client.delete(f"/items/{item_id}", headers=headers)
    (entry,) = new_entries(before)
    assert entry.entity_id == item_id
    assert [(i.entry_id, i.entity_id, i.user_id) for i in intents()] == [(entry.entry_id, item_id, entry.user_id)]
    assert count(Item, id=item_id) == 1


# ------------------------------------------------------------------ writing: retries, budget, no overwrite

def test_two_failures_then_success_completes_the_deletion(client, signup, monkeypatch):
    headers = signup()
    item_id = new_item(client, headers)
    real, calls = ledger_store.writer, []

    class Flaky:
        def put(self, name, body):
            calls.append(name)
            if len(calls) < 3:
                raise ledger_store.LedgerUnavailable("timeout")
            real.put(name, body)

    monkeypatch.setattr(ledger_store, "writer", Flaky())
    assert client.delete(f"/items/{item_id}", headers=headers).status_code == 204
    assert len(calls) == 3 and len(set(calls)) == 1  # the same entry each time
    assert count(Item, id=item_id) == 0


def test_writing_gives_up_within_two_seconds():
    entry = ledger.make_entry(deletion.signing_key, settings.ledger_key_id, "item",
                              "5c1f9e2d-8b3a-4f6e-a1d7-9e0c2b4f6a18", "a3d8f1c2-4b6e-4d9a-8f2c-1e7b5a9c3d40",
                              "2026-11-25T09:14:03.120458Z", environment=settings.environment)

    class Slow:
        calls = 0

        def put(self, name, body):
            Slow.calls += 1
            time.sleep(0.9)
            raise ledger_store.LedgerUnavailable("timeout")

    started = time.monotonic()
    with pytest.raises(ledger_store.LedgerUnavailable):
        ledger_store.write_entry(Slow(), entry)
    assert time.monotonic() - started < ledger_store.BUDGET_SECONDS
    assert Slow.calls == 2  # a third try would not fit in the budget


def test_s3_timeouts_obey_deadline_and_failure_preserves_item(client, signup, monkeypatch):
    # Clients that take their whole socket timeout and then fail: each retry uses a smaller tier,
    # and the write still ends within the budget.
    from botocore.exceptions import ClientError

    headers = signup()
    item_id = new_item(client, headers)
    used = []

    class DelayedS3:
        def __init__(self, total):
            self.total = total

        def put_object(self, **_kwargs):
            used.append(self.total)
            time.sleep(self.total)
            raise ClientError({"Error": {"Code": "SlowDown", "Message": "timed out"}}, "PutObject")

    tiers = [(c + r, DelayedS3(c + r)) for c, r in ledger_store.TIERS]
    monkeypatch.setattr(ledger_store, "writer", ledger_store.S3Ledger("ledger", clients=tiers))
    durations = []
    real_write_entry = ledger_store.write_entry

    def timed_write_entry(*args, **kwargs):
        started = time.monotonic()
        try:
            return real_write_entry(*args, **kwargs)
        finally:
            durations.append(time.monotonic() - started)

    monkeypatch.setattr(ledger_store, "write_entry", timed_write_entry)
    response = client.delete(f"/items/{item_id}", headers=headers)

    assert response.status_code == 503
    assert durations and durations[0] < ledger_store.BUDGET_SECONDS
    assert len(used) >= 2 and used == sorted(used, reverse=True) and used[1] < used[0]
    assert count(Item, id=item_id) == 1
    assert intents() == []


def test_s3_clients_are_made_once_from_their_own_sessions(monkeypatch):
    # Never boto3.client(), the shared default session; one fresh session per client, at start-up.
    import boto3
    import boto3.session

    def shared(*_args, **_kwargs):
        raise AssertionError("boto3.client() uses the shared default session")

    monkeypatch.setattr(boto3, "client", shared)
    sessions = []
    real_session = boto3.session.Session

    class Counting(real_session):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            sessions.append(self)

    monkeypatch.setattr(boto3.session, "Session", Counting)
    writer = ledger_store.S3Ledger("ledger", endpoint_url="http://127.0.0.1:9")
    assert len(sessions) == len(ledger_store.TIERS) == len(writer.clients)
    assert len({id(s) for s in sessions}) == len(sessions)
    assert len({id(c) for _, c in writer.clients}) == len(writer.clients)
    assert [round(total, 2) for total, _ in writer.clients] == [round(c + r, 2) for c, r in ledger_store.TIERS]
    # Writing makes no session or client.
    with pytest.raises(ledger_store.LedgerUnavailable):
        writer.put_with_timeout("2026/11/25/x.json", b"{}", 0.01)  # no tier fits: refused at once
    assert len(sessions) == len(ledger_store.TIERS)


def _silent_server():
    """A TCP server that accepts connections and never answers: an unresponsive ledger."""
    import socket
    import threading

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(16)
    held = []
    stop = threading.Event()

    def accept():
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                held.append(server.accept()[0])
            except OSError:
                continue

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()

    def close():
        stop.set()
        thread.join(2)
        for connection in held:
            connection.close()
        server.close()

    return server.getsockname()[1], close


def test_an_unresponsive_ledger_fails_within_two_seconds_and_deletes_nothing(client, signup, monkeypatch):
    # The real S3Ledger and its real clients, against a server that never answers.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    port, close = _silent_server()
    try:
        writer = ledger_store.S3Ledger("ledger", endpoint_url=f"http://127.0.0.1:{port}", encryption=None)
        monkeypatch.setattr(ledger_store, "writer", writer)
        headers = signup()
        item_id = new_item(client, headers)
        photo = upload(client, headers, item_id).json()
        started = time.monotonic()
        response = client.delete(f"/items/{item_id}", headers=headers)
        elapsed = time.monotonic() - started
    finally:
        close()
    assert response.status_code == 503
    assert elapsed < ledger_store.BUDGET_SECONDS + 0.5, elapsed  # the write itself within 2 s; the rest is the request
    assert count(Item, id=item_id) == 1 and count(ItemPhoto, id=photo["id"]) == 1
    assert intents() == []


@pytest.mark.skipif(settings.ledger_storage != "s3", reason="needs the S3 ledger (MinIO in CI)")
def test_concurrent_deletions_write_their_entries_through_the_shared_clients(client, monkeypatch):
    # Many request threads writing at once through the real S3Ledger on MinIO.
    import threading

    import boto3

    def shared(*_args, **_kwargs):
        raise AssertionError("boto3.client() uses the shared default session")

    monkeypatch.setattr(boto3, "client", shared)
    before = entries()
    made, durations, errors = [], [], []
    lock = threading.Lock()

    def write(n):
        entry = ledger.make_entry(deletion.signing_key, settings.ledger_key_id, "item", str(uuid.uuid4()),
                                  str(uuid.uuid4()), clock.now(), environment=settings.environment)
        started = time.monotonic()
        try:
            ledger_store.write_entry(ledger_store.writer, entry)
        except Exception as error:  # reported below
            with lock:
                errors.append(error)
            return
        with lock:
            made.append(entry)
            durations.append(time.monotonic() - started)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert errors == []
    assert len(made) == 16
    assert max(durations) < ledger_store.BUDGET_SECONDS, durations
    written = {e.entry_id for e in new_entries(before)}
    assert written == {e.entry_id for e in made}


def test_an_entry_is_never_replaced(client, tmp_path):
    entry = ledger.make_entry(deletion.signing_key, settings.ledger_key_id, "photo",
                              "5c1f9e2d-8b3a-4f6e-a1d7-9e0c2b4f6a18", "a3d8f1c2-4b6e-4d9a-8f2c-1e7b5a9c3d40",
                              "2026-11-25T09:14:03.120458Z", environment=settings.environment)
    for writer in (ledger_store.LocalLedger(str(tmp_path)), ledger_store.writer):
        ledger_store.write_entry(writer, entry)
        calls = []
        method = "put_with_timeout" if hasattr(writer, "put_with_timeout") else "put"
        real_put = getattr(writer, method)

        def counting(name, body, *args, real_put=real_put):
            calls.append(name)
            return real_put(name, body, *args)

        setattr(writer, method, counting)
        with pytest.raises(ledger_store.LedgerEntryExists):
            ledger_store.write_entry(writer, entry)
        assert len(calls) == 1  # not retried
        delattr(writer, method)


def test_local_entries_are_created_private_and_never_overwritten(tmp_path, monkeypatch):
    """Every platform: the entry is created with mode 0600 and O_CREAT | O_EXCL, and a second
    write of the same name is refused. (Windows ignores POSIX mode bits; there the folder's ACL
    decides who can read it, and the local ledger is for development only: staging and
    production require the S3 ledger.)"""
    opened = []
    real_open = os.open

    def recording_open(path, flags, mode=0o777, *args, **kwargs):
        opened.append((flags, mode))
        return real_open(path, flags, mode, *args, **kwargs)

    monkeypatch.setattr(ledger_store.os, "open", recording_open)
    writer = ledger_store.LocalLedger(str(tmp_path))
    writer.put("2026/11/25/x.json", b"{}")
    ((flags, mode),) = opened
    assert mode == 0o600
    assert flags & os.O_CREAT and flags & os.O_EXCL and flags & os.O_WRONLY
    with pytest.raises(ledger_store.LedgerEntryExists):
        writer.put("2026/11/25/x.json", b"{\"other\": 1}")
    assert (tmp_path / "2026/11/25/x.json").read_bytes() == b"{}"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits; Windows is covered by the test above")
def test_local_entries_are_private_files(tmp_path):
    writer = ledger_store.LocalLedger(str(tmp_path))
    writer.put("2026/11/25/x.json", b"{}")
    assert (os.stat(tmp_path / "2026/11/25/x.json").st_mode & 0o777) == 0o600


# ------------------------------------------------------------------ the known gap (Ali, 2026-11-25)

def test_clearing_the_profile_is_not_yet_in_the_ledger(client, signup):
    # Format v1 has no "profile" entity. This test changes, on purpose, when format v2 adds it.
    headers = signup()
    client.put("/me/profile", json={"height_cm": 180}, headers=headers)
    before = entries()
    assert client.delete("/me/profile", headers=headers).status_code == 204
    assert entries() == before


# ------------------------------------------------------------------ settings: DOLAB_ENV and the ledger key (SIG10)

def _seed() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()


def _start(env: dict[str, str]) -> subprocess.CompletedProcess:
    full = {k: v for k, v in os.environ.items() if not k.startswith(("DOLAB_",))}
    full.update({"DOLAB_DATABASE_URL": "sqlite:///./unused.db", **env})
    code = "from app.config import settings as s; print(s.environment, s.ledger_key_id, s.ledger_storage)"
    return subprocess.run([sys.executable, "-c", code], cwd=API_DIR, env=full, capture_output=True, text=True)


STRICT = {"DOLAB_JWT_SECRET": "x" * 40, "DOLAB_DATABASE_URL": "postgresql+psycopg://u:p@localhost/dolab",
          "DOLAB_STORAGE": "s3", "DOLAB_S3_BUCKET": "photos"}


@pytest.mark.parametrize("environment", ["development", "test"])
def test_development_and_tests_run_without_a_key(environment):
    result = _start({"DOLAB_ENV": environment})
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [environment, "test-1", "local"]


def test_dolab_env_defaults_to_development():
    assert _start({}).stdout.split()[0] == "development"


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_staging_and_production_start_with_a_real_key_and_a_ledger_bucket(environment):
    result = _start({**STRICT, "DOLAB_ENV": environment, "DOLAB_LEDGER_SIGNING_KEY": _seed(),
                     "DOLAB_LEDGER_KEY_ID": "prod-2026-1", "DOLAB_LEDGER_BUCKET": "ledger"})
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [environment, "prod-2026-1", "s3"]


@pytest.mark.parametrize("value", ["prod", "Production", "stage", "dev", "PRODUCTION", " production", ""])
def test_an_unknown_environment_stops_the_server(value):
    result = _start({"DOLAB_ENV": value})
    assert result.returncode != 0
    assert "DOLAB_ENV must be one of development, test, staging, production" in result.stderr


@pytest.mark.parametrize("environment", ["staging", "production"])
@pytest.mark.parametrize("case", ["no-key", "test-key-1", "test-key-2", "local-ledger", "no-bucket",
                                  "photo-bucket", "no-key-id", "bad-key"])
def test_sig10_and_the_ledger_settings_in_staging_and_production(environment, case):
    good = {**STRICT, "DOLAB_ENV": environment, "DOLAB_LEDGER_SIGNING_KEY": _seed(),
            "DOLAB_LEDGER_KEY_ID": "prod-1", "DOLAB_LEDGER_BUCKET": "ledger"}
    vectors = json.loads((API_DIR.parents[1] / "docs/specs/ledger-entry-v1-vectors.json").read_text())
    seeds = {k["key_id"]: k["seed_b64url"] for k in vectors["keys"]}
    env, expected = {
        "no-key": ({**good, "DOLAB_LEDGER_SIGNING_KEY": ""}, "DOLAB_LEDGER_SIGNING_KEY must be set"),
        "test-key-1": ({**good, "DOLAB_LEDGER_SIGNING_KEY": seeds["test-1"]}, "refused: test_key"),
        "test-key-2": ({**good, "DOLAB_LEDGER_SIGNING_KEY": seeds["test-2"]}, "refused: test_key"),
        "local-ledger": ({**good, "DOLAB_LEDGER_STORAGE": "local"}, "must be 's3'"),
        "no-bucket": ({**good, "DOLAB_LEDGER_BUCKET": ""}, "DOLAB_LEDGER_BUCKET must be set"),
        "photo-bucket": ({**good, "DOLAB_LEDGER_BUCKET": "photos"}, "a bucket of its own"),
        "no-key-id": ({**good, "DOLAB_LEDGER_KEY_ID": ""}, "DOLAB_LEDGER_KEY_ID"),
        "bad-key": ({**good, "DOLAB_LEDGER_SIGNING_KEY": "not-a-key"}, "refused: bad_key"),
    }[case]
    result = _start(env)
    assert result.returncode != 0
    assert expected in result.stderr
    assert env.get("DOLAB_LEDGER_SIGNING_KEY") not in result.stderr or not env.get("DOLAB_LEDGER_SIGNING_KEY")
