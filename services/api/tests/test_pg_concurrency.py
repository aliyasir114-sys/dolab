"""Parallel requests on PostgreSQL (plan section 5; deferred from step A to step B).

SQLite serialises all writers, so these only mean something on PostgreSQL, where the item row
lock (SELECT ... FOR UPDATE) is what keeps two requests from both passing a check. Each test
widens the race window with a short sleep inside the critical section. The control tests switch
the lock off and expect the race to show, which proves the tests can catch a missing lock.
"""
import threading
import time
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app import clock
from app.db import SessionLocal, engine
from app.models import Item, ItemPhoto, WearLog
from app.routers import items as items_router
from app.routers import photos as photos_router
from tests.test_photos import make_image

pytestmark = pytest.mark.skipif(engine.dialect.name != "postgresql", reason="needs PostgreSQL")

SHIRT = {"name": "Classic white shirt", "category": "shirt"}
PAUSE = 0.3


def parallel(n: int, call) -> list:
    """Run call(i) in n threads released together; return the results in order."""
    barrier = threading.Barrier(n)
    results: list = [None] * n

    def run(i: int) -> None:
        barrier.wait()
        results[i] = call(i)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results


def slow(monkeypatch, module, name: str) -> None:
    """Make module.name pause after doing its work, inside the request's transaction."""
    original = getattr(module, name)

    def wrapper(*args, **kwargs):
        value = original(*args, **kwargs)
        time.sleep(PAUSE)
        return value

    monkeypatch.setattr(module, name, wrapper)


def without_item_lock(monkeypatch) -> None:
    original = items_router._own_item

    def unlocked(db, user_id, item_id, *, lock=False):
        return original(db, user_id, item_id, lock=False)

    monkeypatch.setattr(items_router, "_own_item", unlocked)
    monkeypatch.setattr(photos_router, "_own_item", unlocked)


def count(model, item_id: str) -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(model).where(model.item_id == item_id))


def wear_count(item_id: str) -> int:
    with SessionLocal() as db:
        return db.get(Item, item_id).wear_count


# ---------------------------------------------------------------- the lock itself

def test_item_lock_blocks_a_second_writer(client, signup):
    headers = signup()
    item_id = client.post("/items", json=SHIRT, headers=headers).json()["id"]
    first, second = SessionLocal(), SessionLocal()
    try:
        assert first.get(Item, item_id, with_for_update=True) is not None
        second.execute(select(1))
        second.connection().exec_driver_sql("SET LOCAL lock_timeout = '300ms'")
        with pytest.raises(OperationalError, match="lock"):
            second.get(Item, item_id, with_for_update=True)
    finally:
        second.rollback()
        first.rollback()
        second.close()
        first.close()


# ---------------------------------------------------------------- wear

def test_parallel_wears_on_one_day_count_once(client, signup, monkeypatch):
    headers = signup()
    item_id = client.post("/items", json=SHIRT, headers=headers).json()["id"]
    slow(monkeypatch, items_router, "_already_worn")
    responses = parallel(6, lambda _: client.post(f"/items/{item_id}/wear", headers=headers))
    assert [r.status_code for r in responses] == [200] * 6
    assert count(WearLog, item_id) == 1
    assert wear_count(item_id) == 1


def _wear_on_six_days(client, headers, item_id):
    today = clock.today_in("UTC")
    return parallel(6, lambda i: client.post(
        f"/items/{item_id}/wear", json={"worn_on": (today - timedelta(days=i)).isoformat()}, headers=headers
    ))


def test_parallel_wears_on_different_days_lose_no_count(client, signup, monkeypatch):
    headers = signup()
    item_id = client.post("/items", json=SHIRT, headers=headers).json()["id"]
    slow(monkeypatch, items_router, "_already_worn")
    responses = _wear_on_six_days(client, headers, item_id)
    assert [r.status_code for r in responses] == [200] * 6
    assert count(WearLog, item_id) == 6
    assert wear_count(item_id) == 6


def test_control_without_the_lock_wear_counts_are_lost(client, signup, monkeypatch):
    headers = signup()
    item_id = client.post("/items", json=SHIRT, headers=headers).json()["id"]
    slow(monkeypatch, items_router, "_already_worn")
    without_item_lock(monkeypatch)
    _wear_on_six_days(client, headers, item_id)
    assert count(WearLog, item_id) == 6
    assert wear_count(item_id) < 6  # lost updates: the lock is what prevents this


# ---------------------------------------------------------------- photos

def _fill_and_race(client, headers, monkeypatch):
    item_id = client.post("/items", json=SHIRT, headers=headers).json()["id"]
    small = make_image(size=(40, 40))
    for _ in range(photos_router.MAX_PHOTOS_PER_ITEM - 2):
        files = {"file": ("p.jpg", small, "image/jpeg")}
        assert client.post(f"/items/{item_id}/photos", files=files, headers=headers).status_code == 201
    slow(monkeypatch, photos_router, "process_image")
    responses = parallel(6, lambda _: client.post(
        f"/items/{item_id}/photos", files={"file": ("p.jpg", small, "image/jpeg")}, headers=headers
    ))
    return item_id, sorted(r.status_code for r in responses)


def test_parallel_uploads_cannot_pass_the_photo_limit(client, signup, monkeypatch):
    item_id, codes = _fill_and_race(client, signup(), monkeypatch)
    assert codes == [201, 201, 409, 409, 409, 409]
    assert count(ItemPhoto, item_id) == photos_router.MAX_PHOTOS_PER_ITEM


def test_control_without_the_lock_uploads_pass_the_limit(client, signup, monkeypatch):
    without_item_lock(monkeypatch)
    item_id, codes = _fill_and_race(client, signup(), monkeypatch)
    assert count(ItemPhoto, item_id) > photos_router.MAX_PHOTOS_PER_ITEM  # the lock prevents this
