"""The ZIP export reads one consistent snapshot (plan 4.3: X1, X2, X5; X4 in each). PostgreSQL
only: SQLite does not give the REPEATABLE READ isolation these rely on.

Each test changes the database from another connection right after the export's snapshot has
opened (the export's _after_snapshot hook), the way a second request from the same user would.
"""
import time

import pytest
from sqlalchemy import delete, select, text, update

from app import export
from app.db import SessionLocal, engine
from app.models import Item, ItemPhoto, new_id
from app.storage import storage
from tests.test_export_zip import check_manifest, download, read_json, temp_files, wardrobe

pytestmark = pytest.mark.skipif(engine.dialect.name != "postgresql", reason="needs PostgreSQL")


def test_x1_a_photo_added_after_the_snapshot_is_not_exported(client, signup, monkeypatch):
    headers, item_ids, photos = wardrobe(client, signup)
    with SessionLocal() as db:
        template = db.get(ItemPhoto, photos[0]["id"])
        user_id, item_id = template.user_id, template.item_id
    late_id = new_id()

    def add_photo():
        key = f"{user_id}/{late_id}_original.jpg"
        storage.save(key, b"late")
        storage.save(f"{user_id}/{late_id}_thumb.jpg", b"late")
        with SessionLocal.begin() as db:
            db.add(ItemPhoto(id=late_id, item_id=item_id, user_id=user_id, original_key=key,
                             thumb_key=f"{user_id}/{late_id}_thumb.jpg", content_type="image/jpeg",
                             width=1, height=1, size_bytes=4))

    monkeypatch.setattr(export, "_after_snapshot", add_photo)
    response, archive = download(client, headers)
    assert response.status_code == 200
    manifest = check_manifest(archive)
    assert manifest["snapshot"]["photo_count"] == 3
    assert late_id not in archive.read("data.json").decode()
    assert not any(late_id in name for name in archive.namelist())
    # It exists, and the next export has it.
    monkeypatch.setattr(export, "_after_snapshot", lambda: None)
    _, later = download(client, headers)
    assert check_manifest(later)["snapshot"]["photo_count"] == 4


def test_x2_a_photo_deleted_after_the_snapshot_is_listed_as_missing(client, signup, monkeypatch):
    headers, _, photos = wardrobe(client, signup)
    gone = photos[0]["id"]

    def delete_photo():
        with SessionLocal.begin() as db:
            record = db.get(ItemPhoto, gone)
            keys = (record.original_key, record.thumb_key)
            db.execute(delete(ItemPhoto).where(ItemPhoto.id == gone))
        for key in keys:
            storage.delete(key)

    monkeypatch.setattr(export, "_after_snapshot", delete_photo)
    response, archive = download(client, headers)
    assert response.status_code == 200
    manifest = check_manifest(archive)
    assert gone in {p["id"] for p in read_json(archive, "data.json")["photos"]}  # in the snapshot
    assert manifest["missing_photos"] == [gone]
    assert manifest["photos_written"] == manifest["snapshot"]["photo_count"] - 1 == 2


def test_x5_an_item_renamed_after_the_snapshot_keeps_its_old_name(client, signup, monkeypatch):
    headers, item_ids, _ = wardrobe(client, signup)

    def rename():
        with SessionLocal.begin() as db:
            db.execute(update(Item).where(Item.id == item_ids[0]).values(name="Renamed shirt"))

    monkeypatch.setattr(export, "_after_snapshot", rename)
    _, archive = download(client, headers)
    check_manifest(archive)
    text = archive.read("data.json").decode()
    assert "Renamed shirt" not in text
    names = {i["id"]: i["name"] for i in read_json(archive, "data.json")["items"]}
    assert names[item_ids[0]] == "Classic white shirt"
    with SessionLocal() as db:
        assert db.scalar(select(Item.name).where(Item.id == item_ids[0])) == "Renamed shirt"


def test_x3_photos_added_after_the_snapshot_do_not_count_against_the_limit(client, signup, monkeypatch):
    # 3000 photos (the limit) when the snapshot opens; 10 more arrive while the export runs.
    from tests.test_export_limits import bulk_wardrobe

    headers = signup()
    user_id = client.get("/me", headers=headers).json()["id"]
    item_ids = bulk_wardrobe(user_id, items=500, photos=3000, photo_bytes=1024)

    def ten_more():
        with SessionLocal.begin() as db:
            for _ in range(10):
                photo_id = new_id()
                db.add(ItemPhoto(id=photo_id, item_id=item_ids[0], user_id=user_id,
                                 original_key=f"{user_id}/{photo_id}_original.jpg",
                                 thumb_key=f"{user_id}/{photo_id}_thumb.jpg", content_type="image/jpeg",
                                 width=1, height=1, size_bytes=1024))

    monkeypatch.setattr(export, "_after_snapshot", ten_more)
    response, archive = download(client, headers)
    assert response.status_code == 200
    manifest = check_manifest(archive)
    assert manifest["snapshot"]["photo_count"] == 3000
    assert manifest["photos_written"] == 3000
    assert len(read_json(archive, "data.json")["photos"]) == 3000


def test_a_slow_query_late_in_the_build_is_stopped_at_the_deadline(client, signup, monkeypatch):
    # Review of PR #48: statement_timeout follows the time left, so a query that starts after
    # part of the time is gone cannot run past the deadline.
    headers, _, _ = wardrobe(client, signup)
    user_id = client.get("/me", headers=headers).json()["id"]
    real = export.write_export_json

    def slow(db, user, write):
        time.sleep(1.5)  # most of the time is gone
        db.execute(text("SELECT pg_sleep(10)"))  # then a statement that would run long
        real(db, user, write)

    monkeypatch.setattr(export, "write_export_json", slow)
    started = time.monotonic()
    with pytest.raises(export.ExportTimeout):
        export.build_zip(user_id, deadline=started + 2.0)
    elapsed = time.monotonic() - started
    # Stopped at the deadline (about 2 s), not 2 s after the query began (3.5 s).
    assert elapsed < 2.6, elapsed
    assert temp_files() == []
    assert export.active_files() == set()
