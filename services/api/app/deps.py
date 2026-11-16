from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

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
