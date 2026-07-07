# Review Scraper

默认数据库现在使用 PostgreSQL。`docker-compose.yml` 会启动一套本地 PostgreSQL，应用与 Celery worker 默认连接它。

默认连接串：

```text
postgresql+psycopg2://review_scraper:review_scraper@postgres:5432/review_scraper
```

如果你在本机直接运行，也可以把 `DATABASE_URL` 改成 `localhost` 版本；生产环境建议始终使用 PostgreSQL。

## Windows 快速启动

如果你不想看到命令行窗口，直接双击 `start.bat`。

- Redis / Celery / FastAPI 由计划任务后台拉起
- 启动日志写到 `start.log` 和 `logs/` 目录
- 启动后自动打开浏览器
- 停止用 `stop.bat`

首次使用时，管理员权限运行一次 `service_install.bat` 用来注册计划任务。
