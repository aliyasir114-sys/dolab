"""Small rules shared by several routers."""
from sqlalchemy.orm import Session

from .models import Item, ItemStatusChange


def set_status(db: Session, item: Item, new_status: str, *, initial: bool = False) -> None:
    """Change an item's status and record the change. No-op if the status is unchanged."""
    if not initial and item.status == new_status:
        return
    db.add(
        ItemStatusChange(
            item_id=item.id,
            user_id=item.user_id,
            from_status=None if initial else item.status,
            to_status=new_status,
        )
    )
    item.status = new_status
