from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import delete, select

from ..deps import CurrentUser, Db
from ..models import DonationItem, Item, WearLog
from ..schemas import ItemIn, ItemOut, ItemPatch, ItemStatus, WearIn

router = APIRouter(prefix="/items", tags=["wardrobe"])

# Fields that may be cleared by sending null; the rest ignore null.
_NULLABLE = {"primary_color", "purchase_date", "purchase_price", "currency"}


def _own_item(db: Db, user_id: int, item_id: int) -> Item:
    item = db.get(Item, item_id)
    # 404 (not 403) so one user cannot learn which ids belong to another.
    if item is None or item.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "item not found")
    return item


@router.get("", response_model=list[ItemOut])
def list_items(
    user: CurrentUser,
    db: Db,
    status_filter: Annotated[ItemStatus | None, Query(alias="status")] = None,
    category: str | None = None,
    color: str | None = None,
) -> list[Item]:
    query = select(Item).where(Item.user_id == user.id)
    if status_filter is not None:
        query = query.where(Item.status == status_filter)
    if category is not None:
        query = query.where(Item.category == category)
    if color is not None:
        query = query.where(Item.primary_color == color)
    return list(db.scalars(query.order_by(Item.id)))


@router.post("", response_model=ItemOut, status_code=status.HTTP_201_CREATED)
def create_item(body: ItemIn, user: CurrentUser, db: Db) -> Item:
    item = Item(user_id=user.id, wear_count=0, **body.model_dump())
    db.add(item)
    db.commit()
    return item


@router.get("/{item_id}", response_model=ItemOut)
def get_item(item_id: int, user: CurrentUser, db: Db) -> Item:
    return _own_item(db, user.id, item_id)


@router.patch("/{item_id}", response_model=ItemOut)
def update_item(item_id: int, body: ItemPatch, user: CurrentUser, db: Db) -> Item:
    item = _own_item(db, user.id, item_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is None and field not in _NULLABLE:
            continue
        setattr(item, field, value)
    db.commit()
    return item


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, user: CurrentUser, db: Db) -> Response:
    item = _own_item(db, user.id, item_id)
    db.execute(delete(WearLog).where(WearLog.item_id == item.id))
    db.execute(delete(DonationItem).where(DonationItem.item_id == item.id))
    db.delete(item)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{item_id}/wear", response_model=ItemOut)
def wear_item(item_id: int, user: CurrentUser, db: Db, body: WearIn | None = None) -> Item:
    """Record that the item was worn. Only available items can be worn."""
    item = _own_item(db, user.id, item_id)
    if item.status != "available":
        raise HTTPException(status.HTTP_409_CONFLICT, f"item is {item.status}, not available")
    worn_on = (body.worn_on if body else None) or date.today()
    if worn_on > date.today():
        raise HTTPException(422, "worn_on cannot be in the future")
    db.add(WearLog(user_id=user.id, item_id=item.id, worn_on=worn_on))
    item.wear_count += 1
    if item.last_worn_at is None or worn_on > item.last_worn_at:
        item.last_worn_at = worn_on
    db.commit()
    return item
