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


def client_ip(request: Request) -> str:
    """The caller's address. Behind N trusted proxies, the Nth address from the right of
    X-Forwarded-For; otherwise the socket address. Untrusted headers are ignored."""
    hops = settings.trusted_proxy_hops
    if hops > 0:
        forwarded = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
        if len(forwarded) >= hops:
            return forwarded[-hops]
    return request.client.host if request.client else "unknown"
