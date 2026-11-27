"""Cache backend interface for pyptine."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class CacheBackend(ABC):
    """Abstract cache backend interface.

    Defines the interface that all cache backends must implement.
    """

    @abstractmethod
    def get(self, key: str) -> Any | None:
        """Get value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found/expired
        """
        pass

    @abstractmethod
    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Set value in cache.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds (None for default)
        """
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete value from cache.

        Args:
            key: Cache key

        Returns:
            True if key existed and was deleted
        """
        pass

    @abstractmethod
    def clear(self) -> None:
        """Clear all cached values."""
        pass

    @abstractmethod
    def size(self) -> int:
        """Get number of cached items.

        Returns:
            Number of items in cache
        """
        pass

    @abstractmethod
    def get_cache_dir(self) -> Path | None:
        """Get cache directory path.

        Returns:
            Path to cache directory or None if not applicable
        """
        pass
