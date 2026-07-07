"""Tmall review scraping API endpoints.

Key fixes vs. the previous version:
- Removed the duplicate ``GET /reviews/tmall/tasks/{task_id}/reviews`` route.
  It is now split into ``/reviews`` (paginated list), ``/preview`` (preview
  bundle), and ``/batches`` (raw captured batches).
- Scrape jobs are dispatched to Celery instead of ``asyncio.create_task`` so
  they survive worker restarts and can be retried / rate-limited centrally.
- The target URL is validated against a platform whitelist before any browser
  is launched.
- Review pagination reads from the ``reviews`` table, not from exported JSON
  files.
"""

from __future__ import annotations

import ipaddress
import json
import logging
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from review_scraper.core.cache import cache_delete, cache_get, cache_set
from review_scraper.core.config import get_settings
from review_scraper.core.database import get_session
from review_scraper.core.websocket import get_connection_manager
from review_scraper.models import CrawlBatch, Product, Review, ReviewAnalysisResult, ScrapeTask, ScrapeTaskReview, TaskArtifact, TaskLog
from review_scraper.modules.tmall_reviews.parser import extract_item_id

router = APIRouter(tags=["reviews"])
settings = get_settings()
manager = get_connection_manager()
logger = logging.getLogger(__name__)


def get_db():
    """Dependency to get database session."""
    db = get_session()
    try:
        yield db
    finally:
        db.close()


# Cache keys for the task list endpoint. Centralized so worker + API
# invalidate exactly the same set of keys instead of guessing strings.
TASK_LIST_CACHE_LIMITS: tuple[int, ...] = (10, 20, 50, 100)


def invalidate_task_cache(task_id: str | None = None) -> None:
    """Invalidate cached task list/detail entries.

    Centralizes the cache key conventions so the worker and API cannot
    drift (the previous implementation cleared ``tasks:list`` while the
    writer stored ``tasks:list:20``/``tasks:list:50``/...).
    """
    if task_id:
        cache_delete(f"task:{task_id}")
    for limit in TASK_LIST_CACHE_LIMITS:
        cache_delete(f"tasks:list:{limit}")


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class TmallScrapeRequest(BaseModel):
    url: str
    max_pages: int = Field(0, ge=0, le=500)
    sort: str = Field(default="default", pattern="^(default|latest)$")
    headless: bool = True
    browser: str = Field(default="chrome", pattern="^(chrome|edge)$")
    scan_all_responses: bool = False
    retry_failed: bool = False


class TaskInfo(BaseModel):
    task_id: str
    status: TaskStatus
    item_id: str | None = None
    name: str | None = None
    task_type: str = "review_scrape"
    progress: int = 0
    review_count: int = 0
    page_count: int = 0
    expected_pages: int | None = None
    error: str | None = None
    artifacts: dict[str, str] | None = None
    created_at: str | None = None
    completed_at: str | None = None


class TaskActionResult(BaseModel):
    ok: bool
    task_id: str
    new_task_id: str | None = None
    message: str


class ReviewPage(BaseModel):
    task_id: str
    review_count: int
    limit: int
    offset: int
    has_more: bool
    reviews: list[dict[str, Any]]


class ReviewPreview(BaseModel):
    task_id: str
    review_count: int
    page_count: int
    columns: list[str]
    reviews: list[dict[str, Any]]


class BatchList(BaseModel):
    task_id: str
    batch_count: int
    batches: list[dict[str, Any]]


def _allowed_hosts() -> set[str]:
    return {h.strip().lower().strip(".") for h in settings.allowed_hosts.split(",") if h.strip()}


def _is_private_host(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def _is_allowed_host(host: str) -> bool:
    host = host.lower().strip(".")
    if _is_private_host(host):
        return False
    allowed = _allowed_hosts()
    for allowed_host in allowed:
        if host == allowed_host or host.endswith("." + allowed_host):
            return True
    return False


def _validate_url(url: str) -> str:
    """Reject URLs whose host is not in the platform whitelist."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="Invalid URL scheme")
    host = (parsed.hostname or "").lower()
    if not host:
        raise HTTPException(status_code=400, detail="Invalid URL: missing host")
    if not _is_allowed_host(host):
        allowed = sorted(_allowed_hosts())
        raise HTTPException(
            status_code=400,
            detail=f"URL host '{host}' is not in the allowed list: {allowed}",
        )
    return url


def _resolve_output_dir() -> Path:
    configured = Path(settings.review_export_dir)
    if configured.is_absolute():
        return configured
    return Path.cwd() / configured


def _review_to_dict(review: Review) -> dict[str, Any]:
    return {
        "review_id": review.review_id,
        "user_nick": review.user_nick,
        "review_date": review.review_date,
        "sku": review.sku,
        "rating": float(review.rating) if review.rating is not None else None,
        "content": review.content,
        "append_content": review.append_content,
        "append_date": review.append_date,
        "helpful_count": review.helpful_count or 0,
        "page_number": review.page_number,
        "total_pages": review.total_pages,
        "picture_urls": review.picture_urls,
        "source_url": review.source_url,
        "platform": review.platform,
    }


def _task_to_info(task: ScrapeTask) -> TaskInfo:
    artifacts = None
    if task.file_paths:
        try:
            artifacts = json.loads(task.file_paths)
        except Exception:
            artifacts = None
    return TaskInfo(
        task_id=task.task_id,
        status=task.status,
        item_id=task.product_id,
        name=task.name,
        task_type=task.task_type,
        progress=task.progress or 0,
        review_count=task.review_count or 0,
        page_count=task.page_count or 0,
        expected_pages=task.expected_pages,
        error=task.error_message,
        artifacts=artifacts,
        created_at=task.created_at.isoformat() if task.created_at else None,
        completed_at=task.completed_at.isoformat() if task.completed_at else None,
    )


def _dispatch_scrape(task_id: str, payload: TmallScrapeRequest) -> None:
    """Send the scrape job to Celery instead of running it in-process.

    Raises whatever Celery raises (e.g. ``RuntimeError`` when the Redis broker
    or result backend is unreachable) so the caller can mark the just-created
    ``ScrapeTask`` row as ``failed`` instead of leaving it stranded at
    ``status="queued"`` forever.
    """
    from review_scraper.workers.celery_app import celery_app

    celery_app.send_task(
        "review_scraper.workers.tasks.scrape_tmall",
        kwargs={
            "task_id": task_id,
            "url": payload.url,
            "max_pages": payload.max_pages,
            "sort": payload.sort,
            "headless": payload.headless,
            "browser": payload.browser,
            "scan_all_responses": payload.scan_all_responses,
        },
    )


def _fail_task(db: Session, task: ScrapeTask, exc: Exception) -> None:
    """Mark a freshly-created task as failed and commit the change."""
    task.status = "failed"
    task.progress = 100
    task.error_message = f"{type(exc).__name__}: {exc}"
    task.completed_at = datetime.now(timezone.utc)
    db.commit()
    invalidate_task_cache(task.task_id)


@router.post("/reviews/tmall/scrape", response_model=TaskInfo, summary="创建天猫评论抓取任务")
async def start_tmall_scrape(payload: TmallScrapeRequest, db: Session = Depends(get_db)) -> TaskInfo:
    """
    创建一个新的天猫评论抓取任务。

    - **url**: 天猫商品详情页URL (必填，必须命中平台白名单)
    - **max_pages**: 抓取的最大页数，0表示全部抓取 (默认: 0)
    - **sort**: 排序方式 - default/latest (默认: default)
    - **headless**: 是否使用无头浏览器 (默认: true)
    - **browser**: 本地调试浏览器 - chrome/edge (默认: chrome，Linux/Docker 自动用 Playwright Chromium)
    - **scan_all_responses**: 是否扫描所有网络响应 (默认: false)

    任务通过 Celery 异步执行，返回 task_id 用于后续查询。
    """
    _validate_url(payload.url)
    task_id = str(uuid4())
    try:
        item_id = extract_item_id(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    product = (
        db.query(Product)
        .filter(
            Product.platform == "tmall",
            Product.external_product_id == item_id,
        )
        .first()
    )
    if product is None:
        product = Product(
            platform="tmall",
            source_url=payload.url,
            normalized_url=payload.url,
            external_product_id=item_id,
        )
        db.add(product)
        db.flush()

    task = ScrapeTask(
        task_id=task_id,
        product_id=product.id,
        platform="tmall",
        task_type="review_scrape",
        name=f"天猫评论抓取 {item_id}",
        status="queued",
        source_url=payload.url,
        task_params={**payload.model_dump(), "platform": "tmall", "external_product_id": item_id},
        progress=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    from review_scraper.core.metrics import scrape_tasks_total, active_scrape_tasks
    scrape_tasks_total.labels(source="tmall").inc()
    active_scrape_tasks.labels(source="tmall").inc()

    try:
        _dispatch_scrape(task_id, payload)
    except Exception as exc:
        # Dispatch failure (Redis down, broker unreachable, Celery worker
        # missing, result backend broken) must not leave the task row stranded
        # at status="queued". Mark it failed with the real error, decrement the
        # active counter, and surface a clear 503 to the caller.
        _fail_task(db, task, exc)
        active_scrape_tasks.labels(source="tmall").dec()
        from review_scraper.core.metrics import scrape_tasks_completed
        scrape_tasks_completed.labels(source="tmall", status="failed").inc()
        logger.error("Failed to dispatch scrape task %s: %s", task_id, exc, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=(
                f"Task {task_id} created in database but could not be dispatched "
                f"to the worker ({type(exc).__name__}: {exc}). "
                "Make sure Redis and the Celery worker are running, then retry."
            ),
        ) from exc

    return _task_to_info(task)


@router.get("/reviews/tmall/tasks/{task_id}", response_model=TaskInfo, summary="查询任务状态")
async def get_task_status(task_id: str, db: Session = Depends(get_db)) -> TaskInfo:
    """查询指定任务的当前状态。"""
    cache_key = f"task:{task_id}"
    cached = cache_get(cache_key)
    if cached:
        return TaskInfo(**cached)

    task = db.query(ScrapeTask).filter(ScrapeTask.task_id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    result = _task_to_info(task)
    ttl = 3600 if task.status in ("done", "failed") else 10
    cache_set(cache_key, result.model_dump(), ttl=ttl)
    return result


@router.get(
    "/reviews/tmall/tasks/{task_id}/reviews",
    response_model=ReviewPage,
    summary="分页获取评论明细 (从数据库读取)",
)
async def get_task_reviews(
    task_id: str,
    limit: int = Query(20, ge=1, le=200, description="每页返回数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    db: Session = Depends(get_db),
) -> ReviewPage:
    """
    分页获取指定任务的评论明细。

    数据直接从 reviews 表读取，不再依赖导出的 JSON 文件。
    """
    task = db.query(ScrapeTask).filter(ScrapeTask.task_id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    query = (
        db.query(Review)
        .join(ScrapeTaskReview, ScrapeTaskReview.review_id == Review.id)
        .filter(ScrapeTaskReview.task_id == task_id)
    )
    total = query.count()
    rows = (
        query.order_by(Review.id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return ReviewPage(
        task_id=task_id,
        review_count=total,
        limit=limit,
        offset=offset,
        has_more=offset + limit < total,
        reviews=[_review_to_dict(r) for r in rows],
    )


@router.get(
    "/reviews/tmall/tasks/{task_id}/preview",
    response_model=ReviewPreview,
    summary="获取任务评论预览",
)
async def get_task_reviews_preview(
    task_id: str,
    limit: int = Query(20, ge=1, le=200, description="返回评论条数"),
    offset: int = Query(0, ge=0, description="评论偏移量"),
    db: Session = Depends(get_db),
) -> ReviewPreview:
    """获取任务评论的预览快照（前 N 条），用于结果页快速展示。"""
    task = db.query(ScrapeTask).filter(ScrapeTask.task_id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != "done":
        raise HTTPException(status_code=400, detail=f"Task is {task.status}, not done")

    query = (
        db.query(Review)
        .join(ScrapeTaskReview, ScrapeTaskReview.review_id == Review.id)
        .filter(ScrapeTaskReview.task_id == task_id)
    )
    total = query.count()
    rows = query.order_by(Review.id.asc()).offset(offset).limit(limit).all()

    columns = [
        "review_id", "user_nick", "review_date", "sku", "rating", "content",
        "append_content", "append_date", "helpful_count",
        "page_number", "total_pages", "picture_urls", "source_url", "platform",
    ]
    return ReviewPreview(
        task_id=task_id,
        review_count=total,
        page_count=task.page_count or 0,
        columns=columns,
        reviews=[_review_to_dict(r) for r in rows],
    )


@router.get(
    "/reviews/tmall/tasks/{task_id}/batches",
    response_model=BatchList,
    summary="获取任务的原始抓取批次",
)
async def get_task_batches(
    task_id: str,
    db: Session = Depends(get_db),
) -> BatchList:
    """返回该任务抓取到的每一页/每一个接口响应的批次记录（不含 raw_payload）。"""
    task = db.query(ScrapeTask).filter(ScrapeTask.task_id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    batches = (
        db.query(CrawlBatch)
        .filter(CrawlBatch.task_id == task_id)
        .order_by(CrawlBatch.id.asc())
        .all()
    )
    return BatchList(
        task_id=task_id,
        batch_count=len(batches),
        batches=[
            {
                "id": b.id,
                "page_number": b.page_number,
                "total_pages": b.total_pages,
                "review_count": b.review_count,
                "source_url": b.source_url,
                "captured_at": b.captured_at.isoformat() if b.captured_at else None,
            }
            for b in batches
        ],
    )


@router.post("/reviews/tmall/tasks/{task_id}/retry", response_model=TaskActionResult, summary="重试失败任务")
async def retry_task(task_id: str, db: Session = Depends(get_db)) -> TaskActionResult:
    task = db.query(ScrapeTask).filter(ScrapeTask.task_id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in {"failed", "done"}:
        raise HTTPException(status_code=400, detail="Only finished tasks can be retried")
    if not task.source_url:
        raise HTTPException(status_code=400, detail="Missing source url")

    _validate_url(task.source_url)
    params = task.task_params or {}
    payload = TmallScrapeRequest(
        url=task.source_url,
        max_pages=params.get("max_pages", 0),
        sort=params.get("sort", "default"),
        headless=params.get("headless", True),
        browser=params.get("browser", "chrome"),
        scan_all_responses=params.get("scan_all_responses", False),
    )

    new_task_id = str(uuid4())
    new_task = ScrapeTask(
        task_id=new_task_id,
        product_id=task.product_id,
        platform="tmall",
        status="queued",
        source_url=task.source_url,
        task_params=payload.model_dump(),
        parent_task_id=task_id,
        started_at=datetime.now(timezone.utc),
    )
    db.add(new_task)
    db.commit()
    db.refresh(new_task)

    try:
        _dispatch_scrape(new_task_id, payload)
    except Exception as exc:
        _fail_task(db, new_task, exc)
        logger.error("Failed to dispatch retry task %s: %s", new_task_id, exc, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=(
                f"Retry task {new_task_id} created in database but could not be "
                f"dispatched to the worker ({type(exc).__name__}: {exc}). "
                "Make sure Redis and the Celery worker are running, then retry."
            ),
        ) from exc

    return TaskActionResult(ok=True, task_id=task_id, new_task_id=new_task_id, message="Retry queued")


@router.delete("/reviews/tmall/tasks/{task_id}", response_model=TaskActionResult, summary="删除任务记录")
async def delete_task(task_id: str, db: Session = Depends(get_db)) -> TaskActionResult:
    task = db.query(ScrapeTask).filter(ScrapeTask.task_id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    db.query(ScrapeTaskReview).filter(ScrapeTaskReview.task_id == task_id).delete(synchronize_session=False)
    db.query(CrawlBatch).filter(CrawlBatch.task_id == task_id).delete(synchronize_session=False)
    db.query(TaskLog).filter(TaskLog.task_id == task_id).delete(synchronize_session=False)
    db.query(TaskArtifact).filter(TaskArtifact.task_id == task_id).delete(synchronize_session=False)
    db.query(ReviewAnalysisResult).filter(ReviewAnalysisResult.task_id == task_id).delete(synchronize_session=False)
    db.delete(task)
    db.commit()
    invalidate_task_cache(task_id)
    return TaskActionResult(ok=True, task_id=task_id, message="Task deleted")


@router.get("/reviews/tmall/tasks/{task_id}/artifacts/{artifact_type}", summary="下载任务产物")
async def download_task_artifact(
    task_id: str,
    artifact_type: str,
    db: Session = Depends(get_db),
):
    """下载已完成任务的评论导出文件 (csv/xlsx/json)。"""
    task = db.query(ScrapeTask).filter(ScrapeTask.task_id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if not task.file_paths:
        raise HTTPException(status_code=404, detail="Result files not found")

    try:
        file_paths = json.loads(task.file_paths)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Invalid task file metadata: {exc}") from exc

    key_map = {
        "csv": ("csv_path", "text/csv"),
        "xlsx": ("xlsx_path", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        "json": ("json_path", "application/json"),
        "task_error": ("task_error", "application/json"),
        "error_html": ("error_html", "text/html"),
        "console_log": ("console_log", "text/plain"),
        "screenshot": ("screenshot", "image/png"),
    }
    if artifact_type not in key_map:
        raise HTTPException(status_code=400, detail="Unsupported artifact type")
    key, media_type = key_map[artifact_type]
    file_path = Path(file_paths.get(key, ""))
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"{artifact_type} file not found")

    return FileResponse(file_path, media_type=media_type, filename=file_path.name)


@router.get("/reviews/tmall/tasks/{task_id}/download", summary="[兼容] 按 format 参数下载任务产物")
async def download_task_artifact_legacy(
    task_id: str,
    format: str = Query("csv", description="导出格式: csv/xlsx/json"),
    db: Session = Depends(get_db),
):
    """Legacy download endpoint.

    Older clients (and parts of the current UI before the route rename)
    requested downloads via ``/download?format=csv``. Forward to the
    canonical ``/artifacts/{type}`` route so we keep backward compatibility
    without duplicating the FileResponse logic.
    """
    return await download_task_artifact(task_id, format, db)


@router.get("/reviews/tmall/tasks", summary="获取任务列表")
async def list_tasks(
    limit: int = Query(20, ge=1, le=100, description="返回的最大任务数量"),
    db: Session = Depends(get_db),
) -> list[TaskInfo]:
    """获取最近创建的任务列表，按创建时间倒序。"""
    if limit <= 20:
        cache_key = f"tasks:list:{limit}"
        cached = cache_get(cache_key)
        if cached:
            return [TaskInfo(**item) for item in cached]

    tasks = db.query(ScrapeTask).order_by(ScrapeTask.created_at.desc()).limit(limit).all()
    result = [_task_to_info(t) for t in tasks]

    if limit <= 20:
        cache_set(f"tasks:list:{limit}", [r.model_dump() for r in result], ttl=30)
    return result


@router.websocket("/reviews/tmall/tasks/{task_id}/ws")
async def websocket_endpoint(websocket: WebSocket, task_id: str) -> None:
    """WebSocket endpoint for real-time task updates."""
    await manager.connect(websocket, task_id)
    try:
        db = get_session()
        try:
            task = db.query(ScrapeTask).filter(ScrapeTask.task_id == task_id).first()
            if task:
                await websocket.send_json({
                    "type": "status",
                    "task_id": task_id,
                    "status": task.status,
                    "review_count": task.review_count or 0,
                    "page_count": task.page_count or 0,
                })
        finally:
            db.close()

        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
    finally:
        await manager.disconnect(websocket, task_id)
