"""Deletions with a ledger entry first (plan 4.10.1 and 4.10.1د, as decided by Ali 2026-11-25).

Order, for deleting an item, a photo or an account:
1. Record the intent (deletion_intents), committed on its own.
2. Write the signed ledger entry and wait for the confirmation (three tries, two seconds).
3. If that fails: remove the intent (one try) and answer 503. Nothing is deleted, even if
   removing the intent fails too (an intent without an entry is never acted on).
4. If it worked: delete, and remove the intent, in one transaction, and commit.
5. The caller then deletes the photo files.

So every committed deletion, in any copy of the database, has an entry in the ledger written
before it. If step 4 fails after the entry was written, the entry and the intent both remain:
the reconciliation job (next part of step C) completes that deletion.

One entry per request: deleting an account is one "user" entry, however many photos it had.

Step 4 runs the one deletion logic (plan 4.10.1د; Ali's decision 2026-11-26): on PostgreSQL the
function dolab.apply_deletion, which the reconciliation function reconcile_apply also uses; on
SQLite (development and tests only, no PL/pgSQL) the same statements in Python below. A test on
PostgreSQL runs both on the same data and compares every affected table and the file keys
(tests/test_pg_deleter_functions.py), so they cannot drift apart.

Step 4 also queues the photo files in pending_file_deletions, in the same transaction (plan
4.10.3, notice #59). Step 5 then deletes them from storage, only after the commit, and removes
their rows; a file it could not delete keeps its row for the queue job (app.jobs.file_deletions).

Not covered yet: DELETE /me/profile (clearing body measurements) writes no entry, because format
v1 knows only item, photo and user. Until "profile" is added in a format v2 (a separate request,
Ali's decision 2026-11-25), a restored backup can bring cleared measurements back; the deletion
guarantee is not complete before then.

"""
import logging
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import delete, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from . import clock, ledger, ledger_store
from .config import settings
from .db import SessionLocal, is_sqlite
from .models import (
    DeletionIntent, Donation, DonationItem, Item, ItemPhoto, ItemStatusChange, PasswordResetToken,
    PendingFileDeletion, Profile, RefreshToken, User, WearLog,
)

log = logging.getLogger("dolab.deletion")

signing_key = ledger.private_key_from_seed(settings.ledger_signing_key)


def _record_intent(entry: ledger.Entry) -> None:
    with SessionLocal.begin() as db:
        db.add(DeletionIntent(entry_id=entry.entry_id, entity_type=entry.entity_type, entity_id=entry.entity_id,
                              user_id=entry.user_id, created_at=clock.now()))


def _remove_intent(entry_id: str) -> None:
    with SessionLocal.begin() as db:
        db.execute(delete(DeletionIntent).where(DeletionIntent.entry_id == entry_id))


class NotFound(LookupError):
    """The entity does not exist for this user (the function raises no_data_found)."""


def _keys(photos) -> list[str]:
    return [key for p in photos for key in (p.original_key, p.thumb_key)]


def python_deletion(db: Session, entity_type: str, entity_id: str, user_id: str) -> list[str]:
    """The deletion statements of dolab.apply_deletion, for SQLite. Not committed. Returns the
    photo file keys to delete, each once, and queues them in pending_file_deletions in the same
    transaction (one INSERT; a key already queued is left as it is). Keep in step with
    migration 0007 (the parity test checks it)."""
    keys = list(dict.fromkeys(_delete_rows(db, entity_type, entity_id, user_id)))
    if keys:
        _queue_files(db, keys)
    return keys


# A newly queued file is first due for the queue job this long after the deletion, so the job
# rarely takes it while the server's immediate attempt is still working on it (migration 0007).
# Rarely, not never: an immediate attempt slowed by storage timeouts can still be running when
# the row comes due. The overlap is then a second delete of the same key, harmless because a
# queued key is never reused; and the server's removal of the row waits at most its lock timeout.
FIRST_TRY_DELAY = timedelta(minutes=5)


def _queue_files(db: Session, keys: list[str]) -> None:
    """SQLite's INSERT ... ON CONFLICT DO NOTHING, one statement for all keys."""
    now = clock.now()
    due = now + FIRST_TRY_DELAY
    db.execute(sqlite_insert(PendingFileDeletion)
               .values([{"key": k, "attempts": 0, "next_try_at": due, "created_at": now} for k in keys])
               .on_conflict_do_nothing())


def _delete_rows(db: Session, entity_type: str, entity_id: str, user_id: str) -> list[str]:
    if entity_type == "item":
        if db.scalar(select(Item.id).where(Item.id == entity_id, Item.user_id == user_id)) is None:
            raise NotFound("no such item for this user")
        keys = _keys(db.execute(select(ItemPhoto.original_key, ItemPhoto.thumb_key)
                                .where(ItemPhoto.item_id == entity_id)).all())
        db.execute(delete(WearLog).where(WearLog.item_id == entity_id))
        db.execute(delete(ItemStatusChange).where(ItemStatusChange.item_id == entity_id))
        db.execute(delete(DonationItem).where(DonationItem.item_id == entity_id))
        # Explicit statements in dependency order: the models declare no relationships, so the ORM
        # would not know the photos must go before the item (PostgreSQL enforces the foreign key).
        db.execute(delete(ItemPhoto).where(ItemPhoto.item_id == entity_id))
        db.execute(delete(Item).where(Item.id == entity_id))
        return keys
    if entity_type == "photo":
        rows = db.execute(select(ItemPhoto.original_key, ItemPhoto.thumb_key)
                          .where(ItemPhoto.id == entity_id, ItemPhoto.user_id == user_id)).all()
        if not rows:
            raise NotFound("no such photo for this user")
        db.execute(delete(ItemPhoto).where(ItemPhoto.id == entity_id))
        return _keys(rows)
    if entity_type == "user":
        if entity_id != user_id or db.scalar(select(User.id).where(User.id == user_id)) is None:
            raise NotFound("no such account")
        keys = _keys(db.execute(select(ItemPhoto.original_key, ItemPhoto.thumb_key)
                                .where(ItemPhoto.user_id == user_id)).all())
        db.execute(delete(ItemPhoto).where(ItemPhoto.user_id == user_id))
        donation_ids = select(Donation.id).where(Donation.user_id == user_id)
        db.execute(delete(DonationItem).where(DonationItem.donation_id.in_(donation_ids)))
        db.execute(delete(Donation).where(Donation.user_id == user_id))
        db.execute(delete(WearLog).where(WearLog.user_id == user_id))
        db.execute(delete(ItemStatusChange).where(ItemStatusChange.user_id == user_id))
        db.execute(delete(Item).where(Item.user_id == user_id))
        db.execute(delete(Profile).where(Profile.user_id == user_id))
        db.execute(delete(RefreshToken).where(RefreshToken.user_id == user_id))
        db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user_id))
        db.execute(delete(User).where(User.id == user_id))
        return keys
    raise ValueError("unknown entity type")


def function_deletion(db: Session, entity_type: str, entity_id: str, user_id: str) -> list[str]:
    """dolab.apply_deletion on PostgreSQL. Not committed. Returns the photo file keys."""
    db.flush()
    return list(db.scalars(text("SELECT dolab.apply_deletion(:entity_type, CAST(:entity_id AS uuid), "
                                "CAST(:user_id AS uuid))"),
                           {"entity_type": entity_type, "entity_id": entity_id, "user_id": user_id}))


def apply_deletion(db: Session, entity_type: str, entity_id: str, user_id: str) -> list[str]:
    """The one deletion logic: the PostgreSQL function, or its Python twin on SQLite."""
    if is_sqlite:
        return python_deletion(db, entity_type, entity_id, user_id)
    return function_deletion(db, entity_type, entity_id, user_id)


def delete_with_ledger(db: Session, entity_type: str, entity_id: str, user_id: str) -> list[str]:
    """Delete one item, photo or account (steps 1 to 4 above) and return the photo file keys
    for the caller to delete (step 5). Raises 503 if the ledger entry cannot be written."""
    entry = ledger.make_entry(signing_key, settings.ledger_key_id, entity_type, entity_id, user_id, clock.now(),
                              environment=settings.environment)
    _record_intent(entry)
    try:
        ledger_store.write_entry(ledger_store.writer, entry)
    except ledger_store.LedgerUnavailable as error:
        try:
            _remove_intent(entry.entry_id)
        except Exception:  # left for the clean-up of old intents; nothing was deleted
            log.exception("deletion intent left behind after a failed ledger write: %s", entry.entry_id)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "deleting is unavailable for a moment, try again shortly",
                            headers={"Retry-After": "30"}) from error
    for attempt in range(DELETION_TRIES):
        try:
            keys = apply_deletion(db, entity_type, entity_id, user_id)
            db.execute(delete(DeletionIntent).where(DeletionIntent.entry_id == entry.entry_id))
            db.commit()
            return keys
        except Exception as error:
            db.rollback()
            code = _sqlstate(error)
            if isinstance(error, NotFound) or code == _NO_DATA_FOUND:
                # The entity went in the meantime: another deletion (an account deletion that
                # this one waited for) removed it first. Nothing was deleted here; this request
                # did not happen, so its intent goes and the answer is 404, not an error.
                _remove_intent_quietly(entry.entry_id)
                raise HTTPException(status.HTTP_404_NOT_FOUND, f"{entity_type} not found") from error
            if code in _RETRYABLE and attempt + 1 < DELETION_TRIES:
                # PostgreSQL ended this transaction to break a deadlock: nothing of it was kept
                # (the session was rolled back above, so the next try starts a new transaction),
                # the intent and the ledger entry stand, so running the deletion again is safe.
                # Not hidden: logged, and raised if it recurs.
                log.warning("deletion retried after PostgreSQL error %s (try %d of %d)", code, attempt + 1,
                            DELETION_TRIES)
                continue
            raise
    raise AssertionError("unreachable")


# A deletion that PostgreSQL rolls back to break a lock cycle is tried once more (review of #60,
# High 2). With the lock order users before items (apply_deletion, reconcile_apply and the item
# routes' _own_item), no cycle is known between deletions and the API's own writes; this covers
# one that is not. Any other error, or a second one, is raised as it is.
DELETION_TRIES = 2
# deadlock_detected only. serialization_failure (40001) cannot happen here: the deletion runs in
# READ COMMITTED, where PostgreSQL does not raise it; it is not retried.
_RETRYABLE = {"40P01"}
_NO_DATA_FOUND = "P0002"  # raised by dolab.apply_deletion when the entity is not there


def _remove_intent_quietly(entry_id: str) -> None:
    try:
        _remove_intent(entry_id)
    except Exception:  # noqa: BLE001 - an old intent is removed by the reconciliation clean-up
        log.warning("deletion intent left behind after a 404: %s", entry_id)


def _sqlstate(error: BaseException) -> str | None:
    original = getattr(error, "orig", error)
    return getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
