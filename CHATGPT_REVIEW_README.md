# Review Scraper — 送审说明

本压缩包为电商评论抓取与分析系统 **Review Scraper** 的完整源码包，供 ChatGPT 静态代码审查使用。

## 项目一句话

基于 FastAPI + Celery + Playwright + PostgreSQL 的电商评论抓取/分析平台，
目前以 **天猫 / 淘宝** 为主，支持评论采集、去重、关键词分析、Excel/PDF 导出、Web 端管理后台与实时 WebSocket 推送。

## 技术栈

| 层 | 选型 |
| --- | --- |
| Web 框架 | FastAPI 0.100+ |
| 异步任务 | Celery 5 + Redis 5 |
| 浏览器 | Playwright 1.40+（采集 + 渲染） |
| 数据库 | PostgreSQL（SQLAlchemy 2 + Alembic） |
| 模板/前端 | Jinja2 模板 + 原生 JS/CSS（无前端构建链） |
| 监控 | Prometheus client + 简单中间件 |
| 中文分词 | jieba |
| 导出 | openpyxl（Excel）、reportlab（PDF） |

## 目录结构

```
Review Crawler/
├── README.md                       ← 项目说明（Windows 启动）
├── pyproject.toml                  ← 依赖与打包配置
├── pytest.ini                      ← 测试配置
├── alembic.ini / alembic/          ← 数据库迁移
├── docker-compose.yml              ← PG + Redis + Web + Worker
├── Dockerfile
├── prometheus.yml                  ← 监控配置
├── .github/workflows/ci.yml        ← CI
├── start.bat / start_hidden.cmd / start.vbs / stop.bat
├── service_install.bat / service_check.bat
├── scripts/                        ← 开发运行脚本（run_dev.sh/.bat）
├── tools/redis/                    ← Windows 端 Redis 二进制（不参与审查）
├── src/review_scraper/             ← 主代码包
│   ├── main.py                     ← FastAPI 入口（app、生命周期、路由挂载）
│   ├── core/                       ← 配置、DB、缓存、日志、指标、WebSocket、浏览器池
│   ├── api/
│   │   ├── routes/                 ← reviews / products / analysis / frontend
│   │   └── middleware/             ← error_handlers / metrics / rate_limit
│   ├── models/                     ← SQLAlchemy 模型（__init__.py 中）
│   ├── modules/tmall_reviews/      ← 抓取/解析/去重/分析/导出/报告
│   ├── workers/                    ← Celery app + 任务定义
│   └── web/                        ← Jinja2 模板与 CSS（前后台）
├── tests/                          ← pytest 单元/接口测试
├── docs/                           ← API / 架构 / 监控 / 优化报告
├── examples/notepad_demo.json      ← 接口请求样例
└── data/                           ← 运行时产物（已排除 SQLite/导出文件/任务文件）
```

## 入口与运行

- 应用入口：`src/review_scraper/main.py`，创建 `app`、注册中间件、挂载路由、启动 lifespan。
- Celery 入口：`src/review_scraper/workers/celery_app.py`（`tasks.py` 提供任务实现）。
- Windows 启动：`start.bat` / `start_hidden.cmd`（后台拉起 Redis / Celery / FastAPI）。
- Docker：`docker-compose up` 拉起 postgres + redis + web + worker。
- Alembic：`alembic upgrade head` 初始化/升级数据库结构。

## 审查建议重点

请关注以下方面：

1. **核心抓取流水线**（`modules/tmall_reviews/collector.py` 是 1200+ 行的核心）
   - Playwright 上下文生命周期与浏览器池（`core/browser_pool.py`）的协作
   - 翻页、登录态、二次重试、异常恢复路径
2. **API 设计**（`api/routes/`）— RESTful 是否合理、错误处理（`api/middleware/error_handlers.py`）
3. **数据库层**（`models/__init__.py` 中的 SQLAlchemy 2.x 模型 + Alembic 迁移）
4. **异步任务可靠性**（`workers/tasks.py`：超时、重试、状态回写、WebSocket 推送）
5. **安全相关**
   - `api/middleware/rate_limit.py`（SlowAPI 限流）
   - 任意文件读/写风险（如 `analysis.py` 中读取任意路径、`export.py` 写文件）
6. **类型与可维护性**
   - 是否存在 `Any`、过宽异常捕获
   - 大文件是否需要拆分（`collector.py` 1267 行）
7. **测试覆盖**（`tests/` 6 个文件，关注 `test_reviews_api.py` 是否真的覆盖到真实路径）

## 范围说明

- **未打包**：`__pycache__`、`.git`、`.venv`、`.pytest_cache`、
  `.tmall-profile*` / `.workbuddy`（Playwright 浏览器/任务快照）、
  `data/`（SQLite + 导出 + 任务文件，含用户数据与凭证）、
  `outputs/` / `logs/` / `debug/`（运行产物）、
  `tools/redis/`（Windows Redis 二进制，约 5MB，不属于代码审查范围）、
  `start.log`（运行日志）。
- 仅打包源码、配置、测试、文档、模板、静态资源与 `examples/`。

## 联系方式

如需补充上下文，请阅读 `docs/architecture.md` 与 `docs/API.md`。
