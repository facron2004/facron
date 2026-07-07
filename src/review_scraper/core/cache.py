"""Redis cache utilities."""

from __future__ import annotations

import json
import logging
from typing import Any

import redis

from review_scraper.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_redis_client: redis.Redis | None = None
_cache_hits = 0
_cache_misses = 0


def get_redis() -> redis.Redis:
    """Get Redis client instance."""
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
            )
            _redis_client.ping()
            logger.info("Connected to Redis")
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}, using in-memory fallback")
            _redis_client = None
    return _redis_client


def cache_set(key: str, value: Any, ttl: int = 300) -> bool:
    """Set a value in cache with TTL in seconds."""
    try:
        client = get_redis()
        if client is None:
            return False
        serialized = json.dumps(value, default=str)
        client.setex(key, ttl, serialized)
        from review_scraper.core.metrics import cache_operations_total
        cache_operations_total.labels(operation="set", status="success").inc()
        return True
    except Exception as e:
        logger.warning(f"Cache set failed for {key}: {e}")
        from review_scraper.core.metrics import cache_operations_total
        cache_operations_total.labels(operation="set", status="error").inc()
        return False


def cache_get(key: str) -> Any | None:
    """Get a value from cache."""
    global _cache_hits, _cache_misses
    try:
        client = get_redis()
        if client is None:
            return None
        value = client.get(key)
        if value is None:
            _cache_misses += 1
            from review_scraper.core.metrics import cache_operations_total
            cache_operations_total.labels(operation="get", status="miss").inc()
            return None
        _cache_hits += 1
        from review_scraper.core.metrics import cache_operations_total, cache_hit_ratio
        cache_operations_total.labels(operation="get", status="hit").inc()
        total = _cache_hits + _cache_misses
        cache_hit_ratio.set(_cache_hits / total if total > 0 else 0.0)
        return json.loads(value)
    except Exception as e:
        logger.warning(f"Cache get failed for {key}: {e}")
        from review_scraper.core.metrics import cache_operations_total
        cache_operations_total.labels(operation="get", status="error").inc()
        return None


def cache_delete(key: str) -> bool:
    """Delete a key from cache."""
    try:
        client = get_redis()
        if client is None:
            return False
        client.delete(key)
        from review_scraper.core.metrics import cache_operations_total
        cache_operations_total.labels(operation="delete", status="success").inc()
        return True
    except Exception as e:
        logger.warning(f"Cache delete failed for {key}: {e}")
        from review_scraper.core.metrics import cache_operations_total
        cache_operations_total.labels(operation="delete", status="error").inc()
        return False


def cache_clear_pattern(pattern: str) -> int:
    """Delete all keys matching a pattern."""
    try:
        client = get_redis()
        if client is None:
            return 0
        keys = client.keys(pattern)
        if keys:
            return client.delete(*keys)
        return 0
    except Exception as e:
        logger.warning(f"Cache clear pattern failed for {pattern}: {e}")
        return 0
