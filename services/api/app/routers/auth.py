from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import delete, select

from ..deps import CurrentUser, Db
from ..models import Donation, DonationItem, Item, Profile, User, WearLog
from ..schemas import LoginIn, ProfileBody, RegisterIn, TokenOut, UserOut
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
    user = db.scalar(select(User).where(User.email == body.email))
    # Same answer for an unknown email and a wrong password, so accounts cannot be probed.
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong email or password")
    return TokenOut(access_token=create_token(user.id))


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(user: CurrentUser, db: Db) -> Response:
    """Delete the account and everything it owns (PRD A6)."""
    donation_ids = select(Donation.id).where(Donation.user_id == user.id)
    db.execute(delete(DonationItem).where(DonationItem.donation_id.in_(donation_ids)))
    db.execute(delete(Donation).where(Donation.user_id == user.id))
    db.execute(delete(WearLog).where(WearLog.user_id == user.id))
    db.execute(delete(Item).where(Item.user_id == user.id))
    db.execute(delete(Profile).where(Profile.user_id == user.id))
    db.execute(delete(User).where(User.id == user.id))
    db.commit()
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
