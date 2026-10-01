from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Role, User
from app.security import decode_access_token

DbSession = Annotated[Session, Depends(get_db)]

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    user_id = decode_access_token(credentials.credentials)
    if user_id is None:
        raise unauthorized
    # Re-read the user on every request so deactivation and role changes apply immediately,
    # not only when the token expires.
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized

    request.state.user_id = user.id  # picked up by the access-log middleware
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: Role) -> Callable[..., User]:
    """Dependency factory: `Depends(require_roles(Role.operator, Role.admin))`."""

    def check(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed for your role")
        return user

    return check


# Operators and admins share every staff permission; admin adds user management.
Staff = Annotated[User, Depends(require_roles(Role.operator, Role.admin))]
Admin = Annotated[User, Depends(require_roles(Role.admin))]
