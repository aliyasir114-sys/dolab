"""Password hashing (scrypt, standard library) and access tokens (JWT)."""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from .config import settings

_N, _R, _P, _KEYLEN = 2**14, 8, 1, 32


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_KEYLEN)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def create_token(user_id: str, version: int = 0) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.token_minutes)
    payload = {"sub": str(user_id), "ver": version, "typ": "access", "exp": expires}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def read_token(token: str) -> tuple[str, int] | None:
    """Return (user id, token version) from a valid access token, or None."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        if payload.get("typ") != "access":
            return None
        return str(payload["sub"]), int(payload.get("ver", 0))
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        return None


def new_secret_token() -> str:
    """An opaque random token for refresh and password-reset links."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
