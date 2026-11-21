import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from app.db import SessionLocal
from app.mailer import mailer
from app.models import PasswordResetToken, RefreshToken, utcnow
from app.routers import auth as auth_router
from app.security import create_token

EMAIL = "ali@example.com"
PASSWORD = "correct-horse"


def register(client, email=EMAIL, password=PASSWORD, locale="ar"):
    response = client.post("/auth/register", json={"email": email, "password": password, "locale": locale})
    assert response.status_code == 201, response.text
    return response.json()


def login(client, email=EMAIL, password=PASSWORD):
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def reset_code_from_mail() -> str:
    body = mailer.sent[-1].body
    return re.search(r"^([A-Za-z0-9_-]{30,})$", body, re.MULTILINE).group(1)


# --- refresh tokens --------------------------------------------------------


def test_login_returns_a_refresh_token_and_expiry(client):
    register(client)
    tokens = login(client)
    assert tokens["refresh_token"] and tokens["refresh_token"] != tokens["access_token"]
    assert tokens["expires_in"] == 15 * 60


def test_refresh_gives_a_new_working_pair(client):
    register(client)
    first = login(client)
    second = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert second.status_code == 200
    second = second.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert client.get("/me", headers=auth(second)).json()["email"] == EMAIL


def test_reusing_a_refresh_token_ends_the_whole_session(client):
    register(client)
    first = login(client)
    second = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).json()
    # The old token comes back (e.g. it was stolen): refused, and the newer one dies too.
    assert client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]}).status_code == 401


def test_reuse_on_one_device_does_not_end_other_devices(client):
    register(client)
    phone = login(client)
    laptop = login(client)
    client.post("/auth/refresh", json={"refresh_token": phone["refresh_token"]})
    client.post("/auth/refresh", json={"refresh_token": phone["refresh_token"]})  # reuse on the phone
    assert client.post("/auth/refresh", json={"refresh_token": laptop["refresh_token"]}).status_code == 200


def test_concurrent_refresh_uses_a_token_once_and_revokes_the_family(client, monkeypatch):
    register(client)
    first = login(client)
    barrier = threading.Barrier(2)
    original_issue_tokens = auth_router._issue_tokens

    def slow_issue_tokens(db, user, family_id=None):
        time.sleep(0.25)
        return original_issue_tokens(db, user, family_id)

    monkeypatch.setattr(auth_router, "_issue_tokens", slow_issue_tokens)

    def refresh_once(_):
        barrier.wait()
        return client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(refresh_once, range(2)))

    successful = [response for response in responses if response.status_code == 200]
    refused = [response for response in responses if response.status_code == 401]
    assert len(successful) == 1
    assert len(refused) == 1
    replacement = successful[0].json()["refresh_token"]
    assert client.post("/auth/refresh", json={"refresh_token": replacement}).status_code == 401


def test_expired_and_unknown_refresh_tokens_are_refused(client):
    register(client)
    tokens = login(client)
    with SessionLocal() as db:
        for row in db.query(RefreshToken):
            row.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    assert client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": "x" * 43}).status_code == 401


def test_logout_ends_that_session_only(client):
    register(client)
    phone = login(client)
    laptop = login(client)
    assert client.post("/auth/logout", json={"refresh_token": phone["refresh_token"]}).status_code == 204
    assert client.post("/auth/refresh", json={"refresh_token": phone["refresh_token"]}).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": laptop["refresh_token"]}).status_code == 200
    # Logging out with an unknown token is harmless.
    assert client.post("/auth/logout", json={"refresh_token": "y" * 43}).status_code == 204


def test_refresh_tokens_are_stored_only_as_hashes(client):
    register(client)
    tokens = login(client)
    with SessionLocal() as db:
        stored = [row.token_hash for row in db.query(RefreshToken)]
    assert tokens["refresh_token"] not in stored
    assert all(len(value) == 64 for value in stored)


def test_a_refresh_token_cannot_be_used_as_an_access_token(client):
    register(client)
    tokens = login(client)
    headers = {"Authorization": f"Bearer {tokens['refresh_token']}"}
    assert client.get("/me", headers=headers).status_code == 401


# --- password reset --------------------------------------------------------


def test_forgot_password_answers_the_same_for_unknown_emails(client):
    register(client)
    known = client.post("/auth/password/forgot", json={"email": EMAIL})
    unknown = client.post("/auth/password/forgot", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert [m.to for m in mailer.sent] == [EMAIL]


def test_reset_email_is_in_the_users_language(client):
    register(client, locale="en")
    client.post("/auth/password/forgot", json={"email": EMAIL})
    assert mailer.sent[-1].subject == "Reset your DOLAB password"


def test_full_password_reset(client):
    register(client)
    old_session = login(client)
    client.post("/auth/password/forgot", json={"email": EMAIL})
    code = reset_code_from_mail()

    response = client.post("/auth/password/reset", json={"token": code, "new_password": "new-password-1"})
    assert response.status_code == 204
    # Old password and every old session stop working.
    assert client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 401
    assert client.get("/me", headers=auth(old_session)).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": old_session["refresh_token"]}).status_code == 401
    # New password works; the code cannot be used twice.
    login(client, password="new-password-1")
    again = client.post("/auth/password/reset", json={"token": code, "new_password": "another-pass-2"})
    assert again.status_code == 400


def test_only_the_newest_reset_code_works(client):
    register(client)
    client.post("/auth/password/forgot", json={"email": EMAIL})
    first = reset_code_from_mail()
    client.post("/auth/password/forgot", json={"email": EMAIL})
    second = reset_code_from_mail()
    assert client.post("/auth/password/reset", json={"token": first, "new_password": "new-password-1"}).status_code == 400
    assert client.post("/auth/password/reset", json={"token": second, "new_password": "new-password-1"}).status_code == 204


def test_expired_reset_code_is_refused(client):
    register(client)
    client.post("/auth/password/forgot", json={"email": EMAIL})
    code = reset_code_from_mail()
    with SessionLocal() as db:
        for row in db.query(PasswordResetToken):
            row.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    assert client.post("/auth/password/reset", json={"token": code, "new_password": "new-password-1"}).status_code == 400


def test_reset_emails_are_rate_limited(client):
    register(client)
    for _ in range(5):
        assert client.post("/auth/password/forgot", json={"email": EMAIL}).status_code == 202
    assert len(mailer.sent) == 3


def test_reset_clears_a_login_lockout(client):
    register(client)
    for _ in range(5):
        client.post("/auth/login", json={"email": EMAIL, "password": "wrong-password"})
    assert client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 429
    client.post("/auth/password/forgot", json={"email": EMAIL})
    client.post("/auth/password/reset", json={"token": reset_code_from_mail(), "new_password": "new-password-1"})
    login(client, password="new-password-1")


def test_reset_rejects_a_short_password(client):
    register(client)
    client.post("/auth/password/forgot", json={"email": EMAIL})
    response = client.post("/auth/password/reset", json={"token": reset_code_from_mail(), "new_password": "short"})
    assert response.status_code == 422


# --- change password -------------------------------------------------------


def test_change_password_keeps_this_session_and_ends_others(client):
    register(client)
    phone = login(client)
    laptop = login(client)
    wrong = client.post(
        "/me/password", json={"current_password": "nope", "new_password": "new-password-1"}, headers=auth(phone)
    )
    assert wrong.status_code == 403

    changed = client.post(
        "/me/password", json={"current_password": PASSWORD, "new_password": "new-password-1"}, headers=auth(phone)
    )
    assert changed.status_code == 200
    fresh = changed.json()
    assert client.get("/me", headers=auth(fresh)).status_code == 200
    assert client.get("/me", headers=auth(laptop)).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": laptop["refresh_token"]}).status_code == 401
    login(client, password="new-password-1")


def test_old_style_token_without_version_matches_version_zero(client):
    user = register(client)
    assert client.get("/me", headers={"Authorization": f"Bearer {create_token(user['id'])}"}).status_code == 200


def test_deleting_the_account_removes_sessions(client):
    register(client)
    tokens = login(client)
    assert client.delete("/me", headers=auth(tokens)).status_code == 204
    with SessionLocal() as db:
        assert db.query(RefreshToken).count() == 0
    assert client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
