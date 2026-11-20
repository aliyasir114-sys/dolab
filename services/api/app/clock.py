"""The one place the app reads the current time.

Every "now" and "today" goes through here, so tests can move time (for example to check a
24-hour window) by replacing `clock.now`, and "today" is always the user's own day.
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def now() -> datetime:
    return datetime.now(timezone.utc)


def valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def today_in(tz_name: str) -> date:
    """Today's date in the given IANA time zone (falls back to UTC for an unknown name)."""
    try:
        zone = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        zone = timezone.utc
    return now().astimezone(zone).date()
