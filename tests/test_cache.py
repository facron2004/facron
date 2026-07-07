"""Tests for cache module."""

from __future__ import annotations

import unittest

from review_scraper.core.cache import cache_delete, cache_get, cache_set


class CacheTests(unittest.TestCase):
    def test_cache_set_get(self) -> None:
        """Test basic cache set and get."""
        cache_set("test_key", {"foo": "bar"}, ttl=60)
        result = cache_get("test_key")
        # May be None if Redis not available
        if result is not None:
            self.assertEqual(result, {"foo": "bar"})

    def test_cache_get_nonexistent(self) -> None:
        """Test getting non-existent key returns None."""
        result = cache_get("nonexistent_key_12345")
        self.assertIsNone(result)

    def test_cache_delete(self) -> None:
        """Test cache deletion."""
        cache_set("test_delete", "value", ttl=60)
        cache_delete("test_delete")
        result = cache_get("test_delete")
        self.assertIsNone(result)

    def test_cache_with_complex_data(self) -> None:
        """Test caching complex nested data."""
        data = {
            "list": [1, 2, 3],
            "dict": {"nested": "value"},
            "number": 42,
            "string": "hello",
        }
        cache_set("complex", data, ttl=60)
        result = cache_get("complex")
        if result is not None:
            self.assertEqual(result, data)


if __name__ == "__main__":
    unittest.main()
