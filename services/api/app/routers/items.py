from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from .. import clock
from ..deps import CurrentUser, Db
from ..models import DonationItem, Item, ItemPhoto, ItemStatusChange, WearLog, new_id
from ..schemas import ItemIn, ItemOut, ItemPatch, ItemStatus, WearIn
from ..services import delete_photo_files, item_out, items_out, set_status
from ..transitions import path_for, refusal

router = APIRouter(prefix="/items", tags=["wardrobe"])

# Fields that may be cleared by sending null; the rest ignore null.
_NULLABLE = {"primary_color", "purchase_date", "purchase_price", "currency"}


def _own_item(db: Db, user_id: str, item_id: str, *, lock: bool = False) -> Item:
    """The user's item, or 404. With lock=True the row is locked until commit (PostgreSQL;
    SQLite ignores it), so concurrent writes to the same item are applied one after another."""
    item = db.get(Item, item_id, with_for_update=lock or None)
    # 404 (not 403) so one user cannot learn which ids belong to another.
    if item is None or item.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "item not found")
    return item


@router.get("", response_model=list[ItemOut])
def list_items(
    response: Response,
    user: CurrentUser,
    db: Db,
    status_filter: Annotated[ItemStatus | None, Query(alias="status")] = None,
    category: str | None = None,
    color: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ItemOut]:
    """List the user's items, oldest first. The total is returned in the X-Total-Count header."""
    conditions = [Item.user_id == user.id]
    if status_filter is not None:
        conditions.append(Item.status == status_filter)
    if category is not None:
        conditions.append(Item.category == category)
    if color is not None:
        conditions.append(Item.primary_color == color)
    total = db.scalar(select(func.count()).select_from(Item).where(*conditions))
    response.headers["X-Total-Count"] = str(total)
    query = select(Item).where(*conditions).order_by(Item.added_at, Item.id).limit(limit).offset(offset)
    return items_out(db, list(db.scalars(query)))


@router.post("", response_model=ItemOut, status_code=status.HTTP_201_CREATED)
def create_item(body: ItemIn, user: CurrentUser, db: Db) -> ItemOut:
    data = body.model_dump()
    first_status = data.pop("status")
    item = Item(id=new_id(), user_id=user.id, wear_count=0, status=first_status, **data)
    db.add(item)
    set_status(db, item, first_status, initial=True)
    db.commit()
    return item_out(db, item)


@router.get("/{item_id}", response_model=ItemOut)
def get_item(item_id: str, user: CurrentUser, db: Db) -> ItemOut:
    return item_out(db, _own_item(db, user.id, item_id))


@router.patch("/{item_id}", response_model=ItemOut)
def update_item(item_id: str, body: ItemPatch, user: CurrentUser, db: Db) -> ItemOut:
    item = _own_item(db, user.id, item_id, lock=True)
    changes = body.model_dump(exclude_unset=True)
    new_status = changes.get("status")
    if new_status is not None and new_status != item.status and path_for(item.status, new_status) != "patch":
        raise HTTPException(status.HTTP_409_CONFLICT, refusal(item.status, new_status))
    for field, value in changes.items():
        if value is None and field not in _NULLABLE:
            continue
        if field == "status":
            set_status(db, item, value)
        else:
            setattr(item, field, value)
    db.commit()
    return item_out(db, item)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: str, user: CurrentUser, db: Db) -> Response:
    item = _own_item(db, user.id, item_id)
    db.execute(delete(WearLog).where(WearLog.item_id == item.id))
    db.execute(delete(ItemStatusChange).where(ItemStatusChange.item_id == item.id))
    db.execute(delete(DonationItem).where(DonationItem.item_id == item.id))
    photos = list(db.scalars(select(ItemPhoto).where(ItemPhoto.item_id == item.id)))
    # Explicit statements in dependency order: the models declare no relationships, so the ORM
    # would not know the photos must go before the item (PostgreSQL enforces the foreign key).
    db.execute(delete(ItemPhoto).where(ItemPhoto.item_id == item.id))
    db.execute(delete(Item).where(Item.id == item.id))
    db.commit()
    delete_photo_files(photos)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{item_id}/wear", response_model=ItemOut)
def wear_item(item_id: str, user: CurrentUser, db: Db, body: WearIn | None = None) -> ItemOut:
    """Record that the item was worn on a day. Only available items can be worn.

    Idempotent per day: recording the same item on the same day twice counts once.
    """
    item = _own_item(db, user.id, item_id, lock=True)
    if item.status != "available":
        raise HTTPException(status.HTTP_409_CONFLICT, f"item is {item.status}, not available")
    today = clock.today_in(user.timezone)
    worn_on = (body.worn_on if body else None) or today
    if worn_on > today:
        raise HTTPException(422, "worn_on cannot be in the future")
    if _already_worn(db, item.id, worn_on):
        return item_out(db, item)
    db.add(WearLog(user_id=user.id, item_id=item.id, worn_on=worn_on))
    item.wear_count += 1
    if item.last_worn_at is None or worn_on > item.last_worn_at:
        item.last_worn_at = worn_on
    try:
        db.commit()
    except IntegrityError:
        # Another request recorded the same item and day first (unique constraint). Same answer.
        db.rollback()
        return item_out(db, _own_item(db, user.id, item_id))
    return item_out(db, item)


def _already_worn(db: Db, item_id: str, day) -> bool:
    return db.scalar(select(WearLog.id).where(WearLog.item_id == item_id, WearLog.worn_on == day)) is not None


@router.get("/{item_id}/history")
def item_history(item_id: str, user: CurrentUser, db: Db) -> list[dict]:
    """Status changes of one item, oldest first."""
    item = _own_item(db, user.id, item_id)
    changes = db.scalars(
        select(ItemStatusChange).where(ItemStatusChange.item_id == item.id).order_by(ItemStatusChange.changed_at)
    )
    return [
        {
            "from_status": c.from_status,
            "to_status": c.to_status,
            "reason": c.reason,
            "ref_id": c.ref_id,
            "changed_at": c.changed_at.isoformat(),
        }
        for c in changes
    ]
