"""Prometheus metrics for monitoring application performance."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, Info

# Application info
app_info = Info("review_scraper_app", "Application information")

# Request metrics
http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
)

# Task metrics
scrape_tasks_total = Counter(
    "scrape_tasks_total",
    "Total scrape tasks created",
    ["source"],
)

scrape_tasks_completed = Counter(
    "scrape_tasks_completed",
    "Total scrape tasks completed",
    ["source", "status"],
)

scrape_task_duration_seconds = Histogram(
    "scrape_task_duration_seconds",
    "Scrape task duration in seconds",
    ["source"],
)

scrape_task_reviews_collected = Histogram(
    "scrape_task_reviews_collected",
    "Number of reviews collected per task",
    ["source"],
)

# Active tasks gauge
active_scrape_tasks = Gauge(
    "active_scrape_tasks",
    "Number of currently active scrape tasks",
    ["source"],
)

# Cache metrics
cache_operations_total = Counter(
    "cache_operations_total",
    "Total cache operations",
    ["operation", "status"],
)

cache_hit_ratio = Gauge(
    "cache_hit_ratio",
    "Cache hit ratio (0-1)",
)

# Database metrics
db_connections_total = Counter(
    "db_connections_total",
    "Total database connections created",
)

db_query_duration_seconds = Histogram(
    "db_query_duration_seconds",
    "Database query duration in seconds",
    ["operation"],
)

# Browser pool metrics
browser_contexts_active = Gauge(
    "browser_contexts_active",
    "Number of active browser contexts in the pool",
)

browser_contexts_created_total = Counter(
    "browser_contexts_created_total",
    "Total browser contexts created",
)

browser_contexts_evicted_total = Counter(
    "browser_contexts_evicted_total",
    "Total browser contexts evicted from pool",
)

# Analysis metrics
analysis_operations_total = Counter(
    "analysis_operations_total",
    "Total analysis operations",
    ["type"],
)

analysis_duration_seconds = Histogram(
    "analysis_duration_seconds",
    "Analysis operation duration in seconds",
    ["type"],
)


def init_metrics() -> None:
    """Initialize application metrics with default values."""
    app_info.info(
        {
            "version": "1.0.0",
            "name": "review_scraper",
        }
    )
    cache_hit_ratio.set(0.0)
    browser_contexts_active.set(0)
