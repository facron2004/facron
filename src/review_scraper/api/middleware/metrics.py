"""Middleware for collecting HTTP request metrics."""

from __future__ import annotations

import time
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from review_scraper.core.metrics import http_request_duration_seconds, http_requests_total


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware to collect Prometheus metrics for HTTP requests."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and collect metrics."""
        start_time = time.time()

        # Process request
        response = await call_next(request)

        # Calculate duration
        duration = time.time() - start_time

        # Extract endpoint path template (e.g., /api/reviews/tmall/tasks/{task_id})
        endpoint = request.url.path
        if request.path_params:
            # Replace actual path params with template placeholders
            for param_name, param_value in request.path_params.items():
                endpoint = endpoint.replace(str(param_value), f"{{{param_name}}}")

        # Record metrics
        http_requests_total.labels(
            method=request.method,
            endpoint=endpoint,
            status=response.status_code,
        ).inc()

        http_request_duration_seconds.labels(
            method=request.method,
            endpoint=endpoint,
        ).observe(duration)

        return response
