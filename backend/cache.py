"""A small SQLite cache so repeated questions cost no SerpApi credits."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class Cache:
    def __init__(self, path: Path, ttl_hours: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = max(0, ttl_hours) * 3600
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, created REAL NOT NULL)"
        )
        self._db.commit()

    def get(self, key: str) -> Any | None:
        with self._lock:
            row = self._db.execute("SELECT value, created FROM cache WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        value, created = row
        if self.ttl_seconds and time.time() - created > self.ttl_seconds:
            return None
        return json.loads(value)

    def set(self, key: str, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO cache (key, value, created) VALUES (?, ?, ?)",
                (key, payload, time.time()),
            )
            self._db.commit()

    def count(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM cache").fetchone()[0])
