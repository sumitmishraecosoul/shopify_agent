import secrets
import time
from typing import Dict

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings
from .mongo_user_store import create_user, get_user

try:
    from passlib.context import CryptContext
except Exception:  # pragma: no cover
    CryptContext = None  # type: ignore[assignment]


_TOKENS: Dict[str, float] = {}
_bearer = HTTPBearer(auto_error=False)
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto") if CryptContext else None


def _cleanup_expired_tokens() -> None:
    now = time.time()
    expired = [t for t, exp in _TOKENS.items() if exp <= now]
    for token in expired:
        _TOKENS.pop(token, None)


def login_and_issue_token(username: str, password: str) -> dict:
    """
    Validate credentials and return an access token payload.
    """
    _cleanup_expired_tokens()

    # 1) If Mongo is configured and user exists there, authenticate against Mongo
    mongo_url = (settings.MONGO_URL or "").strip()
    if mongo_url:
        user = get_user(username)
        if user and _pwd and user.password_hash and _pwd.verify(password, user.password_hash):
            token = secrets.token_urlsafe(32)
            expires_in = int(settings.API_AUTH_TOKEN_TTL_SECONDS)
            _TOKENS[token] = time.time() + expires_in
            return {"access_token": token, "token_type": "bearer", "expires_in": expires_in}

    # 2) Fallback: static env credentials
    if username != settings.API_AUTH_USERNAME or password != settings.API_AUTH_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = secrets.token_urlsafe(32)
    expires_in = int(settings.API_AUTH_TOKEN_TTL_SECONDS)
    _TOKENS[token] = time.time() + expires_in
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": expires_in,
    }


def signup_user(username: str, password: str) -> dict:
    """
    Create a new API user in MongoDB. Requires MONGO_URL to be configured.
    """
    if not (settings.MONGO_URL or "").strip():
        raise HTTPException(status_code=500, detail="MONGO_URL is not configured")
    if not _pwd:
        raise HTTPException(status_code=500, detail="Password hashing is not available (passlib not installed)")

    u = (username or "").strip()
    p = (password or "").strip()
    if len(u) < 3:
        raise HTTPException(status_code=400, detail="username must be at least 3 characters")
    if len(p) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")

    existing = get_user(u)
    if existing:
        raise HTTPException(status_code=409, detail="username already exists")

    ph = _pwd.hash(p)
    rec = create_user(u, ph)
    return {"success": True, "username": rec.username, "message": "User created"}


def require_bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> str:
    """
    FastAPI dependency to protect routes via Authorization: Bearer <token>.
    """
    _cleanup_expired_tokens()
    if credentials is None or (credentials.scheme or "").lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials or ""
    exp = _TOKENS.get(token)
    if not exp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if exp <= time.time():
        _TOKENS.pop(token, None)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


def require_refresh_auth(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> str:
    """
    Auth for inventory refresh trigger.
    Accepts either:
    - normal login-issued bearer tokens, OR
    - a stable service token from settings.INVENTORY_REFRESH_SERVICE_TOKEN
    """
    _cleanup_expired_tokens()
    if credentials is None or (credentials.scheme or "").lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials or ""
    service = (settings.INVENTORY_REFRESH_SERVICE_TOKEN or "").strip()
    if service and token == service:
        return token

    exp = _TOKENS.get(token)
    if not exp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if exp <= time.time():
        _TOKENS.pop(token, None)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token
