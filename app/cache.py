import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import CacheEntry


class Cache:
    # Provides database-backed JSON caching with expiration handling.
    def __init__(self, db: Session):
        self.db = db

    # Returns a cached value when it exists and has not expired.
    def get(self, key: str) -> Any | None:
        entry = self.db.get(CacheEntry, key)
        if entry is None:
            return None
        expires_at = entry.expires_at
        if expires_at.tzinfo is None:
            # SQLite drops tzinfo on round-trip even though the column is
            # DateTime(timezone=True); normalize so this comparison is safe
            # on both SQLite and Postgres.
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            self.db.delete(entry)
            self.db.commit()
            return None
        return json.loads(entry.value)

    # Stores or refreshes a JSON value with a time-to-live.
    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        entry = self.db.get(CacheEntry, key)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        if entry is None:
            entry = CacheEntry(key=key, value=json.dumps(value), expires_at=expires_at)
            self.db.add(entry)
        else:
            entry.value = json.dumps(value)
            entry.expires_at = expires_at
        self.db.commit()
