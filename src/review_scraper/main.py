from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from prometheus_client import make_asgi_app
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import SQLAlchemyError

from review_scraper.api.middleware.error_handlers import (
    general_exception_handler,
    sqlalchemy_exception_handler,
)
from review_scraper.api.middleware.metrics import MetricsMiddleware
from review_scraper.api.middleware.rate_limit import get_limiter
from review_scraper.api.routes.analysis import router as analysis_router
from review_scraper.api.routes.frontend import router as frontend_router
from review_scraper.api.routes.products import router as products_router
from review_scraper.api.routes.reviews import router as reviews_router
from review_scraper.core.config import get_settings
from review_scraper.core.database import get_engine
from review_scraper.core.logging import setup_logging
from review_scraper.core.metrics import init_metrics

# Setup logging
setup_logging()

# Initialize metrics
init_metrics()

settings = get_settings()
limiter = get_limiter()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from review_scraper.core.websocket import get_task_update_subscriber

    subscriber = get_task_update_subscriber()
    await subscriber.start()
    try:
        yield
    finally:
        await subscriber.stop()


app = FastAPI(
    title=settings.app_name,
    description="评论抓取与分析系统 - 支持天猫、京东等电商平台的评论数据采集和智能分析",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
    openapi_tags=[
        {
            "name": "reviews",
            "description": "评论抓取相关接口 - 管理评论采集任务的创建、查询和下载",
        },
        {
            "name": "analysis",
            "description": "数据分析相关接口 - 提供关键词提取、情感分析等智能分析功能",
        },
        {
            "name": "products",
            "description": "商品监控相关接口 - 管理监控的商品列表和快照数据",
        },
        {
            "name": "frontend",
            "description": "前端页面路由 - Web界面的HTML页面渲染",
        },
    ],
)
app.state.limiter = limiter

# Add metrics middleware
app.add_middleware(MetricsMiddleware)

# Register exception handlers
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

# Include routers
app.include_router(frontend_router)
app.include_router(products_router, prefix="/api")
app.include_router(reviews_router, prefix="/api")
app.include_router(analysis_router, prefix="/api")

# Mount Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

static_dir = Path(__file__).resolve().parent / "web" / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Basic health check endpoint."""
    return {"status": "ok"}


@app.get("/health")
async def health_check(request: Request) -> dict:
    """Detailed health check with component status."""
    health = {
        "status": "healthy",
        "components": {
            "api": "up",
            "database": "unknown",
        },
    }

    # Check database connection
    try:
        from sqlalchemy import text
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        health["components"]["database"] = "up"
    except Exception as e:
        health["components"]["database"] = f"down: {e}"
        health["status"] = "unhealthy"

    return health
