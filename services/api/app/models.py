import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from . import clock
from .base import Base

ITEM_STATUSES = ("available", "laundry", "repair", "stored", "for_sale", "donated", "lost")
# Items in these states have left the wardrobe and are never suggested or counted as usable.
GONE_STATUSES = ("donated", "lost")

_ID = String(36)


def new_id() -> str:
    """Random, unguessable identifier (UUID4) used for every public record."""
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return clock.now()


def as_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; treat them as UTC. PostgreSQL columns are timestamptz."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    locale: Mapped[str] = mapped_column(String(5), default="ar")
    # IANA name. Decides which calendar day a wear belongs to and where a month starts.
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    account_type: Mapped[str] = mapped_column(String(20), default="individual")
    # Raised on password reset; access tokens carrying an older value stop working at once.
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RefreshToken(Base):
    """A long-lived sign-in session. Only a hash of the token is stored.

    Each use replaces the token with a new one in the same family. If a replaced token is
    presented again, someone copied it, so the whole family is revoked.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(_ID, ForeignKey("users.id"), index=True)
    family_id: Mapped[str] = mapped_column(_ID, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PasswordResetToken(Base):
    """Single-use, short-lived. Only a hash of the token is stored."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(_ID, ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Profile(Base):
    """Optional body details. Sensitive: never exposed to other users, never asked at sign-up."""

    __tablename__ = "profiles"

    user_id: Mapped[str] = mapped_column(_ID, ForeignKey("users.id"), primary_key=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    usual_size: Mapped[str | None] = mapped_column(String(10), nullable=True)
    body_shape: Mapped[str | None] = mapped_column(String(30), nullable=True)
    face_shape: Mapped[str | None] = mapped_column(String(30), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Item(Base):
    __tablename__ = "items"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(_ID, ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(60))
    primary_color: Mapped[str | None] = mapped_column(String(40), nullable=True)
    secondary_colors: Mapped[list] = mapped_column(JSON, default=list)
    color_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    color_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="available", index=True)
    # Purchase date, date added and last worn are separate facts; age is never inferred from added_at.
    purchase_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    wear_count: Mapped[int] = mapped_column(Integer, default=0)
    last_worn_at: Mapped[date | None] = mapped_column(Date, nullable=True)


class ItemPhoto(Base):
    """A private photo of an item. Files are reached only through short-lived signed URLs."""

    __tablename__ = "item_photos"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(_ID, ForeignKey("items.id"), index=True)
    # Owner is stored on the photo itself so access never depends on a join.
    user_id: Mapped[str] = mapped_column(_ID, ForeignKey("users.id"), index=True)
    original_key: Mapped[str] = mapped_column(String(200))
    thumb_key: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(30))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ItemStatusChange(Base):
    """History of an item's status, so the current status is not the only record."""

    __tablename__ = "item_status_changes"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(_ID, ForeignKey("items.id"), index=True)
    user_id: Mapped[str] = mapped_column(_ID, ForeignKey("users.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20))
    # How the change happened: created, user, donation, donation_cancelled, listing.
    reason: Mapped[str] = mapped_column(String(20), default="user")
    # The donation or listing that caused the change, if any.
    ref_id: Mapped[str | None] = mapped_column(_ID, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WearLog(Base):
    __tablename__ = "wear_logs"
    # One wear per item per day: a double tap cannot inflate the count.
    __table_args__ = (UniqueConstraint("item_id", "worn_on", name="uq_wear_item_day"),)

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(_ID, ForeignKey("users.id"), index=True)
    item_id: Mapped[str] = mapped_column(_ID, ForeignKey("items.id"), index=True)
    worn_on: Mapped[date] = mapped_column(Date)


class Donation(Base):
    __tablename__ = "donations"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(_ID, ForeignKey("users.id"), index=True)
    # "person" = handed over directly; "charity" = a named organisation. Neither is verified by DOLAB.
    recipient_type: Mapped[str] = mapped_column(String(10))
    recipient_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DonationItem(Base):
    __tablename__ = "donation_items"

    donation_id: Mapped[str] = mapped_column(_ID, ForeignKey("donations.id"), primary_key=True)
    item_id: Mapped[str] = mapped_column(_ID, ForeignKey("items.id"), primary_key=True)


class RateLimitEvent(Base):
    """One counted attempt (sign-in or reset email), shared by every server process (plan 4.7).

    No email or address is stored: key_hash and subject_hash are keyed hashes (HMAC) of them.
    Rows live at most a few hours and are not linked to an account.
    """

    __tablename__ = "rate_limit_events"
    # Counting a key's events in a time window; the name is what the naming convention gives.
    __table_args__ = (Index("ix_rate_limit_events_scope", "scope", "key_hash", "at"),)

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(String(32))
    key_hash: Mapped[str] = mapped_column(String(64))
    # The email the event is about, so a password reset can clear every limit for that email.
    subject_hash: Mapped[str] = mapped_column(String(64), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
