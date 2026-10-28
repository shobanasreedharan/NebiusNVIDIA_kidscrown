"""Parent authentication: password hashing, JWT issue/verify, request dependency.

Bearer JWT in the Authorization header (no cookies), so there is no CSRF
surface -- the tradeoff is that the client must store the token (XSS exposure);
an httpOnly cookie would avoid that but needs CORS-with-credentials and CSRF
handling. Named as a known tradeoff in the README.

Required env: JWT_SECRET (>= 32 chars, e.g. `openssl rand -hex 32`).
Optional env: JWT_EXPIRE_MINUTES (default 1440 = 24h).
"""

import os
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Parent

JWT_ALGORITHM = "HS256"
DEFAULT_TOKEN_MINUTES = 60 * 24
MIN_SECRET_LENGTH = 32

_hasher = PasswordHasher()
# Verified against when the email is unknown, so "no such user" and "wrong
# password" take similar time and can't be told apart by timing.
_DUMMY_HASH = _hasher.hash("kidscrown-timing-equalizer")
_bearer = HTTPBearer(auto_error=False)


def require_jwt_secret() -> str:
    # Fails closed: no insecure default secret, ever.
    secret = os.getenv("JWT_SECRET", "")
    if len(secret) < MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"JWT_SECRET must be set to at least {MIN_SECRET_LENGTH} characters "
            "(generate one with: openssl rand -hex 32)"
        )
    return secret


def hash_password(password: str) -> str:
    # Hashes a parent password with Argon2 before it is persisted.
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    # Verifies a password while using a dummy hash for unknown accounts.
    # Pass password_hash=None for an unknown user: it still does a full
    # verification (against a dummy hash) and always returns False.
    try:
        _hasher.verify(password_hash or _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False
    return password_hash is not None


def create_access_token(parent_id: int) -> str:
    # Creates a signed bearer token containing the parent identity and expiry.
    now = datetime.now(timezone.utc)
    minutes = int(os.getenv("JWT_EXPIRE_MINUTES", DEFAULT_TOKEN_MINUTES))
    payload = {"sub": str(parent_id), "iat": now, "exp": now + timedelta(minutes=minutes)}
    return jwt.encode(payload, require_jwt_secret(), algorithm=JWT_ALGORITHM)


def _unauthorized(detail: str) -> HTTPException:
    # Builds the consistent 401 response used by bearer-token validation.
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_parent(
    # Authenticates the bearer token and loads its parent account.
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> Parent:
    # Resolves the bearer token to a Parent, or raises 401.
    if credentials is None:
        raise _unauthorized("Not authenticated")
    try:
        payload = jwt.decode(
            credentials.credentials,
            require_jwt_secret(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
        parent_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise _unauthorized("Invalid or expired token") from None
    parent = db.get(Parent, parent_id)
    if parent is None:
        raise _unauthorized("Invalid or expired token")
    return parent
