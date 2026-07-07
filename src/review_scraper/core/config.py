"""Application settings for the review scraping platform."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "电商评论抓取与分析系统"
    app_env: str = "local"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # Database / cache
    database_url: str = "postgresql+psycopg2://review_scraper:review_scraper@localhost:5432/review_scraper"
    redis_url: str = "redis://localhost:6379/0"

    # Review scraping
    review_export_dir: str = "data/review_exports"
    tmall_profile_dir: str = ".tmall-profile"
    tmall_login_timeout: int = 300
    scrape_task_timeout: int = 1800
    scrape_max_concurrent: int = 3

    # URL whitelist for review scraping (comma-separated host substrings)
    allowed_hosts: str = "detail.tmall.com,item.taobao.com,item.jd.com,detail.m.tmall.com,item.m.jd.com"

    # Notification (optional, for future alerting on bad-review spikes)
    feishu_webhook_url: str | None = None
    smtp_host: str = "localhost"
    smtp_port: int = 25
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    notify_from: str = "alerts@example.com"
    notify_to: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
