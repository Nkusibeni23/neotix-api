from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from app.config import settings

# Argon2id with pwdlib's recommended parameters.
_hasher = PasswordHash.recommended()

JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _hasher.verify(password, password_hash)


# Hash of a throwaway password, verified against when the email is unknown so a failed login
# takes the same time whether or not the account exists (no user enumeration by timing).
DUMMY_HASH = hash_password("not-a-real-password")


# Tokens carry a scope so a short-lived stream token (sent in a URL, see routers/events.py) can
# never be used as a normal access token, and vice versa.
ACCESS_SCOPE = "access"
STREAM_SCOPE = "stream"
STREAM_TOKEN_SECONDS = 60


def _encode(user_id: int, scope: str, lifetime: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "scope": scope, "iat": now, "exp": now + lifetime}
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def create_access_token(user_id: int) -> str:
    return _encode(user_id, ACCESS_SCOPE, timedelta(minutes=settings.jwt_expires_minutes))


def create_stream_token(user_id: int) -> str:
    return _encode(user_id, STREAM_SCOPE, timedelta(seconds=STREAM_TOKEN_SECONDS))


def decode_token(token: str, scope: str = ACCESS_SCOPE) -> int | None:
    """Returns the user id, or None if the token is invalid, expired or has another scope."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGORITHM])
        if payload.get("scope") != scope:
            return None
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
