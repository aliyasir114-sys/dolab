"""Small rules shared by several routers."""
import logging
import time
from collections.abc import Callable

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
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


# The immediate attempt (plan 4.10.3, layer 1): three tries per file, waits growing to at most two
# seconds in all. A file whose three tries fail means storage is down: the attempt stops there,
# and that file and the rest stay queued for the queue job.
FILE_DELETE_TRIES = 3
FILE_DELETE_WAIT_BUDGET = 2.0
_WAITS = (0.2, 0.6)
# Rows removed per statement (SQLite's bound-parameter limit; small statements on PostgreSQL).
_QUEUE_CHUNK = 500


def delete_file_keys(db: Session, keys: list[str], *, sleep: Callable[[float], None] | None = None) -> int:
    """Delete the photo files of a committed deletion, then remove their queue rows.

    Call only after the commit: apply_deletion queued every key in pending_file_deletions in the
    deletion's own transaction, so a file is never lost whatever happens here. A file already
    gone counts as deleted. Returns how many files are left for the queue job. Logs counts only,
    never keys (a key carries the user id).
    """
    sleep = sleep or time.sleep
    deleted: list[str] = []
    budget = FILE_DELETE_WAIT_BUDGET

    def delete_one(key: str) -> bool:
        nonlocal budget
        for attempt in range(FILE_DELETE_TRIES):
            try:
                storage.delete(key)
                return True
            except StorageUnavailable:
                if attempt == FILE_DELETE_TRIES - 1 or _WAITS[attempt] > budget:
                    return False
                sleep(_WAITS[attempt])
                budget -= _WAITS[attempt]
        return False

    for key in keys:
        if not delete_one(key):
            break  # this file failed all its tries: storage is down, leave the rest queued
        deleted.append(key)
    if deleted:
        try:
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


def delete_photo_files(photos: list[ItemPhoto]) -> None:
    """Remove the stored files of the given photos. Call after the database change is committed.

    The deletion has already happened for the user, and the photo's links stop working with its
    record, so a storage failure here does not fail the request: it is logged, and the file is
    left for the orphan clean-up job (step C, a later pull request).
    """
    for photo in photos:
        for key in (photo.original_key, photo.thumb_key):
            try:
                storage.delete(key)
            except StorageUnavailable:
                log.warning("photo file left in storage after its record was deleted: %s", key)


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
