from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import delete, select

from ..deps import CurrentUser, Db
from ..models import Donation, DonationItem, Item, ItemPhoto, ItemStatusChange, Profile, User, WearLog
from ..ratelimit import login_limiter
from ..schemas import ItemOut, LoginIn, ProfileBody, RegisterIn, TokenOut, UserOut
from ..services import delete_photo_files
from ..security import create_token, hash_password, verify_password

router = APIRouter(tags=["account"])


@router.post("/auth/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, db: Db) -> User:
    if db.scalar(select(User).where(User.email == body.email)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    user = User(email=body.email, password_hash=hash_password(body.password), locale=body.locale)
    db.add(user)
    db.commit()
    return user


@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, db: Db) -> TokenOut:
    if login_limiter.blocked(body.email):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many failed attempts, try again later",
            headers={"Retry-After": str(login_limiter.window_seconds)},
        )
    user = db.scalar(select(User).where(User.email == body.email))
    # Same answer for an unknown email and a wrong password, so accounts cannot be probed.
    if user is None or not verify_password(body.password, user.password_hash):
        login_limiter.record_failure(body.email)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong email or password")
    login_limiter.clear(body.email)
    return TokenOut(access_token=create_token(user.id))


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user


@router.get("/me/export")
def export_data(user: CurrentUser, db: Db) -> dict:
    """Everything DOLAB stores about the signed-in user, as one JSON document (PRD A6)."""
    profile = db.get(Profile, user.id)
    items = list(db.scalars(select(Item).where(Item.user_id == user.id).order_by(Item.added_at, Item.id)))
    wears = db.scalars(select(WearLog).where(WearLog.user_id == user.id).order_by(WearLog.worn_on))
    changes = db.scalars(
        select(ItemStatusChange).where(ItemStatusChange.user_id == user.id).order_by(ItemStatusChange.changed_at)
    )
    donations = []
    for donation in db.scalars(select(Donation).where(Donation.user_id == user.id).order_by(Donation.confirmed_at)):
        item_ids = db.scalars(select(DonationItem.item_id).where(DonationItem.donation_id == donation.id))
        donations.append(
            {
                "id": donation.id,
                "recipient_type": donation.recipient_type,
                "recipient_name": donation.recipient_name,
                "confirmed_at": donation.confirmed_at.isoformat(),
                "item_ids": sorted(item_ids),
            }
        )
    return {
        "user": UserOut.model_validate(user).model_dump(mode="json"),
        "profile": ProfileBody.model_validate(profile).model_dump(mode="json") if profile else None,
        "items": [ItemOut.model_validate(item).model_dump(mode="json") for item in items],
        "wear_log": [{"item_id": w.item_id, "worn_on": w.worn_on.isoformat()} for w in wears],
        "status_history": [
            {
                "item_id": c.item_id,
                "from_status": c.from_status,
                "to_status": c.to_status,
                "changed_at": c.changed_at.isoformat(),
            }
            for c in changes
        ],
        "donations": donations,
        # Photo files are downloaded through the signed links on each item's photo list.
        "photos": [
            {
                "id": p.id,
                "item_id": p.item_id,
                "content_type": p.content_type,
                "width": p.width,
                "height": p.height,
                "size_bytes": p.size_bytes,
                "created_at": p.created_at.isoformat(),
            }
            for p in db.scalars(select(ItemPhoto).where(ItemPhoto.user_id == user.id).order_by(ItemPhoto.created_at))
        ],
    }


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(user: CurrentUser, db: Db) -> Response:
    """Delete the account and everything it owns, including photo files (PRD A6)."""
    photos = list(db.scalars(select(ItemPhoto).where(ItemPhoto.user_id == user.id)))
    db.execute(delete(ItemPhoto).where(ItemPhoto.user_id == user.id))
    donation_ids = select(Donation.id).where(Donation.user_id == user.id)
    db.execute(delete(DonationItem).where(DonationItem.donation_id.in_(donation_ids)))
    db.execute(delete(Donation).where(Donation.user_id == user.id))
    db.execute(delete(WearLog).where(WearLog.user_id == user.id))
    db.execute(delete(ItemStatusChange).where(ItemStatusChange.user_id == user.id))
    db.execute(delete(Item).where(Item.user_id == user.id))
    db.execute(delete(Profile).where(Profile.user_id == user.id))
    db.execute(delete(User).where(User.id == user.id))
    db.commit()
    delete_photo_files(photos)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me/profile", response_model=ProfileBody)
def get_profile(user: CurrentUser, db: Db) -> Profile | ProfileBody:
    return db.get(Profile, user.id) or ProfileBody()


@router.put("/me/profile", response_model=ProfileBody)
def put_profile(body: ProfileBody, user: CurrentUser, db: Db) -> Profile:
    profile = db.get(Profile, user.id)
    if profile is None:
        profile = Profile(user_id=user.id)
        db.add(profile)
    for field, value in body.model_dump().items():
        setattr(profile, field, value)
    db.commit()
    return profile


@router.delete("/me/profile", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(user: CurrentUser, db: Db) -> Response:
    """Remove all body details. They are optional and can be withdrawn at any time."""
    db.execute(delete(Profile).where(Profile.user_id == user.id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
