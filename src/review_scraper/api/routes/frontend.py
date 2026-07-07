"""Frontend page routes and overview API for the review scraping platform."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.requests import Request

from review_scraper.api.routes.reviews import get_db
from review_scraper.core.config import get_settings
from review_scraper.models import Review, ScrapeTask

router = APIRouter(tags=["frontend"])
settings = get_settings()

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "web" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "pages/dashboard_new.html.j2")


@router.get("/reviews", response_class=HTMLResponse, include_in_schema=False)
def reviews_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "pages/reviews_new.html.j2")


@router.get("/products", response_class=HTMLResponse, include_in_schema=False)
def products_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "pages/products.html.j2")


@router.get("/tasks", response_class=HTMLResponse, include_in_schema=False)
def tasks_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "pages/tasks_new.html.j2")


@router.get("/tasks/{task_id}", response_class=HTMLResponse, include_in_schema=False)
def task_detail_page(request: Request, task_id: str) -> HTMLResponse:
    return templates.TemplateResponse(request, "pages/task_detail.html.j2", {"request": request, "task_id": task_id})


@router.get("/analysis", response_class=HTMLResponse, include_in_schema=False)
def analysis_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "pages/analysis_new.html.j2")


@router.get("/api/frontend/overview")
def frontend_overview(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Overview stats for the dashboard, sourced from the database."""
    total_tasks = db.query(ScrapeTask).count()
    done_tasks = db.query(ScrapeTask).filter(ScrapeTask.status == "done").count()
    failed_tasks = db.query(ScrapeTask).filter(ScrapeTask.status == "failed").count()
    running_tasks = db.query(ScrapeTask).filter(ScrapeTask.status == "running").count()
    total_reviews = db.query(Review).count()

    return {
        "title": "电商评论抓取与分析系统",
        "subtitle": "采集 · 入库 · 分析 · 导出",
        "stats": {
            "total_tasks": total_tasks,
            "done_tasks": done_tasks,
            "failed_tasks": failed_tasks,
            "running_tasks": running_tasks,
            "total_reviews": total_reviews,
        },
        "modules": [
            {
                "name": "评论采集层",
                "status": "ready",
                "desc": "Playwright 驱动天猫/淘宝评论抓取，JSONP 解析、翻页、去重入库。",
            },
            {
                "name": "任务调度层",
                "status": "ready",
                "desc": "Celery + Redis 异步任务队列，支持重试、超时、进度推送。",
            },
            {
                "name": "数据存储层",
                "status": "ready",
                "desc": "评论、批次、任务全量入库，唯一约束防重，raw_payload 可追溯。",
            },
            {
                "name": "分析层",
                "status": "ready",
                "desc": "关键词提取、情感分析、评分统计、痛点洞察。",
            },
            {
                "name": "导出层",
                "status": "ready",
                "desc": "CSV / XLSX / JSON 多格式导出，按任务下载。",
            },
        ],
        "pipeline_steps": [
            "1. 提交商品 URL 创建抓取任务",
            "2. Celery Worker 启动 Playwright 抓取评论",
            "3. 评论与原始批次入库",
            "4. WebSocket 实时推送进度",
            "5. 任务完成后触发分析与导出",
            "6. 前端看板展示统计与洞察",
        ],
    }
