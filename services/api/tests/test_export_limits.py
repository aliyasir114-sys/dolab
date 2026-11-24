"""ZIP export, step C part (b) (plan 4.3): limits checked from the snapshot before any photo is
read (E2, E14), a deadline that really stops the build (E5), hourly and concurrent limits (E8,
E9), the clean-up of temporary files (E6), and the 16 MB memory budget measured with
tracemalloc (E3, E11, E12, E13, E15, E16). X3 is in test_pg_export.py."""
import gc
import io
import os
import threading
import time
import tracemalloc
import uuid
import warnings
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert

from app import export
from app.config import settings
from app.db import SessionLocal
from app.models import Item, ItemPhoto
from app.storage import StorageUnavailable, storage
from tests.test_export_zip import check_manifest, download, read_json, temp_files, wardrobe

MB = 1024 * 1024


@pytest.fixture
def setting():
    """Change a setting for one test (the settings object is frozen)."""
    originals = {}

    def change(name, value):
        originals.setdefault(name, getattr(settings, name))
        object.__setattr__(settings, name, value)

    yield change
    for name, value in originals.items():
        object.__setattr__(settings, name, value)


def user_id_of(client, headers) -> str:
    return client.get("/me", headers=headers).json()["id"]


def bulk_wardrobe(user_id: str, *, items: int, photos: int, photo_bytes: int, write_files: bool = True,
                  long_names: bool = False) -> list[str]:
    """Rows written straight into the database, files straight into storage: thousands at once."""
    item_ids = [str(uuid.uuid4()) for _ in range(items)]
    name = "x" * 120 if long_names else "item"
    with SessionLocal.begin() as db:
        for start in range(0, items, 2000):
            db.execute(insert(Item), [{"id": i, "user_id": user_id, "name": name, "category": "shirt"}
                                      for i in item_ids[start : start + 2000]])
    photo_rows = []
    for n in range(photos):
        photo_id = str(uuid.uuid4())
        key = f"{user_id}/{photo_id}_original.jpg"
        if write_files:
            # Different bytes for each file, so nothing can be shared in memory.
            storage.save(key, (photo_id.encode() * (photo_bytes // 36 + 1))[:photo_bytes])
        photo_rows.append({"id": photo_id, "item_id": item_ids[n % items], "user_id": user_id,
                           "original_key": key, "thumb_key": f"{user_id}/{photo_id}_thumb.jpg",
                           "content_type": "image/jpeg", "width": 1, "height": 1, "size_bytes": photo_bytes})
    with SessionLocal.begin() as db:
        for start in range(0, len(photo_rows), 2000):
            db.execute(insert(ItemPhoto), photo_rows[start : start + 2000])
    return item_ids


def measure(*builds):
    """Run the builds (at once, if several) under tracemalloc; return (peak bytes, results)."""
    gc.collect()
    tracemalloc.start()
    try:
        results: list = [None] * len(builds)

        def run(n, build):
            try:
                results[n] = build()
            except BaseException as error:  # reported to the test
                results[n] = error

        threads = [threading.Thread(target=run, args=(n, b)) for n, b in enumerate(builds)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return tracemalloc.get_traced_memory()[1], results
    finally:
        tracemalloc.stop()


class CountingStorage:
    """The configured storage, counting reads."""

    def __init__(self, inner=storage):
        self.inner, self.reads = inner, 0

    def read_chunks(self, key, *args):
        self.reads += 1
        return self.inner.read_chunks(key, *args)


# ------------------------------------------------------------------ limits before any read

def test_e2_too_many_bytes_is_413_with_nothing_read_and_no_file(client, signup, setting, monkeypatch):
    headers, _, _ = wardrobe(client, signup)
    setting("export_max_bytes", 1024)  # the plan's 1 MB, scaled to the test photos
    monkeypatch.setattr(storage, "read_chunks", lambda *a, **k: pytest.fail("a photo was read"))
    response = client.get("/me/export.zip", headers=headers)
    assert response.status_code == 413
    assert "too large to export" in response.json()["detail"]
    assert temp_files() == []


def test_e14_3001_photos_is_413_before_any_storage_read(client, signup):
    headers = signup()
    user_id = user_id_of(client, headers)
    bulk_wardrobe(user_id, items=10, photos=3001, photo_bytes=1024, write_files=False)
    counting = CountingStorage()
    with pytest.raises(export.ExportTooLarge, match="3000 photos"):
        export.build_zip(user_id, counting)
    assert counting.reads == 0
    assert temp_files() == []
    assert client.get("/me/export.zip", headers=headers).status_code == 413


def test_data_json_over_its_limit_is_413_before_any_photo(client, signup, setting):
    headers, _, _ = wardrobe(client, signup)
    setting("export_max_data_bytes", 2000)
    counting = CountingStorage()
    with pytest.raises(export.ExportTooLarge, match="data larger"):
        export.build_zip(user_id_of(client, headers), counting)
    assert counting.reads == 0
    assert temp_files() == []


# ------------------------------------------------------------------ time

def test_e5_no_time_left_is_503_and_no_file(client, signup, setting):
    headers, _, _ = wardrobe(client, signup)
    setting("export_build_seconds", 0)
    response = client.get("/me/export.zip", headers=headers)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "60"
    assert temp_files() == []


def test_e5_the_deadline_stops_a_build_part_way(client, signup, monkeypatch):
    # The build runs in the caller's thread: past the deadline it stops reading at once.
    headers, _, _ = wardrobe(client, signup)
    user_id = user_id_of(client, headers)
    clock = {"now": 1000.0}
    monkeypatch.setattr(export.time, "monotonic", lambda: clock["now"])
    reads = []

    class Slow:
        """Each photo takes 10 (fake) seconds to arrive."""

        def read_chunks(self, key, *args):
            reads.append(key)
            clock["now"] += 10
            return storage.read_chunks(key, *args)

    with pytest.raises(export.ExportTimeout):
        export.build_zip(user_id, Slow(), deadline=1015.0)
    assert len(reads) == 2  # the third photo was never asked for
    assert temp_files() == []
    assert export.active_files() == set()


# ------------------------------------------------------------------ how often, how many at once

def test_e8_a_fourth_export_in_an_hour_is_429_and_failures_do_not_count(client, signup, setting):
    headers, _, _ = wardrobe(client, signup)
    setting("export_build_seconds", 0)
    assert client.get("/me/export.zip", headers=headers).status_code == 503  # failed: not counted
    setting("export_build_seconds", 120)
    for _ in range(3):
        assert client.get("/me/export.zip", headers=headers).status_code == 200
    refused = client.get("/me/export.zip", headers=headers)
    assert refused.status_code == 429
    assert 3500 < int(refused.headers["Retry-After"]) <= 3600
    assert temp_files() == []


def test_e9_a_third_export_at_once_is_503(client, signup):
    headers, _, _ = wardrobe(client, signup)
    taken = [export.reserve() for _ in range(settings.export_concurrency)]
    try:
        refused = client.get("/me/export.zip", headers=headers)
        assert refused.status_code == 503
        assert refused.headers["Retry-After"] == "30"
        with pytest.raises(export.ExportBusy):
            export.reserve()
    finally:
        for place in taken:
            export.release_file(place)
    assert client.get("/me/export.zip", headers=headers).status_code == 200  # the place was given back
    assert export.active_files() == set()


def test_a_place_is_held_until_the_file_has_been_sent(client, signup):
    # Review of PR #48: two exports still downloading keep a third out; when one finishes
    # sending, its place comes back.
    import asyncio

    headers, _, _ = wardrobe(client, signup)
    user_id = user_id_of(client, headers)
    responses = []
    for _ in range(2):
        place = export.reserve()
        responses.append(export.TemporaryFileResponse(export.build_zip(user_id, path=place),
                                                      media_type="application/zip"))
    started = [threading.Event(), threading.Event()]
    finish = [threading.Event(), threading.Event()]
    received = [bytearray(), bytearray()]
    scope = {"type": "http", "method": "GET", "headers": [], "path": "/", "query_string": b""}

    def serve(n):
        async def receive():
            await asyncio.sleep(3600)  # the client never leaves on its own
            return {"type": "http.disconnect"}

        async def send(message):
            started[n].set()
            received[n].extend(message.get("body", b""))
            # A slow client: nothing more is accepted until the test lets it finish.
            await asyncio.get_running_loop().run_in_executor(None, finish[n].wait)

        asyncio.run(responses[n](scope, receive, send))

    threads = [threading.Thread(target=serve, args=(n,)) for n in range(2)]
    for thread in threads:
        thread.start()
    try:
        assert all(s.wait(10) for s in started)
        # Both builds are done; both files are still being sent.
        assert len(export.active_files()) == 2
        assert client.get("/me/export.zip", headers=headers).status_code == 503
        finish[0].set()
        threads[0].join(10)
        assert len(export.active_files()) == 1  # the first download ended: its place is back
        assert zipfile.ZipFile(io.BytesIO(bytes(received[0]))).testzip() is None
        assert client.get("/me/export.zip", headers=headers).status_code == 200
    finally:
        for event in finish:
            event.set()
        for thread in threads:
            thread.join(10)
    assert export.active_files() == set()
    assert temp_files() == []


# ------------------------------------------------------------------ temporary files

def make_temp(name: str, age: timedelta) -> Path:
    folder = Path(settings.export_tmp_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"x")
    then = time.time() - age.total_seconds()
    os.utime(path, (then, then))
    return path


def test_e6_the_clean_up_removes_old_files_and_keeps_new_and_active_ones(client):
    old = make_temp(f"export-{uuid.uuid4()}.zip", timedelta(hours=2))
    new = make_temp(f"export-{uuid.uuid4()}.zip", timedelta(minutes=5))
    busy = make_temp(f"export-{uuid.uuid4()}.zip", timedelta(hours=2))
    other = make_temp("not-an-export.txt", timedelta(hours=2))
    try:
        with export._active_lock:
            export._active[str(busy)] = time.monotonic()  # still being built or sent here
        assert export.clean_temporary(export.PERIODIC_AGE) == [old.name]
        assert new.exists() and busy.exists() and other.exists()
        # At start-up anything untouched for 10 minutes goes, still never an active file.
        stale = make_temp(f"export-{uuid.uuid4()}.zip", timedelta(minutes=11))
        assert export.clean_temporary(export.STARTUP_AGE) == [stale.name]
        assert new.exists() and busy.exists()
    finally:
        export.release_file(busy)
        new.unlink()
        other.unlink()


def test_the_clean_up_runs_at_start_up(client):
    from fastapi.testclient import TestClient

    from app.main import app

    old = make_temp(f"export-{uuid.uuid4()}.zip", timedelta(minutes=30))
    with TestClient(app):
        assert not old.exists()


def test_a_file_is_deleted_even_if_the_download_is_cut_off(client, signup):
    import asyncio

    headers, _, _ = wardrobe(client, signup)
    path = export.build_zip(user_id_of(client, headers))
    response = export.TemporaryFileResponse(path, media_type="application/zip")

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        raise OSError("client went away")

    scope = {"type": "http", "method": "GET", "headers": [], "path": "/", "query_string": b""}
    with pytest.raises(Exception):  # OSError, or the framework's own "client disconnected"
        asyncio.run(response(scope, receive, send))
    assert not path.exists()
    assert str(path) not in export.active_files()


# ------------------------------------------------------------------ memory (16 MB per export)

def report(test: str, peak: int, extra: str = "") -> None:
    # Shown in the warnings summary of the CI log: the measured figure, not just a pass.
    warnings.warn(f"{test}: tracemalloc peak {peak / MB:.2f} MB {extra}".strip(), stacklevel=2)


def test_e3_50_mb_of_photos_in_under_16_mb(client, signup):
    headers = signup()
    user_id = user_id_of(client, headers)
    bulk_wardrobe(user_id, items=10, photos=50, photo_bytes=MB)
    peak, (path,) = measure(lambda: export.build_zip(user_id))
    try:
        assert isinstance(path, Path), path
        assert zipfile.ZipFile(path).testzip() is None
        assert check_manifest(zipfile.ZipFile(path))["snapshot"]["photo_bytes"] == 50 * MB
    finally:
        export.release_file(path)
    report("E3", peak)
    assert peak < 16 * MB


def test_e11_two_exports_at_once_of_50_mb_each_in_under_32_mb(client, signup):
    users = [user_id_of(client, signup(f"user{n}@example.com")) for n in range(2)]
    for user_id in users:
        bulk_wardrobe(user_id, items=10, photos=50, photo_bytes=MB)
    peak, paths = measure(*(lambda u=u: export.build_zip(u) for u in users))
    try:
        assert all(isinstance(p, Path) for p in paths), paths
        assert paths[0] != paths[1]
        for user_id, path in zip(users, paths):
            archive = zipfile.ZipFile(path)
            assert archive.testzip() is None
            assert read_json(archive, "data.json")["user"]["id"] == user_id
            other = users[1 - users.index(user_id)]
            assert other not in archive.read("data.json").decode()
    finally:
        for path in paths:
            export.release_file(path)
    assert temp_files() == []
    report("E11", peak)
    assert peak < 32 * MB


def test_e12_one_of_two_exports_failing_leaves_the_other_intact(client, signup):
    users = [user_id_of(client, signup(f"user{n}@example.com")) for n in range(2)]
    for user_id in users:
        bulk_wardrobe(user_id, items=5, photos=20, photo_bytes=64 * 1024)

    class FailsHalfWay:
        def __init__(self):
            self.reads = 0

        def read_chunks(self, key, *args):
            self.reads += 1
            if self.reads == 10:
                raise StorageUnavailable("connection reset")
            return storage.read_chunks(key, *args)

    _, (good, bad) = measure(lambda: export.build_zip(users[0]), lambda: export.build_zip(users[1], FailsHalfWay()))
    try:
        assert isinstance(good, Path) and isinstance(bad, StorageUnavailable)
        assert zipfile.ZipFile(good).testzip() is None
        assert temp_files() == [good.name]  # only the good one is left, untouched
    finally:
        export.release_file(good)


def test_e13_3000_small_photos_in_under_16_mb(client, signup):
    headers = signup()
    user_id = user_id_of(client, headers)
    bulk_wardrobe(user_id, items=500, photos=3000, photo_bytes=1024)
    peak, (path,) = measure(lambda: export.build_zip(user_id))
    try:
        archive = zipfile.ZipFile(path)
        assert len(archive.namelist()) == 3002
        assert archive.testzip() is None
        # The ZIP index this many entries needs, measured on its own.
        gc.collect()
        tracemalloc.start()
        index = zipfile.ZipFile(path).filelist
        index_bytes = tracemalloc.get_traced_memory()[0]
        tracemalloc.stop()
        assert len(index) == 3002
    finally:
        export.release_file(path)
    report("E13", peak, f"(ZIP index of 3002 entries: {index_bytes / MB:.2f} MB)")
    assert peak < 16 * MB


def test_e15_3000_photos_and_data_json_near_8_mb_in_under_16_mb(client, signup):
    headers = signup()
    user_id = user_id_of(client, headers)
    bulk_wardrobe(user_id, items=13_000, photos=3000, photo_bytes=1024, long_names=True)
    peak, (path,) = measure(lambda: export.build_zip(user_id))
    try:
        assert isinstance(path, Path), path
        data_size = zipfile.ZipFile(path).getinfo("data.json").file_size
        assert 6.5 * MB < data_size <= 8 * MB, data_size
    finally:
        export.release_file(path)
    report("E15", peak, f"(data.json {data_size / MB:.2f} MB)")
    assert peak < 16 * MB


def test_e16_two_exports_of_3000_photos_at_once_in_under_32_mb(client, signup):
    users = [user_id_of(client, signup(f"user{n}@example.com")) for n in range(2)]
    for user_id in users:
        bulk_wardrobe(user_id, items=500, photos=3000, photo_bytes=1024)
    peak, paths = measure(*(lambda u=u: export.build_zip(u) for u in users))
    try:
        assert all(isinstance(p, Path) for p in paths), paths
        assert all(len(zipfile.ZipFile(p).namelist()) == 3002 for p in paths)
    finally:
        for path in paths:
            export.release_file(path)
    report("E16", peak)
    assert peak < 32 * MB
