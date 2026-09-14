from __future__ import annotations

from collections import OrderedDict
from threading import Lock
from typing import Generic, TypeVar


Key = TypeVar("Key", bound=object)
Value = TypeVar("Value")


class BoundedCache(Generic[Key, Value]):
    """Thread-safe LRU cache for deterministic preprocessing results."""

    def __init__(self, max_entries: int = 256) -> None:
        self._max_entries = max_entries
        self._entries: OrderedDict[Key, Value] = OrderedDict()
        self._lock = Lock()

    def get(self, key: Key) -> Value | None:
        with self._lock:
            value = self._entries.get(key)
            if value is not None:
                self._entries.move_to_end(key)
            return value

    def set(self, key: Key, value: Value) -> None:
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            if len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
