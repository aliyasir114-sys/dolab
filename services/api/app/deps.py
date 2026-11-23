import ipaddress
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import User
from .security import read_token

_bearer = HTTPBearer(auto_error=False)

Db = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: Db,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    claims = read_token(credentials.credentials)
    if claims is None:
        raise unauthorized
    user_id, version = claims
    user = db.get(User, user_id)
    if user is None or user.token_version != version:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def _address(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """An IP address, accepting the "1.2.3.4:5678" and "[::1]:80" forms some proxies write."""
    text = text.strip()
    if text.startswith("[") and "]" in text:
        text = text[1 : text.index("]")]
    elif text.count(":") == 1:
        text = text.split(":", 1)[0]
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    mapped = getattr(address, "ipv4_mapped", None)
    return mapped or address


def _trusted(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(address.version == network.version and address in network for network in settings.trusted_proxies)


def client_ip(request: Request) -> str:
    """The caller's address, for rate limits.

    X-Forwarded-For is read only when the connection comes from a trusted proxy
    (DOLAB_TRUSTED_PROXIES), and only from the right: each trusted proxy appends the address it
    received from, so we step left past trusted proxies and stop at the first address that is
    not one. Whatever a browser wrote further left is never reached.
    """
    peer = request.client.host if request.client else ""
    address = _address(peer)
    if address is None:
        return peer or "unknown"
    chain = [part for value in request.headers.getlist("x-forwarded-for") for part in value.split(",") if part.strip()]
    while chain and _trusted(address):
        previous = _address(chain.pop())
        if previous is None:
            # A trusted proxy passed on something that is not an address: do not guess.
            return "invalid-forwarded-address"
        address = previous
    return str(address)
