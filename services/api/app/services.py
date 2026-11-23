"""Small rules shared by several routers."""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .media import signed_path
from .models import Item, ItemPhoto, ItemStatusChange
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
