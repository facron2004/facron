"""Celery application for review scraping tasks."""

from celery import Celery

from review_scraper.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "review_scraper",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_default_queue="default",
    task_routes={
        "review_scraper.workers.tasks.scrape_tmall": {"queue": "scrape"},
    },
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=1800,
    task_soft_time_limit=1500,
)

celery_app.autodiscover_tasks(["review_scraper.workers"])
