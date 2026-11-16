from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import delete, func, select

from ..deps import CurrentUser, Db
from ..models import DonationItem, Item, ItemStatusChange, WearLog, new_id
from ..schemas import ItemIn, ItemOut, ItemPatch, ItemStatus, WearIn
from ..services import set_status

router = APIRouter(prefix="/items", tags=["wardrobe"])

# Fields that may be cleared by sending null; the rest ignore null.
_NULLABLE = {"primary_color", "purchase_date", "purchase_price", "currency"}


def _own_item(db: Db, user_id: str, item_id: str) -> Item:
    item = db.get(Item, item_id)
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
) -> list[Item]:
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
    return list(db.scalars(query))


@router.post("", response_model=ItemOut, status_code=status.HTTP_201_CREATED)
def create_item(body: ItemIn, user: CurrentUser, db: Db) -> Item:
    data = body.model_dump()
    first_status = data.pop("status")
    item = Item(id=new_id(), user_id=user.id, wear_count=0, status=first_status, **data)
    db.add(item)
    set_status(db, item, first_status, initial=True)
    db.commit()
    return item


@router.get("/{item_id}", response_model=ItemOut)
def get_item(item_id: str, user: CurrentUser, db: Db) -> Item:
    return _own_item(db, user.id, item_id)


@router.patch("/{item_id}", response_model=ItemOut)
def update_item(item_id: str, body: ItemPatch, user: CurrentUser, db: Db) -> Item:
    item = _own_item(db, user.id, item_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is None and field not in _NULLABLE:
            continue
        if field == "status":
            set_status(db, item, value)
        else:
            setattr(item, field, value)
    db.commit()
    return item


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: str, user: CurrentUser, db: Db) -> Response:
    item = _own_item(db, user.id, item_id)
    db.execute(delete(WearLog).where(WearLog.item_id == item.id))
    db.execute(delete(ItemStatusChange).where(ItemStatusChange.item_id == item.id))
    db.execute(delete(DonationItem).where(DonationItem.item_id == item.id))
    db.delete(item)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{item_id}/wear", response_model=ItemOut)
def wear_item(item_id: str, user: CurrentUser, db: Db, body: WearIn | None = None) -> Item:
    """Record that the item was worn on a day. Only available items can be worn.

    Idempotent per day: recording the same item on the same day twice counts once.
    """
    item = _own_item(db, user.id, item_id)
    if item.status != "available":
        raise HTTPException(status.HTTP_409_CONFLICT, f"item is {item.status}, not available")
    worn_on = (body.worn_on if body else None) or date.today()
    if worn_on > date.today():
        raise HTTPException(422, "worn_on cannot be in the future")
    already = db.scalar(select(WearLog).where(WearLog.item_id == item.id, WearLog.worn_on == worn_on))
    if already is not None:
        return item
    db.add(WearLog(user_id=user.id, item_id=item.id, worn_on=worn_on))
    item.wear_count += 1
    if item.last_worn_at is None or worn_on > item.last_worn_at:
        item.last_worn_at = worn_on
    db.commit()
    return item


@router.get("/{item_id}/history")
def item_history(item_id: str, user: CurrentUser, db: Db) -> list[dict]:
    """Status changes of one item, oldest first."""
    item = _own_item(db, user.id, item_id)
    changes = db.scalars(
        select(ItemStatusChange).where(ItemStatusChange.item_id == item.id).order_by(ItemStatusChange.changed_at)
    )
    return [
        {"from_status": c.from_status, "to_status": c.to_status, "changed_at": c.changed_at.isoformat()}
        for c in changes
    ]
