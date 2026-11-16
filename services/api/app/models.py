from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

ITEM_STATUSES = ("available", "laundry", "repair", "stored", "for_sale", "donated", "lost")
# Items in these states have left the wardrobe and are never suggested or counted as usable.
GONE_STATUSES = ("donated", "lost")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    locale: Mapped[str] = mapped_column(String(5), default="ar")
    account_type: Mapped[str] = mapped_column(String(20), default="individual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Profile(Base):
    """Optional body details. Sensitive: never exposed to other users."""

    __tablename__ = "profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    usual_size: Mapped[str | None] = mapped_column(String(10), nullable=True)
    body_shape: Mapped[str | None] = mapped_column(String(30), nullable=True)
    face_shape: Mapped[str | None] = mapped_column(String(30), nullable=True)


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(60))
    primary_color: Mapped[str | None] = mapped_column(String(40), nullable=True)
    secondary_colors: Mapped[list] = mapped_column(JSON, default=list)
    color_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    color_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="available", index=True)
    # Purchase date, date added and expected lifetime are separate facts (PRD 4.2).
    purchase_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    wear_count: Mapped[int] = mapped_column(Integer, default=0)
    last_worn_at: Mapped[date | None] = mapped_column(Date, nullable=True)


class WearLog(Base):
    __tablename__ = "wear_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    worn_on: Mapped[date] = mapped_column(Date)


class Donation(Base):
    __tablename__ = "donations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    recipient_type: Mapped[str] = mapped_column(String(10))
    recipient_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DonationItem(Base):
    __tablename__ = "donation_items"

    donation_id: Mapped[int] = mapped_column(ForeignKey("donations.id"), primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), primary_key=True)
