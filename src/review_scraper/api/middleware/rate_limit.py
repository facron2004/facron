"""Rate limiting middleware using slowapi."""

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


def get_limiter() -> Limiter:
    """Get rate limiter instance."""
    return Limiter(
        key_func=get_remote_address,
        default_limits=["100/minute", "2000/hour"],
        storage_uri="memory://",
    )


__all__ = ["get_limiter", "RateLimitExceeded", "_rate_limit_exceeded_handler"]
