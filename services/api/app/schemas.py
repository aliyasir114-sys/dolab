import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

ItemStatus = Literal["available", "laundry", "repair", "stored", "for_sale", "donated", "lost"]
Locale = Literal["ar", "en"]

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _clean_email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or not _EMAIL.match(value):
        raise ValueError("invalid email address")
    return value


class RegisterIn(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)
    locale: Locale = "ar"

    _email = field_validator("email")(_clean_email)


class LoginIn(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=128)

    _email = field_validator("email")(_clean_email)


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    # Seconds until the access token expires; use the refresh token to get a new pair.
    expires_in: int


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=200)


class ForgotPasswordIn(BaseModel):
    email: str

    _email = field_validator("email")(_clean_email)


class ResetPasswordIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(min_length=8, max_length=128)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    locale: str
    account_type: str


class ProfileBody(BaseModel):
    """Every field is optional (PRD 4.4)."""

    model_config = ConfigDict(from_attributes=True)

    height_cm: float | None = Field(default=None, ge=50, le=260)
    weight_kg: float | None = Field(default=None, ge=20, le=400)
    usual_size: str | None = Field(default=None, max_length=10)
    body_shape: str | None = Field(default=None, max_length=30)
    face_shape: str | None = Field(default=None, max_length=30)


def _clean_currency(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", value):
        raise ValueError("currency must be a 3-letter ISO code")
    return value


class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=60)
    primary_color: str | None = Field(default=None, max_length=40)
    secondary_colors: list[str] = Field(default_factory=list, max_length=8)
    color_confirmed: bool = False
    status: ItemStatus = "available"
    purchase_date: date | None = None
    purchase_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str | None = None

    _currency = field_validator("currency")(_clean_currency)


class ItemPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, min_length=1, max_length=60)
    primary_color: str | None = Field(default=None, max_length=40)
    secondary_colors: list[str] | None = Field(default=None, max_length=8)
    color_confirmed: bool | None = None
    status: ItemStatus | None = None
    purchase_date: date | None = None
    purchase_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str | None = None

    _currency = field_validator("currency")(_clean_currency)


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    category: str
    primary_color: str | None
    secondary_colors: list[str]
    color_confidence: float | None
    color_confirmed: bool
    status: str
    purchase_date: date | None
    purchase_price: Decimal | None
    currency: str | None
    added_at: datetime
    updated_at: datetime
    wear_count: int
    last_worn_at: date | None
    # Short-lived signed link to the first photo's thumbnail, if the item has a photo.
    cover_thumb_url: str | None = None

    @computed_field
    @property
    def cost_per_wear(self) -> Decimal | None:
        if self.purchase_price is None or self.wear_count <= 0:
            return None
        return (self.purchase_price / self.wear_count).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class PhotoOut(BaseModel):
    id: str
    content_type: str
    width: int
    height: int
    size_bytes: int
    created_at: datetime
    # Signed links that expire after a few minutes; ask again for fresh ones.
    url: str
    thumb_url: str


class WearIn(BaseModel):
    worn_on: date | None = None


class DonationIn(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=100)
    recipient_type: Literal["charity", "person"]
    recipient_name: str | None = Field(default=None, max_length=120)


class DonationOut(BaseModel):
    id: str
    recipient_type: str
    recipient_name: str | None
    confirmed_at: datetime
    item_ids: list[str]


class DashboardOut(BaseModel):
    total_items: int
    active_items: int
    by_status: dict[str, int]
    colors: dict[str, int]
    worn_this_month: int
    usage_ratio: float | None
