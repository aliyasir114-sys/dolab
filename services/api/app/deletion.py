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

Not covered yet: DELETE /me/profile (clearing body measurements) writes no entry, because format
v1 knows only item, photo and user. Until "profile" is added in a format v2 (a separate request,
Ali's decision 2026-11-25), a restored backup can bring cleared measurements back; the deletion
guarantee is not complete before then.

In the next part, step 4 moves into the one PostgreSQL function dolab.apply_deletion, shared with
the reconciliation job, with a test that both delete the same.
"""
import logging
from collections.abc import Callable

from fastapi import HTTPException, status
from sqlalchemy import delete
from sqlalchemy.orm import Session

from . import clock, ledger, ledger_store
from .config import settings
from .db import SessionLocal
from .models import DeletionIntent

log = logging.getLogger("dolab.deletion")

signing_key = ledger.private_key_from_seed(settings.ledger_signing_key)


def _record_intent(entry: ledger.Entry) -> None:
    with SessionLocal.begin() as db:
        db.add(DeletionIntent(entry_id=entry.entry_id, entity_type=entry.entity_type, entity_id=entry.entity_id,
                              user_id=entry.user_id, created_at=clock.now()))


def _remove_intent(entry_id: str) -> None:
    with SessionLocal.begin() as db:
        db.execute(delete(DeletionIntent).where(DeletionIntent.entry_id == entry_id))


def delete_with_ledger(db: Session, entity_type: str, entity_id: str, user_id: str,
                       apply: Callable[[Session], None]) -> ledger.Entry:
    """Run `apply` (the deletion statements, not yet committed) only once the ledger entry is
    confirmed, and commit it together with the removal of the intent. Raises 503 otherwise."""
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
    apply(db)
    db.execute(delete(DeletionIntent).where(DeletionIntent.entry_id == entry.entry_id))
    db.commit()
    return entry
