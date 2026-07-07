"""Analysis API endpoints.

Reads reviews from the database (not from exported JSON files) so analysis
works as soon as a scrape task finishes, even if exports were not generated.
"""

from __future__ import annotations

import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from review_scraper.api.routes.reviews import get_db
from review_scraper.core.cache import cache_get, cache_set
from review_scraper.core.config import get_settings
from review_scraper.models import Review, ReviewAnalysisResult, ScrapeTask, ScrapeTaskReview, TaskArtifact
from review_scraper.modules.tmall_reviews.analysis import analyze_sentiment, extract_keywords, get_review_statistics
from review_scraper.modules.tmall_reviews.report import build_markdown_report, build_pdf_report

router = APIRouter(tags=["analysis"])
settings = get_settings()


class AnalysisResult(BaseModel):
    keywords: list[tuple[str, float]]
    sentiment: dict[str, Any]
    statistics: dict[str, Any]


class AnalysisInsights(BaseModel):
    highlights: list[str]
    pain_points: list[str]
    action_items: list[str]
    representative_quotes: list[str]


class AnalysisBundle(BaseModel):
    task_id: str
    keywords: list[tuple[str, float]]
    sentiment: dict[str, Any]
    statistics: dict[str, Any]
    insights: AnalysisInsights | None = None


_PAIN_POINT_RULES = {
    "质量问题": ["质量", "做工", "材质", "破", "坏", "掉漆"],
    "物流问题": ["物流", "快递", "送货", "发货", "到货", "运输"],
    "包装问题": ["包装", "盒子", "破损", "漏", "脏"],
    "客服问题": ["客服", "售后", "回复", "态度"],
    "价格问题": ["价格", "贵", "便宜", "性价比"],
    "尺寸问题": ["尺寸", "大小", "偏大", "偏小", "不合适"],
    "安装问题": ["安装", "组装", "不会装", "麻烦"],
    "异味问题": ["味道", "异味", "臭", "刺鼻"],
    "噪音问题": ["噪音", "声音", "很响", "吵"],
    "耐用性问题": ["耐用", "寿命", "易坏", "坏了"],
    "虚假宣传": ["宣传", "不符", "夸大", "骗人"],
    "使用体验问题": ["难用", "不好用", "卡", "不顺手", "体验"],
}


class SKUItem(BaseModel):
    sku: str
    review_count: int
    negative_count: int
    negative_rate: float
    top_keywords: list[str]
    main_issue: str


class PainPointItem(BaseModel):
    name: str
    category: str
    count: int
    severity: str
    quotes: list[str]
    suggestion: str


class QuoteBundle(BaseModel):
    positive_quotes: list[str]
    negative_quotes: list[str]
    sku_quotes: list[str]


class TimelineItem(BaseModel):
    date: str
    review_count: int
    negative_count: int
    negative_rate: float


class TaskOverviewBundle(BaseModel):
    task_id: str
    product_id: str | None
    total_reviews: int
    positive_count: int
    neutral_count: int
    negative_count: int
    image_review_count: int
    append_review_count: int
    top_keywords: list[tuple[str, float]]
    summary: str
    sku_items: list[SKUItem]
    pain_points: list[PainPointItem]
    quotes: QuoteBundle
    timeline: list[TimelineItem]


def _build_insights(texts: list[str]) -> AnalysisInsights:
    samples = [text.strip() for text in texts if text.strip()][:5]
    return AnalysisInsights(
        highlights=["评论分析已生成，适合进一步做人群与卖点拆解。"],
        pain_points=["当前仅做基础规则分析，后续可接入更强的分类器。"],
        action_items=["观察低分评论的共性问题", "抽取高频卖点用于详情页优化"],
        representative_quotes=samples,
    )


def _load_reviews_for_task(task_id: str, db: Session) -> list[dict[str, Any]]:
    rows = (
        db.query(Review)
        .join(ScrapeTaskReview, ScrapeTaskReview.review_id == Review.id)
        .filter(ScrapeTaskReview.task_id == task_id)
        .order_by(Review.id.asc())
        .all()
    )
    return [
        {
            "content": r.content or "",
            "append_content": r.append_content or "",
            "rating": float(r.rating) if r.rating is not None else None,
            "sku": r.sku or "",
            "picture_urls": r.picture_urls or "",
            "review_date": r.review_date or "",
        }
        for r in rows
    ]


def _score_sentiment_text(text: str) -> int:
    positive = ["好", "不错", "满意", "喜欢", "推荐", "值", "棒", "优秀", "great", "good"]
    negative = ["差", "不好", "失望", "垃圾", "后悔", "糟糕", "问题", "坏", "bad", "poor"]
    score = 0
    lower = text.lower()
    score += sum(1 for kw in positive if kw in lower)
    score -= sum(1 for kw in negative if kw in lower)
    return score


def _build_overview(task_id: str, reviews: list[dict[str, Any]]) -> dict[str, Any]:
    texts = [r["content"] for r in reviews if r["content"]]
    sentiment = analyze_sentiment(texts)
    stats = get_review_statistics(reviews)
    return {
        "task_id": task_id,
        "product_id": None,
        "total_reviews": stats["total_reviews"],
        "positive_count": sentiment["positive"],
        "neutral_count": sentiment["neutral"],
        "negative_count": sentiment["negative"],
        "image_review_count": stats.get("has_images", 0),
        "append_review_count": stats.get("has_append", 0),
        "top_keywords": extract_keywords(texts, top_n=10),
        "summary": "; ".join(_build_insights(texts).action_items),
    }


def _build_sku_items(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for review in reviews:
        buckets[review.get("sku") or "未知 SKU"].append(review)

    items: list[dict[str, Any]] = []
    for sku, bucket in buckets.items():
        texts = [r["content"] for r in bucket if r["content"]]
        negative_count = sum(1 for r in bucket if _score_sentiment_text(r.get("content", "")) < 0)
        items.append(
            {
                "sku": sku,
                "review_count": len(bucket),
                "negative_count": negative_count,
                "negative_rate": round(negative_count / len(bucket), 3) if bucket else 0.0,
                "top_keywords": [k for k, _ in extract_keywords(texts, top_n=3)],
                "main_issue": "、".join({k for k, v in _PAIN_POINT_RULES.items() if any(any(token in text for token in v) for text in texts)}) or "综合体验问题",
            }
        )
    return sorted(items, key=lambda x: (x["negative_rate"], x["review_count"]), reverse=True)


def _build_pain_points(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[str]] = defaultdict(list)
    for review in reviews:
        text = review.get("content", "")
        for category, tokens in _PAIN_POINT_RULES.items():
            if any(token in text for token in tokens):
                buckets[category].append(text)
    items = []
    for category, quotes in buckets.items():
        severity = "high" if len(quotes) >= 5 else "medium" if len(quotes) >= 2 else "low"
        items.append(
            {
                "name": category,
                "category": category,
                "count": len(quotes),
                "severity": severity,
                "quotes": quotes[:3],
                "suggestion": f"针对{category}做页面/流程/售后优化。",
            }
        )
    return sorted(items, key=lambda x: x["count"], reverse=True)


def _build_quotes(reviews: list[dict[str, Any]]) -> dict[str, list[str]]:
    positives = []
    negatives = []
    sku_quotes = []
    for review in reviews:
        text = review.get("content", "")
        if not text:
            continue
        score = _score_sentiment_text(text)
        if score > 0 and len(positives) < 5:
            positives.append(text)
        elif score < 0 and len(negatives) < 5:
            negatives.append(text)
        if review.get("sku") and len(sku_quotes) < 5:
            sku_quotes.append(f"{review['sku']}: {text}")
    return {"positive_quotes": positives, "negative_quotes": negatives, "sku_quotes": sku_quotes}


def _build_timeline(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for review in reviews:
        date = (review.get("review_date") or "")[:10] or "unknown"
        buckets[date].append(review)
    items = []
    for date, bucket in sorted(buckets.items()):
        negative = sum(1 for r in bucket if _score_sentiment_text(r.get("content", "")) < 0)
        items.append({"date": date, "review_count": len(bucket), "negative_count": negative, "negative_rate": round(negative / len(bucket), 3) if bucket else 0.0})
    return items


@router.get("/analysis/tasks/{task_id}", response_model=AnalysisBundle, summary="全量分析任务评论")
async def analyze_task_reviews(task_id: str, db: Session = Depends(get_db)) -> AnalysisBundle:
    start_time = time.time()
    cache_key = f"analysis:{task_id}"
    cached = cache_get(cache_key)
    if cached:
        return AnalysisBundle(**cached)

    task = db.query(ScrapeTask).filter(ScrapeTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != "done":
        raise HTTPException(status_code=400, detail="Task not completed yet")

    reviews = _load_reviews_for_task(task_id, db)
    if not reviews:
        raise HTTPException(status_code=400, detail="No reviews to analyze")

    texts = [r["content"] for r in reviews if r["content"]]
    keywords = extract_keywords(texts, top_n=20)
    sentiment = analyze_sentiment(texts)
    statistics = get_review_statistics(reviews)
    insights = _build_insights(texts)

    result = AnalysisBundle(task_id=task_id, keywords=keywords, sentiment=sentiment, statistics=statistics, insights=insights)
    db_result = ReviewAnalysisResult(task_id=task_id, summary_json={"task_id": task_id, "statistics": statistics}, keywords_json={"keywords": keywords}, sentiment_json=sentiment, insights_json=insights.model_dump())
    db.add(db_result)
    db.commit()
    cache_set(cache_key, result.model_dump(), ttl=3600)

    duration = time.time() - start_time
    from review_scraper.core.metrics import analysis_operations_total, analysis_duration_seconds
    analysis_operations_total.labels(type="full_analysis").inc()
    analysis_duration_seconds.labels(type="full_analysis").observe(duration)
    return result


@router.get("/analysis/tasks/{task_id}/overview", response_model=TaskOverviewBundle, summary="任务分析总览")
async def analysis_overview(task_id: str, db: Session = Depends(get_db)) -> TaskOverviewBundle:
    task = db.query(ScrapeTask).filter(ScrapeTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    reviews = _load_reviews_for_task(task_id, db)
    overview = _build_overview(task_id, reviews)
    return TaskOverviewBundle(task_id=task_id, product_id=task.product_id, total_reviews=overview["total_reviews"], positive_count=overview["positive_count"], neutral_count=overview["neutral_count"], negative_count=overview["negative_count"], image_review_count=overview["image_review_count"], append_review_count=overview["append_review_count"], top_keywords=overview["top_keywords"], summary=overview["summary"], sku_items=_build_sku_items(reviews), pain_points=_build_pain_points(reviews), quotes=_build_quotes(reviews), timeline=_build_timeline(reviews))


@router.get("/analysis/tasks/{task_id}/sku", summary="SKU 分析")
async def analysis_sku(task_id: str, db: Session = Depends(get_db)) -> dict:
    return {"items": _build_sku_items(_load_reviews_for_task(task_id, db))}


@router.get("/analysis/tasks/{task_id}/pain-points", summary="痛点分析")
async def analysis_pain_points(task_id: str, db: Session = Depends(get_db)) -> dict:
    return {"items": _build_pain_points(_load_reviews_for_task(task_id, db))}


@router.get("/analysis/tasks/{task_id}/quotes", summary="代表评论")
async def analysis_quotes(task_id: str, db: Session = Depends(get_db)) -> dict:
    return _build_quotes(_load_reviews_for_task(task_id, db))


@router.get("/analysis/tasks/{task_id}/timeline", summary="趋势分析")
async def analysis_timeline(task_id: str, db: Session = Depends(get_db)) -> dict:
    return {"items": _build_timeline(_load_reviews_for_task(task_id, db))}


@router.get("/analysis/tasks/{task_id}/report.md", summary="导出 Markdown 报告")
async def export_analysis_markdown(task_id: str, db: Session = Depends(get_db)):
    task = db.query(ScrapeTask).filter(ScrapeTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    payload = await analysis_overview(task_id, db)
    markdown = build_markdown_report(payload.model_dump())
    output_dir = Path(settings.review_export_dir).parent / "reports" / task_id
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "analysis_report.md"
    path.write_text(markdown, encoding="utf-8")
    db.add(TaskArtifact(task_id=task_id, artifact_type="analysis_report_md", file_path=str(path), file_name=path.name, mime_type="text/markdown", size_bytes=path.stat().st_size))
    db.commit()
    return FileResponse(path, media_type="text/markdown", filename=path.name)


@router.get("/analysis/tasks/{task_id}/report.pdf", summary="导出 PDF 报告")
async def export_analysis_pdf(task_id: str, db: Session = Depends(get_db)):
    task = db.query(ScrapeTask).filter(ScrapeTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    payload = await analysis_overview(task_id, db)
    pdf_bytes = build_pdf_report(payload.model_dump())
    output_dir = Path(settings.review_export_dir).parent / "reports" / task_id
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "analysis_report.pdf"
    path.write_bytes(pdf_bytes)
    db.add(TaskArtifact(task_id=task_id, artifact_type="analysis_report_pdf", file_path=str(path), file_name=path.name, mime_type="application/pdf", size_bytes=path.stat().st_size))
    db.commit()
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{path.name}"'})


@router.get("/analysis/tasks/{task_id}/keywords", summary="获取关键词")
async def get_task_keywords(task_id: str, top_n: int = Query(20, ge=1, le=100, description="返回的关键词数量"), db: Session = Depends(get_db)) -> dict:
    analysis = await analyze_task_reviews(task_id, db)
    return {"keywords": analysis.keywords[:top_n]}


@router.get("/analysis/tasks/{task_id}/sentiment", summary="获取情感分析")
async def get_task_sentiment(task_id: str, db: Session = Depends(get_db)) -> dict:
    analysis = await analyze_task_reviews(task_id, db)
    return {"sentiment": analysis.sentiment}
