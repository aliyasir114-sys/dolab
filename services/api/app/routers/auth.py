from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .. import clock
from ..config import settings

from ..deps import CurrentUser, Db, client_ip
from ..mailer import send_password_reset
from ..models import (
    Donation,
    DonationItem,
    Item,
    ItemPhoto,
    ItemStatusChange,
    PasswordResetToken,
    Profile,
    RefreshToken,
    User,
    WearLog,
    as_utc,
    new_id,
    utcnow,
)
from ..ratelimit import export_limiter, login_limiter, reset_limiter
from ..schemas import (
    AccountPatch,
    ChangePasswordIn,
    ForgotPasswordIn,
    LoginIn,
    ProfileBody,
    RefreshIn,
    RegisterIn,
    ResetPasswordIn,
    TokenOut,
    UserOut,
)
from .. import export
from ..export import export_payload
from ..services import delete_photo_files
from ..storage import StorageUnavailable
from ..security import create_token, hash_password, new_secret_token, token_hash, verify_password

router = APIRouter(tags=["account"])

_INVALID_REFRESH = HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired, sign in again")


def _issue_tokens(db: Session, user: User, family_id: str | None = None) -> TokenOut:
    """Create an access token and a new refresh token (in a new or existing family)."""
    refresh = new_secret_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            family_id=family_id or new_id(),
            token_hash=token_hash(refresh),
            expires_at=utcnow() + timedelta(days=settings.refresh_days),
        )
    )
    return TokenOut(
        access_token=create_token(user.id, user.token_version),
        refresh_token=refresh,
        expires_in=settings.token_minutes * 60,
    )


def _revoke_all_sessions(db: Session, user_id: str) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


@router.post("/auth/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, db: Db) -> User:
    if db.scalar(select(User).where(User.email == body.email)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        locale=body.locale,
        timezone=body.timezone,
    )
    db.add(user)
    db.commit()
    return user


@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, db: Db, request: Request) -> TokenOut:
    attempt = login_limiter.start(body.email, client_ip(request))
    if attempt.wait is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many failed attempts, try again later",
            headers={"Retry-After": str(attempt.wait)},
        )
    user = db.scalar(select(User).where(User.email == body.email))
    # Same answer for an unknown email and a wrong password, so accounts cannot be probed.
    if user is None or not verify_password(body.password, user.password_hash):
        # The attempt is already recorded: it stays as a failure.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong email or password")
    tokens = _issue_tokens(db, user)
    db.commit()
    login_limiter.succeeded(attempt)
    return tokens


@router.post("/auth/refresh", response_model=TokenOut)
def refresh(body: RefreshIn, db: Db) -> TokenOut:
    """Swap a refresh token for a new pair. Each refresh token works once."""
    token_hash_value = token_hash(body.refresh_token)
    now = utcnow()
    claimed = db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.token_hash == token_hash_value,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        )
        .values(revoked_at=now)
    )
    if claimed.rowcount != 1:
        stored = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash_value))
        if stored is not None and stored.revoked_at is not None:
            # A used token came back: it was copied. End every session descended from that sign-in.
            db.execute(
                update(RefreshToken)
                .where(RefreshToken.family_id == stored.family_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=utcnow())
            )
            db.commit()
        raise _INVALID_REFRESH

    stored = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash_value))
    if stored is None:
        raise _INVALID_REFRESH
    user = db.get(User, stored.user_id)
    if user is None:
        raise _INVALID_REFRESH
    tokens = _issue_tokens(db, user, family_id=stored.family_id)
    db.commit()
    return tokens


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(body: RefreshIn, db: Db) -> Response:
    """End one session (the sign-in this refresh token belongs to). Unknown tokens are ignored."""
    stored = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash(body.refresh_token)))
    if stored is not None:
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == stored.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/auth/password/forgot", status_code=status.HTTP_202_ACCEPTED)
def forgot_password(body: ForgotPasswordIn, db: Db) -> dict[str, str]:
    """Email a single-use reset code. The answer is the same whether or not the email is registered."""
    answer = {"detail": "if this email is registered, a reset code has been sent"}
    if not reset_limiter.allow(body.email):
        return answer
    user = db.scalar(select(User).where(User.email == body.email))
    if user is None:
        return answer
    # Only the newest code works.
    db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=utcnow())
    )
    code = new_secret_token()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash(code),
            expires_at=utcnow() + timedelta(minutes=settings.reset_minutes),
        )
    )
    db.commit()
    send_password_reset(user.email, code, user.locale)
    return answer


@router.post("/auth/password/reset", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(body: ResetPasswordIn, db: Db) -> Response:
    """Set a new password with a reset code. Signs the user out everywhere."""
    stored = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash(body.token)))
    if stored is None or stored.used_at is not None or as_utc(stored.expires_at) <= utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "reset code is invalid or has expired")
    user = db.get(User, stored.user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "reset code is invalid or has expired")
    stored.used_at = utcnow()
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1
    _revoke_all_sessions(db, user.id)
    db.commit()
    login_limiter.clear_email(user.email)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/me/password", response_model=TokenOut)
def change_password(body: ChangePasswordIn, user: CurrentUser, db: Db) -> TokenOut:
    """Change the password while signed in. Other sessions are signed out; this one gets new tokens."""
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "current password is wrong")
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1
    _revoke_all_sessions(db, user.id)
    tokens = _issue_tokens(db, user)
    db.commit()
    return tokens


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user


@router.patch("/me", response_model=UserOut)
def update_me(body: AccountPatch, user: CurrentUser, db: Db) -> User:
    """Change account settings: language for emails, and time zone (which decides the day a wear counts on)."""
    for field, value in body.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(user, field, value)
    db.commit()
    return user


@router.get("/me/export")
def export_data(user: CurrentUser, db: Db) -> dict:
    """Everything DOLAB stores about the signed-in user, as one JSON document (PRD A6)."""
    return export_payload(db, user)


@router.get("/me/export.zip", response_class=FileResponse)
def export_zip(user: CurrentUser, db: Db) -> FileResponse:
    """Everything DOLAB stores about the signed-in user, with the photo files, as one ZIP file
    (plan 4.3): data.json, photos/<item_id>/<photo_id>.<ext>, manifest.json.

    413 over a size limit (checked before any photo is read), 429 after DOLAB_EXPORT_PER_HOUR
    exports in an hour, 503 when this server already builds DOLAB_EXPORT_CONCURRENCY exports,
    when the build runs out of time, or when photo storage is unavailable."""
    user_id = user.id
    db.close()  # do not hold the request's connection while the file is built
    try:
        # One of this instance's places, held until the file has been sent (or the client left).
        place = export.reserve()
    except export.ExportBusy as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "too many exports in progress, try again shortly",
                            headers={"Retry-After": "30"}) from error
    try:
        attempt = export_limiter.start(user_id)
        if attempt.wait is not None:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many exports this hour, try again later",
                                headers={"Retry-After": str(attempt.wait)})
        try:
            path = export.build_zip(user_id, path=place)
        except BaseException as error:
            # A failed export does not use up the hour (and build_zip gave the place back).
            export_limiter.forget(attempt)
            if isinstance(error, export.ExportTooLarge):
                raise HTTPException(
                    413,
                    f"this wardrobe is too large to export at once ({error}); "
                    "large exports will come later as a background job",
                ) from error
            if isinstance(error, export.ExportTimeout):
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "the export took too long, try again later",
                                    headers={"Retry-After": "60"}) from error
            if isinstance(error, StorageUnavailable):
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                    "photo storage is unavailable, try again shortly",
                                    headers={"Retry-After": "30"}) from error
            raise
    except BaseException:
        export.release_file(place)  # refused or failed: the place is free again at once
        raise
    # From here the response owns the place: it gives it back when sending ends.
    return export.TemporaryFileResponse(
        path,
        media_type="application/zip",
        filename=f"dolab-export-{clock.now().date().isoformat()}.zip",
        headers={"Cache-Control": "no-store"},
    )


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
    db.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
    db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id))
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
