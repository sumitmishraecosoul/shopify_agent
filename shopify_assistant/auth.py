import secrets
import time
from typing import Dict

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings


_TOKENS: Dict[str, float] = {}
_bearer = HTTPBearer(auto_error=False)


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


def require_bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
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
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
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
