"""Step A of the phase-1 plan: status transitions (T1–T4), time zones, sign-in limits,
concurrent wear records and CORS."""
from datetime import date, datetime, timezone

import pytest

from app import clock
from app.config import _cors_origins, settings
from app.db import SessionLocal
from app.models import Item, ItemStatusChange, WearLog
from app.routers import items as items_router
from app.transitions import path_for

STATUSES = ["available", "laundry", "repair", "stored", "lost", "for_sale", "donated"]
SHIRT = {"name": "Classic white shirt", "category": "shirt"}


def force_status(item_id: str, value: str) -> None:
    """Put an item in a status directly, as the market or a donation would."""
    with SessionLocal() as db:
        db.get(Item, item_id).status = value
        db.commit()


def history_count(item_id: str) -> int:
    with SessionLocal() as db:
        return db.query(ItemStatusChange).filter_by(item_id=item_id).count()


def set_now(monkeypatch, iso: str) -> None:
    moment = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
    monkeypatch.setattr(clock, "now", lambda: moment)


# --- T1–T4: status transitions ---------------------------------------------------


@pytest.mark.parametrize("start", STATUSES)
def test_t1_every_patch_transition_follows_the_table(client, signup, start):
    headers = signup()
    for target in STATUSES:
        item_id = client.post("/items", json=SHIRT, headers=headers).json()["id"]
        force_status(item_id, start)
        before = history_count(item_id)
        response = client.patch(f"/items/{item_id}", json={"status": target}, headers=headers)
        if target == start or path_for(start, target) == "patch":
            assert response.status_code == 200, (start, target, response.text)
            assert response.json()["status"] == target
        else:
            assert response.status_code == 409, (start, target)
            assert client.get(f"/items/{item_id}", headers=headers).json()["status"] == start
            assert history_count(item_id) == before


def test_t2_a_donated_item_cannot_leave_through_patch(client, signup):
    headers = signup()
    item_id = client.post("/items", json=SHIRT, headers=headers).json()["id"]
    client.post("/donations", json={"item_ids": [item_id], "recipient_type": "charity"}, headers=headers)
    response = client.patch(f"/items/{item_id}", json={"status": "available"}, headers=headers)
    assert response.status_code == 409
    assert "cancel" in response.json()["detail"]


def test_t3_reserved_statuses_cannot_be_set_or_created(client, signup):
    headers = signup()
    item_id = client.post("/items", json=SHIRT, headers=headers).json()["id"]
    for target in ("donated", "for_sale"):
        assert client.patch(f"/items/{item_id}", json={"status": target}, headers=headers).status_code == 409
        assert client.post("/items", json={**SHIRT, "status": target}, headers=headers).status_code == 422


def test_t4_only_available_items_can_be_worn(client, signup):
    headers = signup()
    for start in STATUSES:
        item_id = client.post("/items", json=SHIRT, headers=headers).json()["id"]
        force_status(item_id, start)
        expected = 200 if start == "available" else 409
        assert client.post(f"/items/{item_id}/wear", headers=headers).status_code == expected, start


def test_donation_follows_the_table(client, signup):
    headers = signup()
    allowed = [s for s in STATUSES if path_for(s, "donated") == "donation"]
    assert allowed == ["available", "laundry", "repair", "stored"]
    for start in STATUSES:
        item_id = client.post("/items", json=SHIRT, headers=headers).json()["id"]
        force_status(item_id, start)
        response = client.post("/donations", json={"item_ids": [item_id], "recipient_type": "charity"}, headers=headers)
        assert response.status_code == (201 if start in allowed else 409), start


def test_history_records_how_each_change_happened(client, signup):
    headers = signup()
    item_id = client.post("/items", json=SHIRT, headers=headers).json()["id"]
    client.patch(f"/items/{item_id}", json={"status": "laundry"}, headers=headers)
    donation = client.post(
        "/donations", json={"item_ids": [item_id], "recipient_type": "charity"}, headers=headers
    ).json()
    history = client.get(f"/items/{item_id}/history", headers=headers).json()
    assert [(h["to_status"], h["reason"], h["ref_id"]) for h in history] == [
        ("available", "created", None),
        ("laundry", "user", None),
        ("donated", "donation", donation["id"]),
    ]


# --- time zones ------------------------------------------------------------------


def register(client, email, tz):
    response = client.post("/auth/register", json={"email": email, "password": "correct-horse", "timezone": tz})
    assert response.status_code == 201, response.text
    token = client.post("/auth/login", json={"email": email, "password": "correct-horse"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_the_wear_day_is_the_users_own_day(client, monkeypatch):
    set_now(monkeypatch, "2026-01-01T12:00:00")
    ahead = register(client, "kiribati@example.com", "Pacific/Kiritimati")  # UTC+14: already Jan 2
    behind = register(client, "samoa@example.com", "Pacific/Pago_Pago")  # UTC-11: still Jan 1

    item = client.post("/items", json=SHIRT, headers=ahead).json()["id"]
    assert client.post(f"/items/{item}/wear", headers=ahead).json()["last_worn_at"] == "2026-01-02"

    item = client.post("/items", json=SHIRT, headers=behind).json()["id"]
    assert client.post(f"/items/{item}/wear", headers=behind).json()["last_worn_at"] == "2026-01-01"
    tomorrow = client.post(f"/items/{item}/wear", json={"worn_on": "2026-01-02"}, headers=behind)
    assert tomorrow.status_code == 422


def test_the_dashboard_month_is_the_users_month(client, monkeypatch):
    set_now(monkeypatch, "2026-02-01T03:00:00")  # still January 31 in New York
    new_york = register(client, "ny@example.com", "America/New_York")
    utc = register(client, "utc@example.com", "UTC")
    for headers in (new_york, utc):
        item = client.post("/items", json=SHIRT, headers=headers).json()["id"]
        client.post(f"/items/{item}/wear", json={"worn_on": "2026-01-31"}, headers=headers)
    assert client.get("/me/dashboard", headers=new_york).json()["worn_this_month"] == 1
    assert client.get("/me/dashboard", headers=utc).json()["worn_this_month"] == 0


def test_time_zone_is_validated_and_can_be_changed(client, signup):
    bad = client.post("/auth/register", json={"email": "x@example.com", "password": "correct-horse", "timezone": "Mars/Base"})
    assert bad.status_code == 422
    headers = signup()
    assert client.get("/me", headers=headers).json()["timezone"] == "UTC"
    changed = client.patch("/me", json={"timezone": "Africa/Khartoum", "locale": "en"}, headers=headers).json()
    assert (changed["timezone"], changed["locale"]) == ("Africa/Khartoum", "en")
    assert client.patch("/me", json={"timezone": "Nowhere/City"}, headers=headers).status_code == 422


# --- sign-in limits --------------------------------------------------------------


@pytest.fixture
def behind_one_proxy():
    original = settings.trusted_proxy_hops
    object.__setattr__(settings, "trusted_proxy_hops", 1)
    yield
    object.__setattr__(settings, "trusted_proxy_hops", original)


def attempt(client, password, ip):
    return client.post(
        "/auth/login",
        json={"email": "owner@example.com", "password": password},
        headers={"X-Forwarded-For": ip},
    )


def test_someone_else_cannot_lock_the_owner_out(client, signup, behind_one_proxy):
    signup("owner@example.com", "correct-horse")
    for _ in range(5):
        assert attempt(client, "wrong-password", "1.1.1.1").status_code == 401
    assert attempt(client, "wrong-password", "1.1.1.1").status_code == 429
    assert attempt(client, "correct-horse", "1.1.1.1").status_code == 429
    # The owner, from their own address, still signs in.
    assert attempt(client, "correct-horse", "2.2.2.2").status_code == 200


def test_many_addresses_hit_the_email_wide_limit(client, signup, behind_one_proxy):
    signup("owner@example.com", "correct-horse")
    for n in range(20):
        assert attempt(client, "wrong-password", f"10.0.0.{n}").status_code == 401
    blocked = attempt(client, "correct-horse", "10.0.1.1")
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "3600"


def test_forwarded_header_is_ignored_without_a_trusted_proxy(client, signup):
    signup("owner@example.com", "correct-horse")
    for n in range(5):
        attempt(client, "wrong-password", f"9.9.9.{n}")
    # Changing the header does not give a fresh address when no proxy is trusted.
    assert attempt(client, "correct-horse", "8.8.8.8").status_code == 429


def test_a_password_reset_clears_every_limit_for_the_email(client, signup, behind_one_proxy):
    from app.mailer import mailer

    signup("owner@example.com", "correct-horse")
    for n in range(20):
        attempt(client, "wrong-password", f"10.0.0.{n}")
    client.post("/auth/password/forgot", json={"email": "owner@example.com"})
    code = [line for line in mailer.sent[-1].body.splitlines() if len(line) > 30 and " " not in line][0]
    assert client.post("/auth/password/reset", json={"token": code, "new_password": "new-password-1"}).status_code == 204
    assert attempt(client, "new-password-1", "10.0.0.1").status_code == 200


# --- concurrent wear records -----------------------------------------------------


def test_a_wear_recorded_by_a_parallel_request_is_not_an_error(client, signup, monkeypatch):
    headers = signup()
    item_id = client.post("/items", json=SHIRT, headers=headers).json()["id"]
    today = clock.today_in("UTC")
    # Another request recorded today's wear between our check and our commit.
    with SessionLocal() as db:
        user_id = db.get(Item, item_id).user_id
        db.add(WearLog(user_id=user_id, item_id=item_id, worn_on=today))
        db.commit()
    monkeypatch.setattr(items_router, "_already_worn", lambda *args: False)
    response = client.post(f"/items/{item_id}/wear", headers=headers)
    assert response.status_code == 200
    with SessionLocal() as db:
        assert db.query(WearLog).filter_by(item_id=item_id, worn_on=today).count() == 1


# --- CORS ------------------------------------------------------------------------


def preflight(client, origin):
    return client.options(
        "/items",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"},
    )


def test_cors_allows_only_the_configured_origin(client):
    allowed = preflight(client, "https://web.dolab.test")
    assert allowed.headers.get("access-control-allow-origin") == "https://web.dolab.test"
    assert "access-control-allow-credentials" not in allowed.headers
    other = preflight(client, "https://evil.example")
    assert "access-control-allow-origin" not in other.headers


def test_cors_lets_the_web_app_read_the_count_and_retry_headers(client, signup):
    headers = {**signup(), "Origin": "https://web.dolab.test"}
    response = client.get("/items", headers=headers)
    assert response.status_code == 200
    exposed = {h.strip().lower() for h in response.headers.get("access-control-expose-headers", "").split(",")}
    assert {"x-total-count", "retry-after"} <= exposed


@pytest.mark.parametrize("raw", ["*", "https://*.dolab.app", "http://evil.example"])
def test_cors_refuses_wildcards_and_plain_http(raw):
    with pytest.raises(RuntimeError):
        _cors_origins(raw)


def test_cors_accepts_https_and_local_development():
    assert _cors_origins(" https://dolab.app/ , http://localhost:3000") == ("https://dolab.app", "http://localhost:3000")


def test_today_helper_never_uses_the_server_date(monkeypatch):
    set_now(monkeypatch, "2030-06-15T23:30:00")
    assert clock.today_in("Asia/Tokyo") == date(2030, 6, 16)
    assert clock.today_in("Not/AZone") == date(2030, 6, 15)
