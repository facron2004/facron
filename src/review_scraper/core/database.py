"""Database engine and session management."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from review_scraper.core.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        url = settings.database_url
        connect_args: dict = {}
        pool_config: dict = {}

        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
            # SQLite uses SingletonThreadPool by default; pool_size/max_overflow
            # are not valid kwargs for that dialect.
        else:
            # PostgreSQL/MySQL pooling
            pool_config["pool_size"] = 10
            pool_config["max_overflow"] = 20
            pool_config["pool_timeout"] = 30
            pool_config["pool_recycle"] = 3600

        _engine = create_engine(
            url,
            echo=False,
            connect_args=connect_args,
            pool_pre_ping=True,
            **pool_config,
        )

        # Update metrics
        from review_scraper.core.metrics import db_connections_total
        db_connections_total.inc()

    return _engine


def get_session() -> Session:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autocommit=False, autoflush=False)
    return _SessionLocal()


def init_db() -> None:
    """Create all tables if they don't exist."""
    Base.metadata.create_all(bind=get_engine())


def drop_db() -> None:
    """Drop all tables. Only for testing."""
    Base.metadata.drop_all(bind=get_engine())
