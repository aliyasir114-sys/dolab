"""Small rules shared by several routers."""
import logging
import time

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .media import signed_path
from .models import Item, ItemPhoto, ItemStatusChange, PendingFileDeletion
from .schemas import ItemOut
from .storage import StorageUnavailable, storage

log = logging.getLogger("dolab.storage")


def set_status(
    db: Session,
    item: Item,
    new_status: str,
    *,
    initial: bool = False,
    reason: str = "user",
    ref_id: str | None = None,
) -> None:
    """Change an item's status and record the change. No-op if the status is unchanged.

    Callers check the transition table (`transitions.path_for`) first; this only records.
    """
    if not initial and item.status == new_status:
        return
    db.add(
        ItemStatusChange(
            item_id=item.id,
            user_id=item.user_id,
            from_status=None if initial else item.status,
            to_status=new_status,
            reason="created" if initial else reason,
            ref_id=ref_id,
        )
    )
    item.status = new_status


# The immediate attempt after the commit (plan 4.10.3, layer 1; notice #59 and its review).
# - Each file gets up to three tries, with waits of 0.2 s then 0.6 s between them.
# - A file that fails does not stop the others: the next file is tried. The attempt stops when
#   two files in a row have failed (storage looks down), or when the time spent on failures
#   (failed storage calls, measured on a monotonic clock, plus the waits) has reached two
#   seconds; after that no retry starts, and the next failed file ends the attempt.
# - A storage call that is already running is not cut short: it ends within the storage
#   client's own connect and read timeouts and retries (app.storage). So the bound on the time
#   lost to a failing storage is two seconds plus at most one such call.
# Files that were not deleted stay queued (apply_deletion queued them before the commit).
FILE_DELETE_TRIES = 3
FILE_FAILURE_BUDGET = 2.0
FAILED_FILES_IN_A_ROW = 2
_WAITS = (0.2, 0.6)
# Rows removed per statement (SQLite's bound-parameter limit; small statements on PostgreSQL).
_QUEUE_CHUNK = 500
# How long the removal of queue rows may wait for a row lock on PostgreSQL. The queue job does
# not hold row locks while it deletes files (it leases rows instead), so a wait means something
# unusual holds them: the rows are then left for the job, which finds the files gone.
QUEUE_LOCK_TIMEOUT = "2s"
_sleep = time.sleep
_monotonic = time.monotonic


def delete_file_keys(db: Session, keys: list[str]) -> int:
    """Delete the photo files of a committed deletion, then remove their queue rows.

    Call only after the commit: apply_deletion queued every key in pending_file_deletions in the
    deletion's own transaction, so a file is never lost whatever happens here. A file already
    gone counts as deleted. Returns how many files are left for the queue job. Logs counts only,
    never keys (a key carries the user id).
    """
    deleted: list[str] = []
    spent = 0.0  # seconds spent on failures
    failed_in_a_row = 0
    for key in keys:
        ok = False
        invalid_key = False
        for attempt in range(FILE_DELETE_TRIES):
            started = _monotonic()
            try:
                storage.delete(key)
                ok = True
                break
            except ValueError:
                invalid_key = True
                break
            except StorageUnavailable:
                spent += _monotonic() - started
            except Exception:  # noqa: BLE001 - an unexpected storage error is a failed try, never raised
                spent += _monotonic() - started
            if attempt == FILE_DELETE_TRIES - 1 or spent + _WAITS[attempt] > FILE_FAILURE_BUDGET:
                break
            _sleep(_WAITS[attempt])
            spent += _WAITS[attempt]
        if invalid_key:
            continue
        if ok:
            deleted.append(key)
            failed_in_a_row = 0
            continue
        failed_in_a_row += 1
        if failed_in_a_row >= FAILED_FILES_IN_A_ROW or spent >= FILE_FAILURE_BUDGET:
            break
    if deleted:
        try:
            if db.get_bind().dialect.name == "postgresql":
                db.execute(text(f"SET LOCAL lock_timeout = '{QUEUE_LOCK_TIMEOUT}'"))
            for start in range(0, len(deleted), _QUEUE_CHUNK):
                chunk = deleted[start:start + _QUEUE_CHUNK]
                db.execute(sa_delete(PendingFileDeletion).where(PendingFileDeletion.key.in_(chunk)))
            db.commit()
        except Exception as error:  # noqa: BLE001 - the queue job finds the file gone and removes the row
            db.rollback()
            log.warning("queue rows of %d deleted photo files not removed (%s)", len(deleted), type(error).__name__)
    left = len(keys) - len(deleted)
    if left:
        log.warning("%d of %d photo files left for the file deletion queue", left, len(keys))
    return left


def delete_files_after_commit(db: Session, keys: list[str]) -> int:
    """The routes' entry point for the immediate attempt, once the deletion is committed.

    Never raises: the deletion is saved, so the request must report it as done whatever happens
    to the files. Any unexpected error here leaves the files and their queue rows for the queue
    job (apply_deletion queued every key before the commit), and is logged by class only.
    Returns how many files are left for the queue job.
    """
    try:
        return delete_file_keys(db, keys)
    except Exception as error:  # noqa: BLE001 - see above
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        log.warning("immediate deletion of %d photo files stopped (%s); all left for the file deletion queue",
                    len(keys), type(error).__name__)
        return len(keys)


def delete_photo_files(photos: list[ItemPhoto]) -> None:
    """Remove the stored files of an upload that failed after its files were saved.

    The photo record was never committed, so a file left here is an orphan for the orphan
    clean-up job. Never raises, so the upload's own error is the one the caller re-raises: a
    storage failure, an invalid key or any unexpected error on one file only counts that file
    as left, and the other files are still tried. Logs counts and classes only, never keys (a
    key carries the user id).
    """
    left, errors = 0, set()
    for photo in photos:
        for key in (photo.original_key, photo.thumb_key):
            try:
                storage.delete(key)
            except Exception as error:  # noqa: BLE001 - StorageUnavailable, ValueError (invalid key), anything
                left += 1
                errors.add(type(error).__name__)
    if left:
        log.warning("%d photo files of a failed upload left in storage for the orphan job (%s)",
                    left, ", ".join(sorted(errors)))


def items_out(db: Session, items: list[Item]) -> list[ItemOut]:
    """Items as API responses, each with a signed link to its first photo's thumbnail."""
    if not items:
        return []
    photos = db.scalars(
        select(ItemPhoto)
        .where(ItemPhoto.item_id.in_([item.id for item in items]))
        .order_by(ItemPhoto.created_at, ItemPhoto.id)
    )
    cover: dict[str, str] = {}
    for photo in photos:
        cover.setdefault(photo.item_id, photo.id)
    result = []
    for item in items:
        out = ItemOut.model_validate(item)
        if item.id in cover:
            out.cover_thumb_url = signed_path(cover[item.id], "thumb")
        result.append(out)
    return result


def item_out(db: Session, item: Item) -> ItemOut:
    return items_out(db, [item])[0]
