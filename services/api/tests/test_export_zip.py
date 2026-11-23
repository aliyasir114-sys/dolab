"""GET /me/export.zip (plan 4.3, step C part a): E1, E4, E7, E10, and X4 on every export.
The snapshot tests X1, X2 and X5 need PostgreSQL: test_pg_export.py."""
import io
import json
import os
import stat
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select

from app import export
from app.config import settings
from app.db import SessionLocal
from app.models import ItemPhoto
from app.storage import StorageUnavailable, storage
from tests.test_photos import make_image, new_item, upload

SHIRT = {"name": "Classic white shirt", "category": "shirt", "primary_color": "white"}


def temp_files() -> list[str]:
    folder = Path(settings.export_tmp_dir)
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def download(client, headers):
    response = client.get("/me/export.zip", headers=headers)
    archive = zipfile.ZipFile(io.BytesIO(response.content)) if response.status_code == 200 else None
    return response, archive


def read_json(archive: zipfile.ZipFile, name: str) -> dict:
    return json.loads(archive.read(name))


def check_manifest(archive: zipfile.ZipFile) -> dict:
    """X4 on any successful export: the manifest's snapshot counts equal what data.json lists,
    and every listed file is in the archive with its size."""
    data, manifest = read_json(archive, "data.json"), read_json(archive, "manifest.json")
    assert manifest["format"] == "dolab-export" and manifest["version"] == 1
    assert manifest["snapshot"] == {"photo_count": len(data["photos"]),
                                    "photo_bytes": sum(p["size_bytes"] for p in data["photos"])}
    assert manifest["photos_written"] == len(data["photos"]) - len(manifest["missing_photos"])
    names = set(archive.namelist())
    assert names == {f["path"] for f in manifest["files"]} | {"manifest.json"}
    for entry in manifest["files"]:
        assert archive.getinfo(entry["path"]).file_size == entry["bytes"]
    return manifest


def wardrobe(client, signup, email="owner@example.com"):
    headers = signup(email)
    first = client.post("/items", json=SHIRT, headers=headers).json()["id"]
    second = new_item(client, headers)
    photos = [upload(client, headers, first).json(), upload(client, headers, first).json(),
              upload(client, headers, second, data=make_image("PNG"), name="p.png").json()]
    client.post(f"/items/{first}/wear", headers=headers)
    client.put("/me/profile", json={"height_cm": 180}, headers=headers)
    return headers, [first, second], photos


def test_e1_the_export_holds_the_data_and_every_photo(client, signup):
    headers, item_ids, photos = wardrobe(client, signup)
    response, archive = download(client, headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"].startswith('attachment; filename="dolab-export-')
    assert response.headers["cache-control"] == "no-store"
    assert archive.testzip() is None
    # data.json is the JSON export.
    assert read_json(archive, "data.json") == client.get("/me/export", headers=headers).json()
    manifest = check_manifest(archive)
    assert manifest["missing_photos"] == []
    assert manifest["photos_written"] == 3
    # Each photo is its stored, cleaned original, byte for byte, under its item.
    with SessionLocal() as db:
        for photo in photos:
            record = db.get(ItemPhoto, photo["id"])
            suffix = Path(record.original_key).suffix
            content = archive.read(f"photos/{record.item_id}/{record.id}{suffix}")
            assert content == storage.read(record.original_key)
            assert len(content) == record.size_bytes
    assert any(name.endswith(".png") for name in archive.namelist())
    # Nothing is left in the temporary folder once the file has been sent.
    assert temp_files() == []


def test_an_empty_wardrobe_exports_too(client, signup):
    headers = signup()
    response, archive = download(client, headers)
    assert response.status_code == 200
    assert sorted(archive.namelist()) == ["data.json", "manifest.json"]
    assert check_manifest(archive)["snapshot"] == {"photo_count": 0, "photo_bytes": 0}


def test_the_json_export_is_unchanged_and_both_need_a_sign_in(client, signup):
    headers = signup()
    assert set(client.get("/me/export", headers=headers).json()) == {
        "user", "profile", "items", "wear_log", "status_history", "donations", "photos"}
    assert client.get("/me/export.zip").status_code == 401
    assert client.get("/me/export").status_code == 401


def test_e4_a_storage_failure_fails_the_export_and_leaves_no_file(client, signup, monkeypatch):
    headers, _, _ = wardrobe(client, signup)
    real = storage.read_chunks
    calls = {"n": 0}

    def third_fails(key, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise StorageUnavailable("down")
        return real(key, *args, **kwargs)

    monkeypatch.setattr(storage, "read_chunks", third_fails)
    response = client.get("/me/export.zip", headers=headers)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"
    assert temp_files() == []


def test_e4_a_failure_in_the_middle_of_a_photo_also_leaves_no_file(client, signup, monkeypatch):
    headers, _, _ = wardrobe(client, signup)
    real = storage.read_chunks

    def breaks_mid_stream(key, *args, **kwargs):
        blocks = real(key, 16)

        def stream():
            yield next(blocks)
            raise StorageUnavailable("connection reset")

        return stream()

    monkeypatch.setattr(storage, "read_chunks", breaks_mid_stream)
    assert client.get("/me/export.zip", headers=headers).status_code == 503
    assert temp_files() == []


def test_e7_a_photo_whose_file_is_gone_is_listed_not_fatal(client, signup):
    headers, _, photos = wardrobe(client, signup)
    gone = photos[1]["id"]
    with SessionLocal() as db:
        storage.delete(db.get(ItemPhoto, gone).original_key)  # deleted between data and files
    response, archive = download(client, headers)
    assert response.status_code == 200
    manifest = check_manifest(archive)
    assert manifest["missing_photos"] == [gone]
    assert manifest["photos_written"] == 2
    assert gone in {p["id"] for p in read_json(archive, "data.json")["photos"]}
    assert not any(gone in name for name in archive.namelist())


def test_e10_an_export_holds_only_its_owners_data(client, signup):
    mine, my_items, my_photos = wardrobe(client, signup, "me@example.com")
    other, other_items, other_photos = wardrobe(client, signup, "other@example.com")
    _, archive = download(client, mine)
    everything = " ".join(archive.namelist()) + archive.read("data.json").decode() + archive.read("manifest.json").decode()
    for foreign in other_items + [p["id"] for p in other_photos] + ["other@example.com"]:
        assert foreign not in everything
    for own in my_items + [p["id"] for p in my_photos]:
        assert own in everything


def test_the_temporary_file_is_private_and_removed_on_failure(client, signup, monkeypatch):
    headers, _, _ = wardrobe(client, signup)
    user_id = client.get("/me", headers=headers).json()["id"]
    path = export.build_zip(user_id)
    try:
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert zipfile.ZipFile(path).testzip() is None
    finally:
        path.unlink()

    def boom():
        raise RuntimeError("database went away")

    monkeypatch.setattr(export, "_after_snapshot", boom)
    with pytest.raises(RuntimeError):
        export.build_zip(user_id)
    assert temp_files() == []


def test_photos_are_read_in_blocks_not_whole(client, signup, monkeypatch):
    headers, _, _ = wardrobe(client, signup)
    sizes: list[int] = []
    real = storage.read_chunks

    def recording(key, chunk_size=None):
        sizes.append(chunk_size)
        return real(key) if chunk_size is None else real(key, chunk_size)

    monkeypatch.setattr(storage, "read_chunks", recording)
    monkeypatch.setattr(storage, "read", lambda key: pytest.fail("whole-file read during export"))
    assert client.get("/me/export.zip", headers=headers).status_code == 200
    assert len(sizes) == 3


def test_read_chunks_on_the_configured_storage(client):
    data = bytes(range(256)) * 50
    storage.save("chunks-test/a_thumb.jpg", data)
    try:
        assert b"".join(storage.read_chunks("chunks-test/a_thumb.jpg", 1000)) == data
        assert [len(b) for b in storage.read_chunks("chunks-test/a_thumb.jpg", 5000)][:2] == [5000, 5000]
        assert storage.read_chunks("chunks-test/missing_thumb.jpg") is None
    finally:
        storage.delete("chunks-test/a_thumb.jpg")
