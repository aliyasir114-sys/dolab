"""The orphan-files job (plan 4.6; F4): what it deletes, what it never deletes, and how it stops.

Runs on every storage backend: disk in the SQLite and PostgreSQL jobs, MinIO in the S3 job.
"""
import uuid
from datetime import timedelta

import pytest
from botocore.stub import Stubber
from sqlalchemy import delete

from app import clock
from app.db import SessionLocal
from app.jobs import orphans
from app.models import ItemPhoto
from app.storage import LocalStorage, S3Storage, StorageUnavailable, storage
from tests.test_photos import new_item, upload

LATER = timedelta(hours=2)  # "now" for the job: every file made in the test is old enough


def photo_key(kind: str = "thumb") -> str:
    suffix = "thumb.jpg" if kind == "thumb" else "original.jpg"
    return f"{uuid.uuid4()}/{uuid.uuid4()}_{suffix}"


@pytest.fixture
def scratch(tmp_path):
    """A storage of its own, so counts are exact whatever other tests left behind."""
    return LocalStorage(str(tmp_path / "media"))


def later():
    return clock.now() + LATER


def forget_record(photo_id: str) -> None:
    """Remove a photo's record but not its files: what a failed file delete leaves behind."""
    with SessionLocal.begin() as db:
        db.execute(delete(ItemPhoto).where(ItemPhoto.id == photo_id))


# ------------------------------------------------------------------ what is deleted

def test_orphans_are_deleted_and_files_with_records_are_kept(client, signup):
    headers = signup()
    item_id = new_item(client, headers)
    kept = upload(client, headers, item_id).json()
    lost = upload(client, headers, item_id).json()
    user_id = client.get("/me", headers=headers).json()["id"]
    with SessionLocal() as db:
        kept_keys = {db.get(ItemPhoto, kept["id"]).original_key, db.get(ItemPhoto, kept["id"]).thumb_key}
        lost_keys = {db.get(ItemPhoto, lost["id"]).original_key, db.get(ItemPhoto, lost["id"]).thumb_key}
    forget_record(lost["id"])

    report = orphans.sweep(storage, batch=100_000, now=later())

    assert lost_keys <= set(report.deleted)
    assert not kept_keys & set(report.deleted)
    assert set(storage.list_keys(f"{user_id}/")) == kept_keys
    assert report.failed == []
    # The photo that still has a record still works.
    assert client.get(kept["thumb_url"].removeprefix("/v1")).status_code == 200


def test_recent_files_are_never_orphans(client, signup):
    # An upload saves its files before its record is committed: a young file without a record
    # may be an upload in progress.
    headers = signup()
    photo = upload(client, headers, new_item(client, headers)).json()
    user_id = client.get("/me", headers=headers).json()["id"]
    forget_record(photo["id"])
    report = orphans.sweep(storage, batch=100_000)  # now: the files are seconds old
    assert not [k for k in report.deleted if k.startswith(f"{user_id}/")]
    assert len(storage.list_keys(f"{user_id}/")) == 2
    assert report.too_recent >= 2
    # An hour later they go.
    orphans.sweep(storage, batch=100_000, now=clock.now() + timedelta(minutes=61))
    assert storage.list_keys(f"{user_id}/") == []


def test_only_photo_files_are_touched(client, scratch):
    others = ["notes/readme.txt", f"{uuid.uuid4()}/backup.zip", f"{uuid.uuid4()}/{uuid.uuid4()}_thumb.png",
              f"{uuid.uuid4()}/{uuid.uuid4()}_other.jpg", "exports/x/" + photo_key()]
    for key in others:
        scratch.save(key, b"x")
    report = orphans.sweep(scratch, now=later())
    assert report.ignored_other == len(others)
    assert report.deleted == []
    assert scratch.list_keys("") == sorted(others)


def test_dry_run_deletes_nothing(client, scratch):
    keys = sorted(photo_key() for _ in range(3))
    for key in keys:
        scratch.save(key, b"x")
    report = orphans.sweep(scratch, dry_run=True, now=later())
    assert sorted(report.deleted) == keys
    assert scratch.list_keys("") == keys
    assert "would delete 3" in report.summary()


# ------------------------------------------------------------------ batches and re-runs

def test_the_batch_limit_bounds_each_run_and_re_runs_finish_the_job(client, scratch):
    keys = sorted(photo_key() for _ in range(5))
    for key in keys:
        scratch.save(key, b"x")
    runs = []
    for _ in range(4):
        report = orphans.sweep(scratch, batch=2, now=later())
        runs.append((len(report.deleted), report.remaining))
    assert runs == [(2, 3), (2, 1), (1, 0), (0, 0)]
    assert scratch.list_keys("") == []


def test_a_file_already_gone_is_not_an_error(client, scratch):
    # Two runs at once, or a file deleted by hand between listing and deleting.
    key = photo_key()
    scratch.save(key, b"x")

    class Racing(LocalStorage):
        def list_objects(self, prefix):
            listed = super().list_objects(prefix)
            super().delete(key)  # someone else deletes it right after the listing
            return listed

    report = orphans.sweep(Racing(str(scratch.root)), now=later())
    assert report.deleted == [key] and report.failed == []


# ------------------------------------------------------------------ failures

def test_a_failed_delete_is_reported_does_not_stop_the_rest_and_is_retried(client, scratch):
    keys = sorted(photo_key() for _ in range(3))
    for key in keys:
        scratch.save(key, b"x")
    stuck = keys[1]

    class Flaky(LocalStorage):
        def delete(self, key):
            if key == stuck:
                raise StorageUnavailable("timeout")
            super().delete(key)

    report = orphans.sweep(Flaky(str(scratch.root)), now=later())
    assert report.failed == [stuck]
    assert sorted(report.deleted) == [keys[0], keys[2]]
    assert scratch.list_keys("") == [stuck]
    # The next run, with storage healthy again, finishes it.
    assert orphans.sweep(scratch, now=later()).deleted == [stuck]
    assert scratch.list_keys("") == []


def test_a_disk_error_mid_batch_is_reported_and_the_rest_still_go(client, scratch, monkeypatch):
    # The real LocalStorage, with the disk refusing to remove one file (review of PR #44).
    import pathlib

    keys = sorted(photo_key() for _ in range(3))
    for key in keys:
        scratch.save(key, b"x")
    stuck = keys[1]
    real_unlink = pathlib.Path.unlink

    def refusing_unlink(self, *args, **kwargs):
        if self.as_posix().endswith(stuck):
            raise PermissionError(13, "Permission denied", str(self))
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "unlink", refusing_unlink)
    report = orphans.sweep(scratch, now=later())
    assert report.failed == [stuck]
    assert sorted(report.deleted) == [keys[0], keys[2]]  # the file after the failure was handled
    assert scratch.list_keys("") == [stuck]

    monkeypatch.setattr(pathlib.Path, "unlink", real_unlink)  # the disk recovers
    assert orphans.sweep(scratch, now=later()).deleted == [stuck]
    assert scratch.list_keys("") == []


def test_a_raw_os_error_from_a_storage_cannot_stop_the_batch(client, scratch):
    keys = sorted(photo_key() for _ in range(3))
    for key in keys:
        scratch.save(key, b"x")

    class Raw(LocalStorage):
        def delete(self, key):
            if key == keys[0]:
                raise OSError(5, "Input/output error")
            super().delete(key)

    report = orphans.sweep(Raw(str(scratch.root)), now=later())
    assert report.failed == [keys[0]] and sorted(report.deleted) == keys[1:]


class Spy(LocalStorage):
    """Fails the listing, and records any delete (there must be none)."""

    deletes: list[str]

    def __init__(self, root):
        super().__init__(root)
        self.deletes = []

    def list_objects(self, prefix):
        raise StorageUnavailable("listing failed")

    def delete(self, key):
        self.deletes.append(key)


def test_if_storage_cannot_be_listed_nothing_is_deleted(client, scratch):
    scratch.save(photo_key(), b"x")
    spy = Spy(str(scratch.root))
    with pytest.raises(orphans.InventoryFailed):
        orphans.sweep(spy, now=later())
    assert spy.deletes == []
    assert len(scratch.list_keys("")) == 1


def test_a_listing_that_fails_part_way_is_never_used(client):
    # S3 lists 1000 keys per page: a failure on page two must not leave a partial inventory.
    import boto3

    s3_client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="y")
    s3 = S3Storage("bucket", client=s3_client, encryption=None)
    with Stubber(s3_client) as stub:
        stub.add_response("list_objects_v2", {"Contents": [{"Key": photo_key(), "LastModified": clock.now() - LATER}],
                                              "IsTruncated": True, "NextContinuationToken": "t"}, {"Bucket": "bucket", "Prefix": ""})
        stub.add_client_error("list_objects_v2", "InternalError", http_status_code=500)
        with pytest.raises(orphans.InventoryFailed):
            orphans.sweep(s3)
        stub.assert_no_pending_responses()  # no delete_object call was made


def test_if_the_database_cannot_be_read_nothing_is_deleted(client, scratch):
    scratch.save(photo_key(), b"x")

    def broken_sessions():
        raise RuntimeError("database is down")

    with pytest.raises(orphans.InventoryFailed):
        orphans.sweep(scratch, sessions=broken_sessions, now=later())
    assert len(scratch.list_keys("")) == 1


# ------------------------------------------------------------------ command line

def test_command_line_exit_codes(client, scratch, monkeypatch, capsys):
    key = photo_key()
    scratch.save(key, b"x")
    monkeypatch.setattr(orphans, "default_storage", scratch)
    moment = later()
    monkeypatch.setattr(orphans.clock, "now", lambda: moment)
    assert orphans.main(["--dry-run"]) == 0
    assert scratch.list_keys("") == [key]
    assert orphans.main(["--batch", "10"]) == 0
    assert scratch.list_keys("") == []

    monkeypatch.setattr(orphans, "default_storage", Spy(str(scratch.root)))
    assert orphans.main([]) == 2  # inventory failed: stopped without deleting

    class Stuck(LocalStorage):
        def delete(self, key):
            raise StorageUnavailable("timeout")

    scratch.save(key, b"x")
    monkeypatch.setattr(orphans, "default_storage", Stuck(str(scratch.root)))
    assert orphans.main([]) == 1  # a delete failed: retried next run
    for bad in (["--batch", "0"], ["--min-age-minutes", "0"]):
        with pytest.raises(SystemExit):
            orphans.main(bad)
