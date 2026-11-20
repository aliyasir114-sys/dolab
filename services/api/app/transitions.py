"""Which status changes are allowed, and through which path (plan 4.9).

One table for every path: PATCH, donation, donation cancellation and (later) the market.
"""
from typing import Literal

Path = Literal["patch", "donation", "cancel", "listing"]

_EDITABLE = ("available", "laundry", "repair", "stored")

# ALLOWED[(from, to)] = the only path that may make this change. Missing pair = never allowed.
ALLOWED: dict[tuple[str, str], Path] = {}
for _from in _EDITABLE:
    for _to in _EDITABLE + ("lost",):
        if _from != _to:
            ALLOWED[(_from, _to)] = "patch"
    ALLOWED[(_from, "donated")] = "donation"
for _from in ("available", "stored"):
    ALLOWED[(_from, "for_sale")] = "listing"
ALLOWED[("for_sale", "available")] = "listing"
ALLOWED[("lost", "available")] = "patch"
ALLOWED[("donated", "available")] = "cancel"
# Cancellation returns an item to the status it had before the donation, whichever editable one it was.
for _to in _EDITABLE:
    ALLOWED[("donated", _to)] = "cancel"

# Statuses an item may be created with. donated and for_sale only come from their own paths.
CREATABLE = _EDITABLE + ("lost",)


def path_for(from_status: str, to_status: str) -> Path | None:
    return ALLOWED.get((from_status, to_status))


def refusal(from_status: str, to_status: str) -> str:
    """A human-readable reason a PATCH status change is refused."""
    path = path_for(from_status, to_status)
    if from_status == "donated":
        return "a donated item can only come back by cancelling the donation"
    if path == "donation":
        return "items become donated only through a donation"
    if path == "listing":
        return "sale status is set only through a market listing"
    return f"cannot change status from {from_status} to {to_status}"
