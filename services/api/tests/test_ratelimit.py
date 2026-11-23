"""Step C-1: the sign-in and reset limits are shared through the database, and the client
address comes only from trusted proxies (plan 4.7)."""
import ipaddress
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from starlette.datastructures import Headers

from app import clock
from app import ratelimit as rl
from app.config import settings
from app.db import SessionLocal
from app.deps import client_ip
from app.models import RateLimitEvent

API_DIR = Path(__file__).resolve().parents[1]
EMAIL = "owner@example.com"


@pytest.fixture
def trust():
    """Trust the given proxy ranges for one test. The settings object is frozen, so the attribute
    is replaced the same way the hardening tests do."""
    original = settings.trusted_proxies

    def _set(*ranges: str) -> None:
        object.__setattr__(settings, "trusted_proxies", tuple(ipaddress.ip_network(r) for r in ranges))

    yield _set
    object.__setattr__(settings, "trusted_proxies", original)


@pytest.fixture
def at(monkeypatch):
    """Move the clock: at(seconds) sets now to a fixed start plus that many seconds."""
    start = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)

    def _at(seconds: float) -> None:
        moment = start + timedelta(seconds=seconds)
        monkeypatch.setattr(clock, "now", lambda: moment)

    _at(0)
    return _at


def request(peer: str | None, *forwarded: str):
    """A stand-in for a Starlette request: the connection's address and X-Forwarded-For headers."""
    raw = [(b"x-forwarded-for", value.encode()) for value in forwarded]
    return SimpleNamespace(client=SimpleNamespace(host=peer) if peer else None, headers=Headers(raw=raw))


# ------------------------------------------------------------------ client address

def test_without_trusted_proxies_the_header_is_ignored(trust):
    trust()
    assert client_ip(request("203.0.113.5", "198.51.100.1")) == "203.0.113.5"


def test_a_trusted_proxy_gives_the_address_it_saw(trust):
    trust("10.0.0.0/8")
    assert client_ip(request("10.0.0.2", "198.51.100.1")) == "198.51.100.1"


def test_a_forged_header_from_the_browser_is_never_reached(trust):
    # The browser wrote "1.1.1.1"; the trusted proxy appended the address it really saw.
    trust("10.0.0.0/8")
    assert client_ip(request("10.0.0.2", "1.1.1.1, 198.51.100.1")) == "198.51.100.1"
    # Several headers count as one list, in order.
    assert client_ip(request("10.0.0.2", "1.1.1.1", "198.51.100.1")) == "198.51.100.1"


def test_a_chain_of_trusted_proxies_is_stepped_through(trust):
    # Load balancer 10.0.0.1 in front of the web server 10.0.0.2, which calls the API.
    trust("10.0.0.0/8")
    assert client_ip(request("10.0.0.2", "1.1.1.1, 198.51.100.1, 10.0.0.1")) == "198.51.100.1"


def test_a_header_from_an_untrusted_caller_is_ignored(trust):
    trust("10.0.0.0/8")
    assert client_ip(request("203.0.113.5", "10.0.0.9")) == "203.0.113.5"


def test_odd_forms_of_addresses(trust):
    trust("10.0.0.0/8", "2001:db8::/32")
    assert client_ip(request("10.0.0.2", "198.51.100.1:4711")) == "198.51.100.1"
    assert client_ip(request("10.0.0.2", "[2001:db9::1]:443")) == "2001:db9::1"
    assert client_ip(request("::ffff:10.0.0.2", "198.51.100.1")) == "198.51.100.1"  # IPv4 seen as IPv6
    assert client_ip(request("2001:db8::7", "198.51.100.1")) == "198.51.100.1"
    # A trusted proxy that passes on something that is not an address is not guessed at.
    assert client_ip(request("10.0.0.2", "unknown")) == "invalid-forwarded-address"
    # A trusted proxy with nothing to pass on is itself the caller.
    assert client_ip(request("10.0.0.2")) == "10.0.0.2"
    assert client_ip(request(None)) == "unknown"


def _settings_with(env: dict[str, str]) -> subprocess.CompletedProcess:
    """Load the settings in a fresh interpreter with these variables."""
    full = {k: v for k, v in os.environ.items() if not k.startswith("DOLAB_TRUSTED")}
    full.update(env)
    code = "from app.config import settings; print([str(n) for n in settings.trusted_proxies])"
    return subprocess.run([sys.executable, "-c", code], cwd=API_DIR, env=full, capture_output=True, text=True)


def test_trusted_proxy_settings_are_checked():
    good = _settings_with({"DOLAB_TRUSTED_PROXIES": "10.0.0.0/8, 192.0.2.7, 2001:db8::/32"})
    assert good.returncode == 0, good.stderr
    assert good.stdout.strip() == "['10.0.0.0/8', '192.0.2.7/32', '2001:db8::/32']"
    bad = _settings_with({"DOLAB_TRUSTED_PROXIES": "10.0.0.1/8"})  # host bits set: probably a mistake
    assert bad.returncode != 0 and "DOLAB_TRUSTED_PROXIES" in bad.stderr
    # The old hop count trusted the header from anyone; it is refused, not silently ignored.
    old = _settings_with({"DOLAB_TRUSTED_PROXY_HOPS": "1"})
    assert old.returncode != 0 and "DOLAB_TRUSTED_PROXIES" in old.stderr


# ------------------------------------------------------------------ sign-in limits

def test_limits_live_in_the_database_not_in_the_process(client, at):
    # Two limiter objects stand for two server processes (or one before and after a restart).
    first, second = rl.LoginLimiter(), rl.LoginLimiter()
    for n in range(5):
        limiter = first if n % 2 else second
        assert limiter.start(EMAIL, "198.51.100.1").wait is None
    assert first.start(EMAIL, "198.51.100.1").wait is not None
    assert second.start(EMAIL, "198.51.100.1").wait is not None
    assert rl.LoginLimiter().start(EMAIL, "198.51.100.2").wait is None  # another address


def test_nothing_readable_is_stored(client, at):
    rl.login_limiter.start(EMAIL, "198.51.100.1")
    rl.reset_limiter.allow(EMAIL)
    with SessionLocal() as db:
        rows = db.scalars(select(RateLimitEvent)).all()
    assert {row.scope for row in rows} == {"login_address", "login_email", "reset_email"}
    for row in rows:
        stored = f"{row.scope} {row.key_hash} {row.subject_hash}"
        assert "owner" not in stored and "example" not in stored and "198.51" not in stored
        assert len(row.key_hash) == len(row.subject_hash) == 64


def test_the_address_limit_ends_when_its_window_does(client, at):
    for n in range(5):
        at(n * 60)
        rl.login_limiter.start(EMAIL, "198.51.100.1")
    at(5 * 60)
    blocked = rl.login_limiter.start(EMAIL, "198.51.100.1")
    # Free when the first failure (at 0) is 15 minutes old.
    assert blocked.wait == 10 * 60
    at(15 * 60)
    assert rl.login_limiter.start(EMAIL, "198.51.100.1").wait is None


def test_blocked_attempts_do_not_extend_the_block(client, at):
    for _ in range(5):
        rl.login_limiter.start(EMAIL, "198.51.100.1")
    for n in range(1, 20):
        at(n * 30)
        assert rl.login_limiter.start(EMAIL, "198.51.100.1").wait is not None
    at(15 * 60)
    assert rl.login_limiter.start(EMAIL, "198.51.100.1").wait is None


def test_the_email_wide_limit_is_a_growing_delay_not_a_lock(client, at):
    for n in range(20):
        assert rl.login_limiter.start(EMAIL, f"198.51.100.{n}").wait is None
    # 20 failures: the next attempt from anywhere waits 30 seconds after the last one.
    assert rl.login_limiter.start(EMAIL, "203.0.113.1").wait == 30
    at(30)
    assert rl.login_limiter.start(EMAIL, "203.0.113.1").wait is None  # failure 21
    at(31)
    assert rl.login_limiter.start(EMAIL, "203.0.113.2").wait == 59  # doubled to 60 s after failure 21
    at(90)
    assert rl.login_limiter.start(EMAIL, "203.0.113.2").wait is None  # failure 22
    last = 90
    for n in range(23, 28):
        # Each time after waiting the full delay: 2 min, 4 min, then capped at 15 min.
        delay = min(30 * 2 ** (n - 21), 15 * 60)
        at(last + delay - 1)
        assert rl.login_limiter.start(EMAIL, f"203.0.113.{n}").wait == 1, n
        at(last + delay)
        assert rl.login_limiter.start(EMAIL, f"203.0.113.{n}").wait is None, n
        last += delay
    assert last == 2730
    # When the burst at 0 leaves the one-hour window, only 7 failures remain: no delay.
    at(3601)
    assert rl.login_limiter.start(EMAIL, "203.0.113.99").wait is None


def test_the_owner_still_gets_in_through_the_delay(client, signup, at):
    signup(EMAIL, "correct-horse")
    for n in range(20):
        rl.login_limiter.start(EMAIL, f"198.51.100.{n}")
    refused = client.post("/auth/login", json={"email": EMAIL, "password": "correct-horse"})
    assert refused.status_code == 429 and refused.headers["Retry-After"] == "30"
    at(30)
    assert client.post("/auth/login", json={"email": EMAIL, "password": "correct-horse"}).status_code == 200


def test_a_success_clears_its_address_but_not_the_email_count(client, signup, at):
    signup(EMAIL, "correct-horse")
    for _ in range(4):
        assert client.post("/auth/login", json={"email": EMAIL, "password": "wrong-password"}).status_code == 401
    assert client.post("/auth/login", json={"email": EMAIL, "password": "correct-horse"}).status_code == 200
    for _ in range(5):
        assert client.post("/auth/login", json={"email": EMAIL, "password": "wrong-password"}).status_code == 401
    with SessionLocal() as db:
        scopes = [row.scope for row in db.scalars(select(RateLimitEvent)).all()]
    # 4 + 5 failures on the email (the 2 sign-ins of signup and the success are not kept),
    # 5 on the address since the success.
    assert scopes.count("login_email") == 9
    assert scopes.count("login_address") == 5


def test_reset_emails_are_limited_and_shared(client, at):
    assert [rl.ResetLimiter().allow(EMAIL) for _ in range(4)] == [True, True, True, False]
    at(3600)
    assert rl.reset_limiter.allow(EMAIL) is True


def test_old_rows_are_purged(client, at):
    rl.login_limiter.start(EMAIL, "198.51.100.1")
    rl.login_limiter.start("other@example.com", "198.51.100.1")
    at(3 * 3600)
    rl.login_limiter.start("third@example.com", "198.51.100.1")
    with SessionLocal() as db:
        assert len(db.scalars(select(RateLimitEvent)).all()) == 2  # only the new attempt's two rows
