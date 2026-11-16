from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ..deps import CurrentUser, Db
from ..models import GONE_STATUSES, Donation, DonationItem, Item
from ..schemas import DonationIn, DonationOut

router = APIRouter(prefix="/donations", tags=["donations"])


def _out(donation: Donation, item_ids: list[int]) -> DonationOut:
    return DonationOut(
        id=donation.id,
        recipient_type=donation.recipient_type,
        recipient_name=donation.recipient_name,
        confirmed_at=donation.confirmed_at,
        item_ids=sorted(item_ids),
    )


@router.post("", response_model=DonationOut, status_code=status.HTTP_201_CREATED)
def donate(body: DonationIn, user: CurrentUser, db: Db) -> DonationOut:
    """Donate wardrobe items. They move to 'donated' and leave outfit suggestions (PRD D1-D3)."""
    item_ids = sorted(set(body.item_ids))
    items = list(db.scalars(select(Item).where(Item.user_id == user.id, Item.id.in_(item_ids))))
    if len(items) != len(item_ids):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "one or more items not found")
    gone = [item.id for item in items if item.status in GONE_STATUSES]
    if gone:
        raise HTTPException(status.HTTP_409_CONFLICT, f"items already donated or lost: {gone}")
    donation = Donation(user_id=user.id, recipient_type=body.recipient_type, recipient_name=body.recipient_name)
    db.add(donation)
    db.flush()
    for item in items:
        item.status = "donated"
        db.add(DonationItem(donation_id=donation.id, item_id=item.id))
    db.commit()
    return _out(donation, item_ids)


@router.get("", response_model=list[DonationOut])
def list_donations(user: CurrentUser, db: Db) -> list[DonationOut]:
    donations = list(db.scalars(select(Donation).where(Donation.user_id == user.id).order_by(Donation.id)))
    result = []
    for donation in donations:
        ids = list(db.scalars(select(DonationItem.item_id).where(DonationItem.donation_id == donation.id)))
        result.append(_out(donation, ids))
    return result
