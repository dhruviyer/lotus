import os
import pickle
import sqlite3
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from functools import wraps
from typing import Any, Callable

import lotus


def require_cache_enabled(func: Callable) -> Callable:
    """Decorator to check if caching is enabled before calling the function."""

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not lotus.settings.enable_cache:
            return None
        return func(self, *args, **kwargs)

    return wrapper


class Cache(ABC):
    def __init__(self, max_size: int):
        self.max_size = max_size

    @abstractmethod
    def get(self, key: str) -> Any | None:
        pass

    @abstractmethod
    def insert(self, key: str, value: Any):
        pass

    @abstractmethod
    def reset(self, max_size: int | None = None):
        pass


class SQLiteCache(Cache):
    def __init__(self, max_size: int, cache_dir=os.path.expanduser("~/.lotus/cache")):
        super().__init__(max_size)
        self.db_path = os.path.join(cache_dir, "lotus_cache.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self._create_table()

    def _create_table(self):
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    value BLOB,
                    last_accessed INTEGER
                )
            """)

    def _get_time(self):
        return int(time.time())

    @require_cache_enabled
    def get(self, key: str) -> Any | None:
        with self.conn:
            cursor = self.conn.execute("SELECT value FROM cache WHERE key = ?", (key,))
            result = cursor.fetchone()
            if result:
                lotus.logger.debug(f"Cache hit for {key}")
                value = pickle.loads(result[0])
                self.conn.execute(
                    "UPDATE cache SET last_accessed = ? WHERE key = ?",
                    (
                        self._get_time(),
                        key,
                    ),
                )
                return value
        return None

    @require_cache_enabled
    def insert(self, key: str, value: Any):
        pickled_value = pickle.dumps(value)
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO cache (key, value, last_accessed) 
                VALUES (?, ?, ?)
            """,
                (key, pickled_value, self._get_time()),
            )
            self._enforce_size_limit()

    def _enforce_size_limit(self):
        with self.conn:
            count = self.conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            if count > self.max_size:
                num_to_delete = count - self.max_size
                self.conn.execute(
                    """
                    DELETE FROM cache WHERE key IN (
                        SELECT key FROM cache
                        ORDER BY last_accessed ASC
                        LIMIT ?
                    )
                """,
                    (num_to_delete,),
                )

    def reset(self, max_size: int | None = None):
        with self.conn:
            self.conn.execute("DELETE FROM cache")
        if max_size is not None:
            self.max_size = max_size

    def __del__(self):
        self.conn.close()


class InMemoryCache(Cache):
    def __init__(self, max_size: int):
        super().__init__(max_size)
        self.cache: OrderedDict[str, Any] = OrderedDict()

    @require_cache_enabled
    def get(self, key: str) -> Any | None:
        if key in self.cache:
            lotus.logger.debug(f"Cache hit for {key}")

        return self.cache.get(key)

    @require_cache_enabled
    def insert(self, key: str, value: Any):
        self.cache[key] = value

        # LRU eviction
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False)

    def reset(self, max_size: int | None = None):
        self.cache.clear()
        if max_size is not None:
            self.max_size = max_size
