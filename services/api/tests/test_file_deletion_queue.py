"""The photo file deletion queue through the API (plan 4.10.3; notice #59), on SQLite and on
PostgreSQL: every deletion queues its files in its own transaction, the server deletes them right
after the commit and removes their rows, and what it cannot delete stays queued (F1, F2, F6)."""
import pytest
from sqlalchemy import select

from app import services
from app.db import SessionLocal, is_sqlite
from app.models import PendingFileDeletion
from app.storage import StorageUnavailable, storage
from tests.test_photos import new_item, stored_files, upload


def queued() -> list[str]:
    with SessionLocal() as db:
        return sorted(db.scalars(select(PendingFileDeletion.key)))


def enqueue(key: str) -> None:
    """A row the queue already holds (written as the database owner on PostgreSQL: the API has
    no INSERT on the queue; only apply_deletion writes it)."""
    if is_sqlite:
        from app import clock

        with SessionLocal.begin() as db:
            db.add(PendingFileDeletion(key=key, attempts=4, next_try_at=clock.now(), created_at=clock.now()))
        return
    from tests.test_pg_roles import connect

    with connect() as admin:
        admin.execute("INSERT INTO dolab.pending_file_deletions (key, attempts, next_try_at, created_at) "
                      "VALUES (%s, 4, now(), now())", (key,))


def photo_keys(photo_id: str) -> list[str]:
    from app.models import ItemPhoto

    with SessionLocal() as db:
        p = db.get(ItemPhoto, photo_id)
        return sorted([p.original_key, p.thumb_key])


@pytest.fixture
def waits(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(services.time, "sleep", slept.append)
    return slept


@pytest.fixture
def storage_down(monkeypatch):
    calls: list[str] = []

    def down(key):
        calls.append(key)
        raise StorageUnavailable("down")

    monkeypatch.setattr(storage, "delete", down)
    return calls


def test_a_deletion_that_reaches_storage_leaves_no_row(client, signup):
    headers = signup()
    item_id = new_item(client, headers)
    for _ in range(2):
        upload(client, headers, item_id)
    assert client.delete(f"/items/{item_id}", headers=headers).status_code == 204
    assert stored_files(client, headers) == [] and queued() == []


def test_f1_storage_fails_twice_then_works(client, signup, monkeypatch, waits):
    headers = signup()
    item_id = new_item(client, headers)
    photo = upload(client, headers, item_id).json()
    real, failures = storage.delete, {}

    def flaky(key):
        failures[key] = failures.get(key, 0) + 1
        if failures[key] <= 2:
            raise StorageUnavailable("timeout")
        real(key)

    monkeypatch.setattr(storage, "delete", flaky)
    assert client.delete(f"/items/{item_id}/photos/{photo['id']}", headers=headers).status_code == 204
    assert stored_files(client, headers) == [] and queued() == []
    assert waits == [0.2, 0.6, 0.2, 0.6] and sum(waits) <= services.FILE_DELETE_WAIT_BUDGET


def test_f2_storage_down_the_deletion_still_succeeds_and_the_files_stay_queued(client, signup, storage_down, waits):
    headers = signup()
    item_id = new_item(client, headers)
    photo = upload(client, headers, item_id).json()
    keys = photo_keys(photo["id"])
    assert client.delete(f"/items/{item_id}/photos/{photo['id']}", headers=headers).status_code == 204
    assert client.get(f"/items/{item_id}/photos", headers=headers).json() == []
    assert client.get(photo["url"].removeprefix("/v1")).status_code == 404  # the old link is dead at once
    assert queued() == keys
    # Three tries on the first file, then it stops: storage is down, the rest waits in the queue.
    assert len(storage_down) == 3 and sum(waits) <= services.FILE_DELETE_WAIT_BUDGET


def test_f6_an_account_with_50_photos_and_storage_down_queues_100_files(client, signup, storage_down, waits):
    headers = signup()
    for start in range(0, 50, 6):
        item_id = new_item(client, headers)
        for _ in range(min(6, 50 - start)):
            assert upload(client, headers, item_id).status_code == 201
    assert client.delete("/me", headers=headers).status_code == 204
    keys = queued()
    assert len(keys) == 100 and len(set(keys)) == 100
    assert len(storage_down) == 3


def test_a_file_already_queued_is_queued_once(client, signup, storage_down, waits):
    headers = signup()
    item_id = new_item(client, headers)
    photo = upload(client, headers, item_id).json()
    keys = photo_keys(photo["id"])
    enqueue(keys[0])
    assert client.delete(f"/items/{item_id}", headers=headers).status_code == 204
    assert queued() == keys


def test_the_files_are_deleted_only_after_the_commit(client, signup, monkeypatch):
    from app import deletion

    headers = signup()
    item_id = new_item(client, headers)
    upload(client, headers, item_id)
    before = stored_files(client, headers)
    real = deletion.apply_deletion

    def apply_then_fail(db, *args):
        real(db, *args)
        raise RuntimeError("connection lost before commit")

    monkeypatch.setattr(deletion, "apply_deletion", apply_then_fail)
    with pytest.raises(RuntimeError):
        client.delete(f"/items/{item_id}", headers=headers)
    assert stored_files(client, headers) == before  # nothing touched in storage
    assert queued() == []  # and nothing queued: the queue rows rolled back with the deletion


def test_rows_stay_if_they_cannot_be_removed(client, signup, monkeypatch, caplog):
    headers = signup()
    item_id = new_item(client, headers)
    photo = upload(client, headers, item_id).json()
    keys = photo_keys(photo["id"])

    def broken(*_args):
        raise RuntimeError("database gone")

    monkeypatch.setattr(services, "sa_delete", broken)
    assert client.delete(f"/items/{item_id}", headers=headers).status_code == 204
    assert stored_files(client, headers) == []
    assert queued() == keys  # the queue job will find the files gone and remove the rows
    assert "queue rows of 2 deleted photo files not removed" in caplog.text
    assert not any(k in caplog.text for k in keys)
