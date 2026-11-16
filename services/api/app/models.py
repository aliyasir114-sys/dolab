import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

ITEM_STATUSES = ("available", "laundry", "repair", "stored", "for_sale", "donated", "lost")
# Items in these states have left the wardrobe and are never suggested or counted as usable.
GONE_STATUSES = ("donated", "lost")

_ID = String(36)


def new_id() -> str:
    """Random, unguessable identifier (UUID4) used for every public record."""
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    locale: Mapped[str] = mapped_column(String(5), default="ar")
    account_type: Mapped[str] = mapped_column(String(20), default="individual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


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
