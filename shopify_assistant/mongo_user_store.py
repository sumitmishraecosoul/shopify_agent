from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from pymongo import MongoClient
from pymongo.collection import Collection

from .config import settings


@dataclass(frozen=True)
class UserRecord:
    username: str
    password_hash: str
    created_at: datetime


_client: MongoClient | None = None


def _users_collection() -> Collection:
    """
    Returns the MongoDB collection used to store API users.
    """
    global _client
    mongo_url = (settings.MONGO_URL or "").strip()
    if not mongo_url:
        raise RuntimeError("MONGO_URL is not configured")

    if _client is None:
        _client = MongoClient(mongo_url)

    db = _client[settings.DB_NAME]
    col = db["api_users"]
    col.create_index("username", unique=True)
    return col


def get_user(username: str) -> Optional[UserRecord]:
    u = (username or "").strip()
    if not u:
        return None
    doc = _users_collection().find_one({"username": u})
    if not doc:
        return None
    created_at = doc.get("created_at") or datetime.now(timezone.utc)
    if isinstance(created_at, datetime) and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return UserRecord(
        username=str(doc.get("username") or u),
        password_hash=str(doc.get("password_hash") or ""),
        created_at=created_at,
    )


def create_user(username: str, password_hash: str) -> UserRecord:
    u = (username or "").strip()
    if not u:
        raise ValueError("username is required")
    ph = (password_hash or "").strip()
    if not ph:
        raise ValueError("password_hash is required")

    now = datetime.now(timezone.utc)
    _users_collection().insert_one(
        {"username": u, "password_hash": ph, "created_at": now}
    )
    return UserRecord(username=u, password_hash=ph, created_at=now)

