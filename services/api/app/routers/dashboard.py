from fastapi import APIRouter
from sqlalchemy import func, select

from .. import clock
from ..deps import CurrentUser, Db
from ..models import GONE_STATUSES, Item, WearLog
from ..schemas import DashboardOut

router = APIRouter(tags=["dashboard"])


@router.get("/me/dashboard", response_model=DashboardOut)
def dashboard(user: CurrentUser, db: Db) -> DashboardOut:
    """Wardrobe numbers for the fashion dashboard (PRD F1-F3)."""
    items = list(db.scalars(select(Item).where(Item.user_id == user.id)))
    by_status: dict[str, int] = {}
    colors: dict[str, int] = {}
    active_ids = set()
    for item in items:
        by_status[item.status] = by_status.get(item.status, 0) + 1
        if item.status in GONE_STATUSES:
            continue
        active_ids.add(item.id)
        if item.primary_color:
            colors[item.primary_color] = colors.get(item.primary_color, 0) + 1

    month_start = clock.today_in(user.timezone).replace(day=1)
    worn_ids = set(
        db.scalars(
            select(func.distinct(WearLog.item_id)).where(WearLog.user_id == user.id, WearLog.worn_on >= month_start)
        )
    )
    worn = len(worn_ids & active_ids)
    return DashboardOut(
        total_items=len(items),
        active_items=len(active_ids),
        by_status=by_status,
        colors=colors,
        worn_this_month=worn,
        usage_ratio=round(worn / len(active_ids), 4) if active_ids else None,
    )
