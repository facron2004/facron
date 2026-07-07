"""Celery tasks for review scraping pipeline.

All competitive-intelligence / keyword-monitor tasks have been removed.
This module focuses exclusively on the e-commerce review scraping workflow.
"""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path

from celery import shared_task

from review_scraper.core.cache import cache_delete
from review_scraper.core.config import get_settings
from review_scraper.core.database import get_session
from review_scraper.core.metrics import active_scrape_tasks, scrape_tasks_completed
from review_scraper.core.websocket import get_task_update_publisher
from review_scraper.models import Review, ScrapeTask, TaskArtifact, TaskLog
from review_scraper.modules.tmall_reviews.collector import ReviewBatch, ReviewCollector, scrape_reviews
from review_scraper.modules.tmall_reviews.export import export_reviews
from review_scraper.modules.tmall_reviews.parser import extract_item_id

logger = logging.getLogger(__name__)

settings = get_settings()
publisher = get_task_update_publisher()


# Cache keys must match the writer in api/routes/reviews.py — keep them
# in sync so the worker actually invalidates the cached task list pages.
_TASK_LIST_CACHE_LIMITS: tuple[int, ...] = (10, 20, 50, 100)


def _invalidate_task_cache(task_id: str | None = None) -> None:
    if task_id:
        cache_delete(f"task:{task_id}")
    for limit in _TASK_LIST_CACHE_LIMITS:
        cache_delete(f"tasks:list:{limit}")


def _resolve_output_dir() -> Path:
    configured = Path(settings.review_export_dir)
    if configured.is_absolute():
        return configured
    return Path.cwd() / configured


def _resolve_task_output_dir(task_id: str) -> Path:
    base = _resolve_output_dir().parent / "tasks" / task_id
    base.mkdir(parents=True, exist_ok=True)
    return base


def _write_artifact(task_id: str, artifact_type: str, path: Path, mime_type: str | None = None) -> None:
    session = get_session()
    try:
        session.add(
            TaskArtifact(
                task_id=task_id,
                artifact_type=artifact_type,
                file_path=str(path),
                file_name=path.name,
                mime_type=mime_type,
                size_bytes=path.stat().st_size if path.exists() else None,
            )
        )
        session.commit()
    finally:
        session.close()


def _write_failure_artifacts(task_id: str, stage: str, exc: Exception, context: dict[str, object] | None = None) -> dict[str, str]:
    output_dir = _resolve_task_output_dir(task_id)
    error_json = output_dir / "task_error.json"
    html_path = output_dir / "error.html"
    log_path = output_dir / "console.log"
    screenshot_path = output_dir / "error.png"

    payload = {
        "task_id": task_id,
        "stage": stage,
        "error_type": type(exc).__name__,
        "message": str(exc),
        "suggestion": "请检查浏览器登录态、网络连接和页面结构是否变化。",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if context:
        payload.update(context)

    error_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(
        "<html><body><h1>Scrape Failure</h1><pre>" + traceback.format_exc() + "</pre></body></html>",
        encoding="utf-8",
    )
    log_path.write_text(traceback.format_exc(), encoding="utf-8")
    if not screenshot_path.exists():
        screenshot_path.write_bytes(b"")

    return {
        "task_error": str(error_json),
        "error_html": str(html_path),
        "console_log": str(log_path),
        "screenshot": str(screenshot_path),
    }


def _send_ws_update(task_id: str, payload: dict) -> None:
    """Fire-and-forget WebSocket push from a Celery (sync) worker.

    Each call creates a short-lived event loop because Celery prefork
    workers have no asyncio loop installed. Failures are logged but
    never propagate — the scrape must not abort because of a UI push.
    """
    try:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(asyncio.to_thread(publisher.publish, task_id, payload))
        finally:
            loop.close()
    except Exception as exc:
        logger.warning("WebSocket push failed for %s: %s", task_id, exc)


def _compute_progress(page_count: int, expected_pages: int | None) -> int:
    if expected_pages and expected_pages > 0:
        ratio = min(1.0, page_count / expected_pages)
    elif page_count > 0:
        # Conservative fallback when the API never reports totalPage:
        # ~5% per captured page so the bar stays accurate even on
        # 50+ page tasks. Caps at 95% (done = 100% is reserved for the
        # terminal "task complete" write in scrape_tmall).
        ratio = min(1.0, page_count * 0.05)
    else:
        ratio = 0.0
    return min(95, int(round(5 + 90 * ratio)))


def _build_on_batch_callback(
    task_id: str,
    item_id: str,
    item_url: str,
) -> "callable":
    """Return an ``on_batch`` hook that incrementally persists and pushes.

    Persists only the most recent batch to keep DB writes cheap, then
    recomputes the task's progress fields and pushes a ``progress`` WS
    frame. The collector reference is supplied by ``scrape_reviews`` on
    every invocation so the callback never has to know the closure
    scaffolding.
    """
    def _on_batch(batch: ReviewBatch, collector: ReviewCollector) -> None:
        try:
            collector.persist_to_database(
                item_id, source_url=item_url, only_last_batch=True
            )
        except Exception as exc:
            logger.warning("on_batch: persist failed (%s)", exc)

        page_count = len(collector.captured_pages) or collector.batch_count
        expected_pages = batch.total_pages
        progress = _compute_progress(page_count, expected_pages)

        review_count = 0
        session = get_session()
        try:
            t = session.query(ScrapeTask).filter(ScrapeTask.task_id == task_id).first()
            if t is not None:
                t.review_count = session.query(Review).filter(Review.task_id == task_id).count()
                t.page_count = page_count
                if expected_pages and (not t.expected_pages or expected_pages > t.expected_pages):
                    t.expected_pages = expected_pages
                t.progress = progress
                session.commit()
                review_count = t.review_count
        except Exception as exc:
            logger.warning("on_batch: task-row update failed (%s)", exc)
        finally:
            session.close()

        cache_delete(f"task:{task_id}")
        _invalidate_task_cache(task_id)

        _send_ws_update(task_id, {
            "type": "progress",
            "task_id": task_id,
            "status": "running",
            "progress": progress,
            "review_count": review_count,
            "page_count": page_count,
            "expected_pages": expected_pages,
        })

    return _on_batch


@shared_task(name="review_scraper.workers.tasks.scrape_tmall")
def scrape_tmall(task_id: str, url: str, max_pages: int = 0, sort: str = "default", headless: bool = True, browser: str = "chrome", scan_all_responses: bool = False) -> dict:
    """Run a Tmall review scrape as a Celery task.

    Updates the ScrapeTask row and pushes WebSocket updates as it progresses.
    Returns a summary dict.
    """
    db = get_session()
    start_time = None
    try:
        task = db.query(ScrapeTask).filter(ScrapeTask.task_id == task_id).first()
        if not task:
            return {"task_id": task_id, "ok": False, "error": "Task not found"}

        task.status = "running"
        task.progress = 5
        task.started_at = datetime.now(timezone.utc)
        db.commit()
        session = get_session()
        try:
            try:
                session.add(TaskLog(task_id=task_id, level="info", stage="start", message="Task started", extra_json={"url": url}))
                session.commit()
            except Exception as exc:
                logger.warning("task log insert failed (%s)", exc)
                session.rollback()
        finally:
            session.close()
        cache_delete(f"task:{task_id}")

        _send_ws_update(task_id, {
            "type": "status",
            "task_id": task_id,
            "status": "running",
            "progress": 5,
            "review_count": 0,
            "page_count": 0,
        })

        import time
        start_time = time.time()

        item_id_for_callback = extract_item_id(url)
        on_batch_cb = _build_on_batch_callback(task_id, item_id_for_callback, url)

        rows, batches = scrape_reviews(
            item_url=url,
            browser=browser,
            profile_dir=settings.tmall_profile_dir,
            headless=headless,
            sort=sort,
            max_pages=max_pages,
            scan_all_responses=scan_all_responses,
            task_id=task_id,
            on_batch=on_batch_cb,
        )

        output_dir = _resolve_output_dir()
        item_id = item_id_for_callback
        csv_path, xlsx_path, json_path = export_reviews(rows, batches, output_dir, item_id)

        task.status = "done"
        task.progress = 100
        task.review_count = len(rows)
        task.page_count = len(batches)
        task.file_paths = json.dumps({
            "csv_path": str(csv_path),
            "xlsx_path": str(xlsx_path),
            "json_path": str(json_path),
        })
        task.completed_at = datetime.now(timezone.utc)
        db.commit()

        _send_ws_update(task_id, {
            "type": "status",
            "task_id": task_id,
            "status": "done",
            "progress": 100,
            "review_count": len(rows),
            "page_count": len(batches),
        })

        from review_scraper.core.metrics import (
            scrape_task_duration_seconds,
            scrape_task_reviews_collected,
        )
        duration = time.time() - start_time if start_time else 0
        scrape_tasks_completed.labels(source="tmall", status="done").inc()
        scrape_task_duration_seconds.labels(source="tmall").observe(duration)
        scrape_task_reviews_collected.labels(source="tmall").observe(len(rows))

        _invalidate_task_cache(task_id)

        return {
            "task_id": task_id,
            "item_id": item_id,
            "ok": True,
            "review_count": len(rows),
            "page_count": len(batches),
            "csv_path": str(csv_path),
            "xlsx_path": str(xlsx_path),
            "json_path": str(json_path),
        }

    except Exception as exc:
        task = db.query(ScrapeTask).filter(ScrapeTask.task_id == task_id).first()
        artifact_paths = _write_failure_artifacts(task_id, stage="scrape", exc=exc, context={"url": url})
        if task:
            task.status = "failed"
            task.progress = 100
            task.error_message = f"{type(exc).__name__}: {exc}"
            task.file_paths = json.dumps({**artifact_paths}, ensure_ascii=False)
            task.completed_at = datetime.now(timezone.utc)
            db.commit()
            _invalidate_task_cache(task_id)

            session = get_session()
            try:
                try:
                    session.add(TaskLog(
                        task_id=task_id,
                        level="error",
                        stage="scrape",
                        message=task.error_message,
                        extra_json={"url": url, **artifact_paths},
                    ))
                    session.commit()
                except Exception as log_exc:
                    logger.warning("task error log insert failed (%s)", log_exc)
                    session.rollback()
            finally:
                session.close()

            _send_ws_update(task_id, {
                "type": "status",
                "task_id": task_id,
                "status": "failed",
                "progress": 100,
                "error": task.error_message,
                "artifacts": artifact_paths,
            })

            scrape_tasks_completed.labels(source="tmall", status="failed").inc()

        return {"task_id": task_id, "ok": False, "error": f"{type(exc).__name__}: {exc}", "artifacts": artifact_paths}
    finally:
        # The dispatch endpoint (api/routes/reviews.py) increments the
        # active counter when the task is queued. We must decrement on
        # every terminal state (done, failed, or unexpected error) so the
        # Prometheus gauge does not grow unboundedly.
        try:
            active_scrape_tasks.labels(source="tmall").dec()
        except Exception as metric_exc:
            logger.warning("active_scrape_tasks dec failed (%s)", metric_exc)
        db.close()
