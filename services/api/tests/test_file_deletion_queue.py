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
    monkeypatch.setattr(services, "_sleep", slept.append)
    return slept


class FakeClock:
    """services' monotonic clock, advanced by the fake storage to stand for slow calls."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


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
    assert waits == [0.2, 0.6, 0.2, 0.6] and sum(waits) <= services.FILE_FAILURE_BUDGET


def test_f2_storage_down_the_deletion_still_succeeds_and_the_files_stay_queued(client, signup, storage_down, waits):
    headers = signup()
    item_id = new_item(client, headers)
    photo = upload(client, headers, item_id).json()
    keys = photo_keys(photo["id"])
    assert client.delete(f"/items/{item_id}/photos/{photo['id']}", headers=headers).status_code == 204
    assert client.get(f"/items/{item_id}/photos", headers=headers).json() == []
    assert client.get(photo["url"].removeprefix("/v1")).status_code == 404  # the old link is dead at once
    assert queued() == keys
    # Three tries on each of the two files; two files failed in a row: storage is down.
    assert len(storage_down) == 6 and sum(waits) <= services.FILE_FAILURE_BUDGET


def test_f6_an_account_with_50_photos_and_storage_down_queues_100_files(client, signup, storage_down, waits):
    headers = signup()
    for start in range(0, 50, 6):
        item_id = new_item(client, headers)
        for _ in range(min(6, 50 - start)):
            assert upload(client, headers, item_id).status_code == 201
    assert client.delete("/me", headers=headers).status_code == 204
    keys = queued()
    assert len(keys) == 100 and len(set(keys)) == 100
    assert len(storage_down) == 6  # two files in a row failed: the other 98 wait in the queue


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


def test_l1_one_file_that_keeps_failing_does_not_stop_the_others(client, signup, monkeypatch, waits):
    headers = signup()
    item_id = new_item(client, headers)
    photos = [upload(client, headers, item_id).json() for _ in range(3)]
    stuck = photo_keys(photos[0]["id"])[0]
    real = storage.delete

    def one_bad(key):
        if key == stuck:
            raise StorageUnavailable("AccessDenied on this object")
        real(key)

    monkeypatch.setattr(storage, "delete", one_bad)
    assert client.delete(f"/items/{item_id}", headers=headers).status_code == 204
    assert queued() == [stuck]  # only the failing file stays queued; it is not lost
    assert stored_files(client, headers) == [stuck]


def test_an_invalid_storage_key_does_not_stop_immediate_deletion(client, signup, monkeypatch):
    headers = signup()
    item_id = new_item(client, headers)
    photo = upload(client, headers, item_id).json()
    bad = photo_keys(photo["id"])[0]
    real = storage.delete
    calls = []

    def invalid_key(key):
        calls.append(key)
        if key == bad:
            raise ValueError("invalid storage key")
        real(key)

    monkeypatch.setattr(storage, "delete", invalid_key)
    assert client.delete(f"/items/{item_id}/photos/{photo['id']}", headers=headers).status_code == 204
    assert calls.count(bad) == 1
    assert queued() == [bad]
    assert stored_files(client, headers) == [bad]


def test_two_files_failing_in_a_row_stop_the_attempt(client, signup, monkeypatch, waits):
    headers = signup()
    item_id = new_item(client, headers)
    photos = [upload(client, headers, item_id).json() for _ in range(3)]
    keys = sorted(k for p in photos for k in photo_keys(p["id"]))
    calls: list[str] = []

    def down(key):
        calls.append(key)
        raise StorageUnavailable("down")

    monkeypatch.setattr(storage, "delete", down)
    assert client.delete(f"/items/{item_id}", headers=headers).status_code == 204
    # Three tries on each of the first two files; the other four were never tried.
    assert len(calls) == 6 and len(set(calls)) == 2
    assert queued() == keys


def test_m2_slow_failing_calls_count_against_the_budget(client, signup, monkeypatch, waits):
    """The budget is real time spent on failures (calls and waits), not only the waits."""
    headers = signup()
    item_id = new_item(client, headers)
    photos = [upload(client, headers, item_id).json() for _ in range(3)]
    keys = sorted(k for p in photos for k in photo_keys(p["id"]))
    clock = FakeClock()
    monkeypatch.setattr(services, "_monotonic", clock)
    calls: list[str] = []

    def hangs_then_fails(key):
        calls.append(key)
        clock.now += 1.5  # each call hangs 1.5 s, then fails
        raise StorageUnavailable("read timeout")

    monkeypatch.setattr(storage, "delete", hangs_then_fails)
    assert client.delete(f"/items/{item_id}", headers=headers).status_code == 204
    # 1.5 s spent: a 0.2 s wait still fits the 2 s budget; then 1.5 + 0.2 + 1.5 = 3.2 s, so no
    # third try, and with the budget spent the failure ends the attempt: no other file is tried.
    assert len(calls) == 2 and len(set(calls)) == 1 and waits == [0.2]
    assert clock.now - 1000.0 + sum(waits) <= services.FILE_FAILURE_BUDGET + 1.5  # budget + one call
    assert queued() == keys


# ------------------------------------------------------------------ invalid keys and failures after the commit
# (PR #60 review, Ali 2026-11-28). The deletion is committed before any file is touched, so the
# request reports it as done whatever happens to the files; every file not deleted keeps its row.

def item_keys(client, headers, item_id) -> list[str]:
    return sorted(k for p in client.get(f"/items/{item_id}/photos", headers=headers).json() for k in photo_keys(p["id"]))


def storage_that(monkeypatch, bad: dict[str, Exception]) -> list[str]:
    """storage.delete raises the given error for the given keys and works for the others."""
    real, calls = storage.delete, []

    def delete(key):
        calls.append(key)
        if key in bad:
            raise bad[key]
        real(key)

    monkeypatch.setattr(storage, "delete", delete)
    return calls


def test_an_item_with_a_mixed_batch_of_invalid_and_valid_keys(client, signup, monkeypatch, waits):
    headers = signup()
    item_id = new_item(client, headers)
    for _ in range(3):
        upload(client, headers, item_id)
    keys = item_keys(client, headers, item_id)
    invalid = [keys[1], keys[4]]
    calls = storage_that(monkeypatch, {k: ValueError("invalid storage key") for k in invalid})
    assert client.delete(f"/items/{item_id}", headers=headers).status_code == 204
    assert client.get(f"/items/{item_id}", headers=headers).status_code == 404
    assert sorted(calls) == keys and waits == []  # every file tried once; invalid ones not retried
    assert queued() == sorted(invalid)  # kept for the queue job, which marks them invalid_key
    assert stored_files(client, headers) == sorted(invalid)


def test_an_account_with_invalid_keys_is_deleted_and_its_other_files_too(client, signup, monkeypatch, waits):
    headers = signup()
    user_id = client.get("/me", headers=headers).json()["id"]
    keys = []
    for _ in range(2):
        item_id = new_item(client, headers)
        upload(client, headers, item_id)
        upload(client, headers, item_id)
        keys += item_keys(client, headers, item_id)
    keys.sort()
    invalid = [keys[0], keys[5]]
    storage_that(monkeypatch, {k: ValueError("invalid storage key") for k in invalid})
    assert client.delete("/me", headers=headers).status_code == 204
    assert client.get("/me", headers=headers).status_code == 401
    assert queued() == sorted(invalid)
    assert storage.list_keys(f"{user_id}/") == sorted(invalid)


def test_an_unexpected_storage_error_after_the_commit_is_not_a_failed_deletion(client, signup, monkeypatch, waits):
    headers = signup()
    item_id = new_item(client, headers)
    upload(client, headers, item_id)
    upload(client, headers, item_id)
    keys = item_keys(client, headers, item_id)
    storage_that(monkeypatch, {k: RuntimeError("driver bug") for k in keys})
    assert client.delete(f"/items/{item_id}", headers=headers).status_code == 204
    assert client.get(f"/items/{item_id}", headers=headers).status_code == 404
    assert queued() == keys and stored_files(client, headers) == keys  # nothing lost


def test_a_cleanup_that_breaks_entirely_still_reports_the_deletion(client, signup, monkeypatch, caplog):
    headers = signup()
    item_id = new_item(client, headers)
    photo = upload(client, headers, item_id).json()
    keys = photo_keys(photo["id"])

    def broken(db, keys_):
        raise RuntimeError("cleanup bug")

    monkeypatch.setattr(services, "delete_file_keys", broken)
    assert client.delete(f"/items/{item_id}/photos/{photo['id']}", headers=headers).status_code == 204
    assert client.get(f"/items/{item_id}/photos", headers=headers).json() == []
    assert queued() == keys and stored_files(client, headers) == keys
    assert "immediate deletion of 2 photo files stopped (RuntimeError)" in caplog.text
    assert not any(k in caplog.text for k in keys)
    # The account deletion goes through the same guard.
    assert client.delete("/me", headers=headers).status_code == 204
    assert client.get("/me", headers=headers).status_code == 401


def test_a_failed_upload_cleans_up_past_an_invalid_key_and_keeps_its_own_error(client, signup, monkeypatch, caplog):
    from sqlalchemy.orm import Session

    from app.models import ItemPhoto

    headers = signup()
    item_id = new_item(client, headers)
    real_commit, calls = Session.commit, []

    def failing_commit(self):
        # Only the commit that would save the new photo record fails (sign-in, rate limits and
        # the rest of the request commit as usual).
        if any(isinstance(obj, ItemPhoto) for obj in self.new):
            raise RuntimeError("database gone during upload")
        real_commit(self)

    real_delete = storage.delete

    def first_key_invalid(key):
        calls.append(key)
        if len(calls) == 1:
            raise ValueError("invalid storage key")
        real_delete(key)

    monkeypatch.setattr(Session, "commit", failing_commit)
    monkeypatch.setattr(storage, "delete", first_key_invalid)
    with pytest.raises(RuntimeError, match="database gone during upload"):  # the upload's own error
        upload(client, headers, item_id)
    monkeypatch.setattr(Session, "commit", real_commit)
    assert len(calls) == 2  # the second file was still deleted after the first one's error
    assert stored_files(client, headers) == [calls[0]]  # the one left is an orphan for the orphan job
    assert "1 photo files of a failed upload left in storage for the orphan job (ValueError)" in caplog.text
    assert calls[0] not in caplog.text


# ------------------------------------------------------------------ only photo keys reach storage (review of #60)

def set_photo_key(photo_id: str, column: str, key: str) -> None:
    """Give a photo record a key the server would never make (the API role cannot update photos
    on PostgreSQL, so this is done as the database owner there)."""
    if is_sqlite:
        from app.models import ItemPhoto

        with SessionLocal.begin() as db:
            setattr(db.get(ItemPhoto, photo_id), column, key)
        return
    from tests.test_pg_roles import connect

    with connect() as admin:
        admin.execute(f"UPDATE dolab.item_photos SET {column} = %s WHERE id = %s", (key, photo_id))


@pytest.mark.parametrize("not_a_photo_key", ["u/x.jpg", "newline"])
def test_h1_a_key_that_is_not_a_photo_key_never_reaches_storage(client, signup, monkeypatch, waits, not_a_photo_key):
    headers = signup()
    item_id = new_item(client, headers)
    first = upload(client, headers, item_id).json()
    second = upload(client, headers, item_id).json()
    first_keys, second_keys = photo_keys(first["id"]), photo_keys(second["id"])
    real_original = first_keys[0]
    bad = real_original + "\n" if not_a_photo_key == "newline" else "u/x.jpg"
    if bad == "u/x.jpg":
        storage.save(bad, b"not a photo: must survive")  # a file that passes the general key check
    set_photo_key(first["id"], "original_key", bad)
    calls = storage_that(monkeypatch, {})
    try:
        assert client.delete(f"/items/{item_id}", headers=headers).status_code == 204
        assert bad not in calls  # never sent to storage
        good = [first_keys[1], *second_keys]
        assert sorted(calls) == sorted(good)  # the other files were all deleted
        assert queued() == [bad]  # kept in the queue for the job, which marks it invalid_key
        if bad == "u/x.jpg":
            assert storage.list_keys("u/") == ["u/x.jpg"]
    finally:
        monkeypatch.undo()
        if bad == "u/x.jpg":
            storage.delete("u/x.jpg")


def test_l5_a_partly_saved_thumbnail_is_removed_and_the_upload_error_kept(client, signup, monkeypatch):
    headers = signup()
    item_id = new_item(client, headers)
    real_save = storage.save

    def thumb_saved_then_response_lost(key, data):
        real_save(key, data)
        if key.endswith("_thumb.jpg"):
            raise StorageUnavailable("connection reset after the object was stored")

    monkeypatch.setattr(storage, "save", thumb_saved_then_response_lost)
    response = upload(client, headers, item_id)
    assert response.status_code == 503  # the upload's own error
    assert stored_files(client, headers) == []  # neither the original nor the thumbnail is left
    assert client.get(f"/items/{item_id}/photos", headers=headers).json() == []
    assert queued() == []
