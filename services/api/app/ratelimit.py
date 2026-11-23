"""Limits on sign-in attempts and reset emails (plan 4.7), shared by every server process.

The counts live in PostgreSQL (table rate_limit_events), not in memory, so they hold across
several server instances and restarts. No new infrastructure: a count over a time window with
an index. Rows are deleted once they leave every window.

Every attempt is written first and counted afterwards, each in its own committed transaction.
Requests that arrive together therefore see each other: the n-th to commit counts at least n,
so parallel guesses cannot all pass a check made before any of them was recorded. An attempt
that turns out to be blocked is deleted again, so being blocked never extends a block.

Nothing readable is stored: keys are HMACs of the email and address under a key derived from
the server secret.
"""
import hashlib
import hmac
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from . import clock
from .config import settings
from .db import SessionLocal
from .models import RateLimitEvent, as_utc, new_id

_KEY = hashlib.sha256(b"dolab-rate-limit|" + settings.jwt_secret.encode()).digest()
# Rows older than this are past every window below.
_KEEP = timedelta(hours=2)
_PURGE_EVERY = timedelta(minutes=1)
_last_purge: datetime | None = None


def _hash(*parts: str) -> str:
    return hmac.new(_KEY, "|".join(parts).encode(), hashlib.sha256).hexdigest()


def _subject(email: str) -> str:
    return _hash("subject", email.strip().lower())


def _seconds(delta: timedelta) -> int:
    return max(1, math.ceil(delta.total_seconds()))


@dataclass(frozen=True)
class _Key:
    scope: str
    key_hash: str
    subject_hash: str


def _record(keys: list[_Key]) -> tuple[datetime, list[str]]:
    """Write one event per key, committed at once; return the time and the new rows' ids."""
    global _last_purge
    now = clock.now()
    ids = [new_id() for _ in keys]
    with SessionLocal.begin() as db:
        for key, row_id in zip(keys, ids):
            db.add(RateLimitEvent(id=row_id, scope=key.scope, key_hash=key.key_hash, subject_hash=key.subject_hash, at=now))
        # Keep the table small: this key's old rows now, everyone's old rows once a minute.
        for key in keys:
            db.execute(delete(RateLimitEvent).where(
                RateLimitEvent.scope == key.scope, RateLimitEvent.key_hash == key.key_hash, RateLimitEvent.at < now - _KEEP))
        if _last_purge is None or now - _last_purge >= _PURGE_EVERY or now < _last_purge:
            db.execute(delete(RateLimitEvent).where(RateLimitEvent.at < now - _KEEP))
            _last_purge = now
    return now, ids


def _others(key: _Key, own_id: str, since: datetime) -> list[datetime]:
    """Times of this key's other events after `since`, oldest first (the attempt's own row excluded).
    An event exactly one window old has left the window."""
    with SessionLocal() as db:
        rows = db.scalars(
            select(RateLimitEvent.at)
            .where(RateLimitEvent.scope == key.scope, RateLimitEvent.key_hash == key.key_hash,
                   RateLimitEvent.at > since, RateLimitEvent.id != own_id)
            .order_by(RateLimitEvent.at)
        ).all()
    return [as_utc(at) for at in rows]


def _forget(ids: list[str]) -> None:
    if ids:
        with SessionLocal.begin() as db:
            db.execute(delete(RateLimitEvent).where(RateLimitEvent.id.in_(ids)))


@dataclass
class LoginAttempt:
    """One sign-in attempt. `wait` is set (seconds) when it is refused before the password is checked."""

    email: str
    ip: str
    ids: list[str] = field(default_factory=list)
    wait: int | None = None


class LoginLimiter:
    """Two limits on sign-ins (plan 4.7):

    - Per email and client address: 5 failures in 15 minutes block that address for that email.
    - Per email from anywhere: from 20 failures in an hour on, each new attempt waits longer
      (30 s, 1 min, 2 min, ... at most 15 min after the last one) instead of a full lock. So
      someone who only knows an email cannot lock its owner out from another address, and a
      sustained attack from many addresses slows to a few guesses an hour.
    """

    per_address = 5
    address_window = timedelta(minutes=15)
    per_email = 20
    email_window = timedelta(hours=1)
    first_delay = timedelta(seconds=30)
    longest_delay = timedelta(minutes=15)

    def _keys(self, email: str, ip: str) -> tuple[_Key, _Key]:
        email = email.strip().lower()
        subject = _subject(email)
        return (_Key("login_address", _hash("login_address", email, ip), subject),
                _Key("login_email", _hash("login_email", email), subject))

    def start(self, email: str, ip: str) -> LoginAttempt:
        """Record the attempt, then decide whether it may go on."""
        address_key, email_key = self._keys(email, ip)
        now, ids = _record([address_key, email_key])
        attempt = LoginAttempt(email=email, ip=ip, ids=ids)

        earlier = _others(address_key, ids[0], now - self.address_window)
        if len(earlier) >= self.per_address:
            # Free again when enough of them leave the window.
            attempt.wait = _seconds(earlier[len(earlier) - self.per_address] + self.address_window - now)
        else:
            earlier = _others(email_key, ids[1], now - self.email_window)
            if len(earlier) >= self.per_email:
                delay = min(self.first_delay * 2 ** (len(earlier) - self.per_email), self.longest_delay)
                since_last = now - earlier[-1]
                if since_last < delay:
                    attempt.wait = _seconds(delay - since_last)
        if attempt.wait is not None:
            _forget(ids)
            attempt.ids = []
        return attempt

    def succeeded(self, attempt: LoginAttempt) -> None:
        """The password was right: this address starts afresh; earlier failures on the email stay
        (so a sign-in from one address does not reset an attack from others)."""
        address_key, _ = self._keys(attempt.email, attempt.ip)
        with SessionLocal.begin() as db:
            db.execute(delete(RateLimitEvent).where(
                RateLimitEvent.scope == address_key.scope, RateLimitEvent.key_hash == address_key.key_hash))
            if attempt.ids:
                db.execute(delete(RateLimitEvent).where(RateLimitEvent.id.in_(attempt.ids)))

    def clear_email(self, email: str) -> None:
        """After a password reset: every sign-in limit for this email."""
        with SessionLocal.begin() as db:
            db.execute(delete(RateLimitEvent).where(
                RateLimitEvent.scope.in_(("login_address", "login_email")), RateLimitEvent.subject_hash == _subject(email)))

    def reset(self) -> None:
        """Tests only: forget everything."""
        with SessionLocal.begin() as db:
            db.execute(delete(RateLimitEvent))


class ResetLimiter:
    """Reset emails: at most 3 an hour per email, so the endpoint cannot flood an inbox."""

    limit = 3
    window = timedelta(hours=1)

    def allow(self, email: str) -> bool:
        email = email.strip().lower()
        key = _Key("reset_email", _hash("reset_email", email), _subject(email))
        now, ids = _record([key])
        if len(_others(key, ids[0], now - self.window)) >= self.limit:
            _forget(ids)
            return False
        return True

    def reset(self) -> None:
        LoginLimiter().reset()


login_limiter = LoginLimiter()
reset_limiter = ResetLimiter()
