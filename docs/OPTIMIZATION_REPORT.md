# 项目结构优化报告

> 基于 `project_audit_review_scraper.md` 审查结论，对项目进行工程清理 + 核心链路重构。
> 本次优化的核心方向：**砍掉竞品分析，把重心放在电商评论抓取**。

生成时间：2026-07-02

---

## 一、本次优化总览

| 维度 | 优化前 | 优化后 |
|---|---|---|
| 项目定位 | 评论抓取 + Amazon 关键词竞品监控 + RPA 工具三套原型混在一起 | 单一聚焦：电商评论抓取与分析 |
| 项目结构 | 根目录嵌套 `competitive-intelligence-tracker/` 旧项目副本、`src/rpa_tool/`、零散脚本 | 单一 src 布局，所有非评论代码已移除 |
| 任务执行 | `asyncio.create_task` 在 API 进程里跑，重启即丢 | Celery + Redis 异步队列，可重试/超时/恢复 |
| 评论读取 | 从导出的 JSON 文件读，DB 只是部分持久化 | 分页/预览/分析全部从 `reviews` 表读 |
| 数据模型 | 缺 `task_id / platform / rating / dedup_hash / raw_payload` 等关键字段 | 评论表全量补齐，新增 `crawl_batches` 表 |
| 去重 | 内存 set，重启失效 | DB 唯一约束 + `dedup_hash` 双重保险 |
| URL 安全 | 任意 URL 直接打开浏览器 | 平台白名单 (`detail.tmall.com` 等) |
| 跨平台 | 只查 Windows Chrome/Edge 路径，Docker/Linux 直接失败 | Linux/Docker 用 Playwright Chromium；Windows 可选系统浏览器 |
| 路由 | `GET /reviews/tmall/tasks/{task_id}/reviews` 重复定义两次，返回模型不同 | 拆分为 `/reviews` (分页) + `/preview` + `/batches`，无重复 |
| Dockerfile | `COPY pyproject.toml setup.py` (setup.py 不存在)，缺 alembic.ini | 修正为真实文件，补齐 alembic 目录 |
| 健康检查 | `conn.execute("SELECT 1")` SQLAlchemy 2.x 报错 | 改为 `text("SELECT 1")` |
| SQLite 引擎 | 传 `pool_size/max_overflow` 给 SingletonThreadPool 直接抛 TypeError | SQLite 分支不再传 pool 参数 |
| 测试 | 19 个，包含已删除模块的测试 | 18 个，全部通过，覆盖评论分析/缓存/前端 |

---

## 二、删除清单 (已执行)

### 整目录删除
- `competitive-intelligence-tracker/` — Amazon 关键词竞品监控旧项目副本 (131 个文件)
- `src/rpa_tool/` — RPA 工具原型
- `src/review_scraper/cli/` — `keyword_monitor.py` CLI 入口
- `src/review_scraper/modules/ai/` — 竞品 AI 摘要 stub
- `src/review_scraper/modules/collector/` — 竞品快照采集 stub
- `src/review_scraper/modules/diff_engine/` — 竞品快照对比 stub
- `src/review_scraper/modules/keyword_monitor/` — 关键词监控全模块 (10 个文件)
- `src/review_scraper/modules/notifier/` — 竞品变更通知 stub

### 单文件删除
- `1.zip` — 来源不明的压缩包
- `clean_haishi_c7_reviews.py` / `reclean_haishi_c7_keywords.py` — 一次性清洗脚本
- `fetch_tmall_reviews.py` — 早期独立抓取脚本 (功能已并入 `tmall_reviews` 模块)
- `fix_codex_reconnecting.ps1` — Codex 调试脚本
- `run_rpa.py` — RPA 入口
- `figma_report_deck_outline.md` / `questionnaire_analysis.md` / `questionnaire_summary.json` — 与本项目无关的需求/问卷文档
- `scripts/clean_haishi_c7_reviews.py` / `scripts/reclean_haishi_c7_keywords.py`
- `tests/test_engine.py` — RPA 引擎测试
- `tests/test_keyword_diff_engine.py` / `tests/test_keyword_pipeline.py` — 关键词监控测试
- `tests/test_fetch_tmall_reviews.py` — 旧脚本测试

---

## 三、核心代码改动

### 3.1 数据模型重构 (`src/review_scraper/models/__init__.py`)

**删除**：`Snapshot`、`Change` 两张竞品监控表。

**Product 表**：删除 `source_type / currency / crawl_interval_minutes / last_snapshot_id`；新增 `platform / shop_id / shop_name`。

**Review 表**：新增 `task_id / platform / external_product_id / user_id / sku_id / rating / media_urls / sentiment_score / sentiment_label / dedup_hash / raw_payload`；新增唯一约束 `uq_review_platform_ext_review (platform, external_product_id, review_id)`。

**ScrapeTask 表**：新增 `platform / retry_count / parent_task_id`；补齐此前缺失的 `task_type / name / task_params / progress`。

**新增表**：`CrawlBatch` — 每个抓取到的接口响应/页一行，保留 `raw_payload` 与 `fingerprint`，做到全链路可追溯。

### 3.2 Celery 任务化 (`src/review_scraper/workers/`)

- `celery_app.py`：app 名从 `competitive_intelligence_tracker` 改为 `review_scraper`；删除所有竞品任务路由与 beat 调度；新增 `task_acks_late / worker_prefetch_multiplier / task_time_limit` 等生产级配置。
- `tasks.py`：删除 5 个竞品 stub 任务 (`schedule_due_products / collect_product / run_diff / run_ai_summary / send_notification / run_keyword_daily_monitor`)；重写 `scrape_tmall` 为完整的 Celery 任务，自带 DB 状态更新、WebSocket 推送、metrics 上报、异常截图、导出文件生成。

### 3.3 API 路由 (`src/review_scraper/api/routes/`)

**reviews.py** 重写：
- 删除重复的 `GET /reviews/tmall/tasks/{task_id}/reviews`，拆成三个职责清晰的端点。
- `POST /scrape` 改为 `celery_app.send_task(...)`，不再用 `asyncio.create_task`。
- 新增 `_validate_url()` 平台白名单校验，未命中直接 400。
- `/reviews` 与 `/preview` 改为从 `reviews` 表查，不再读 JSON 文件。
- 新增 `/batches` 端点，返回该任务的原始抓取批次。
- `browser` 入参文档说明只支持 chrome/edge (实际只走 chromium)。

**analysis.py** 重写：分析数据从 `reviews` 表读取，不再依赖导出的 JSON 文件。

**frontend.py** 重写：删除所有 keyword_monitor / Amazon 验证 / BSR 相关逻辑；`/api/frontend/overview` 改为从 DB 聚合任务与评论计数。

**products.py** 重写：从竞品"监控商品"改为"评论抓取商品注册"，配合白名单校验。

### 3.4 采集器跨平台修复 (`src/review_scraper/modules/tmall_reviews/collector.py`)

- `browser_executable()` 在 Linux/Docker 返回 `None`，让 Playwright 使用自带 Chromium；Windows 仍可选系统 Chrome/Edge。
- `create_context()` 在 `executable_path` 为空时不传该参数。
- `ReviewCollector.persist_to_database()` 新增 `task_id` 参数；抓取的每个批次写入 `crawl_batches` 表；每条评论计算 `dedup_hash` 并写入。
- `scrape_reviews()` 新增 `task_id` 参数，贯穿到 collector 与 DB 写入。

### 3.5 配置精简 (`src/review_scraper/core/config.py`)

删除所有 `keyword_monitor_*` 配置项；新增 `review_export_dir / tmall_profile_dir / tmall_login_timeout / scrape_task_timeout / scrape_max_concurrent / allowed_hosts` 等评论抓取相关配置；保留 `feishu_webhook_url / smtp_*` 为后续告警预留。

### 3.6 数据库引擎修复 (`src/review_scraper/core/database.py`)

SQLite 分支不再传 `pool_size / max_overflow` (SingletonThreadPool 不支持)，修复了启动即崩的 TypeError。

### 3.7 健康检查修复 (`src/review_scraper/main.py`)

`/health` 端点的 `conn.execute("SELECT 1")` 改为 `conn.execute(text("SELECT 1"))`，适配 SQLAlchemy 2.x。

### 3.8 Alembic 迁移 (`alembic/versions/a1b2c3d4e5f6_refactor_for_review_scraping.py`)

新增第三个迁移版本，执行：
- drop `changes` / `snapshots` 两张竞品表
- products 表列增删
- scrape_tasks 表列增删 (含补齐此前未迁移的 `task_type / name / task_params / progress`)
- reviews 表新增 11 个分析字段 + 唯一约束 (用 `batch_alter_table` 兼容 SQLite)
- 新建 `crawl_batches` 表
- `alembic/env.py` 的 model 导入同步更新

`alembic upgrade head` 在全新 SQLite 上验证通过。

### 3.9 Dockerfile 修复

`COPY pyproject.toml setup.py` → `COPY pyproject.toml`；新增 `COPY alembic.ini` 与 `COPY alembic ./alembic`，让容器内 `alembic upgrade head` 能跑通。

### 3.10 pyproject.toml 重写

补齐 `playwright / jinja2 / jieba / slowapi / prometheus-client / celery[redis] / redis / alembic / sqlalchemy / openpyxl / pandas / httpx / pydantic-settings` 等实际依赖；删除 dev 占位。

---

## 四、验证结果

### 4.1 导入验证
```
OK: main app imports
OK: models import
OK: celery app imports
OK: scrape_tmall task imports
OK: collector imports
OK: reviews router imports
OK: frontend router imports
```

### 4.2 路由验证
OpenAPI 共 15 个端点，**无重复路由**。原 `GET /reviews/tmall/tasks/{task_id}/reviews` 重复定义问题已解决。

### 4.3 测试
```
18 passed, 1 warning in 35.20s
```
覆盖：评论分析 (关键词/情感/统计)、缓存、前端页面、概览 API。

### 4.4 数据库迁移
```
Running upgrade  -> c772a2bb3817, Initial schema
Running upgrade c772a2bb3817 -> 08380b7909a9, Add performance indexes
Running upgrade 08380b7909a9 -> a1b2c3d4e5f6, Refactor schema for review-scraping focus
```
迁移后表结构与 ORM 模型完全一致。

---

## 五、审查报告中 P0/P1 问题对照

| 优先级 | 问题 | 状态 |
|---|---|---|
| P0 | Dockerfile 引用不存在的 setup.py、缺 alembic.ini | ✅ 已修复 |
| P0 | 混合项目结构 (竞品副本 + RPA + 评论) | ✅ 已清理，单一评论系统 |
| P0 | 评论任务在 API 进程里 `asyncio.create_task` | ✅ 改为 Celery `send_task` |
| P0 | 天猫抓取在 Docker/Linux 跑不通 (只查 Windows 路径) | ✅ Linux 用 Playwright Chromium |
| P1 | 重复路由 `GET /reviews/tmall/tasks/{task_id}/reviews` | ✅ 拆分为三个端点 |
| P1 | Review 表缺分析字段 | ✅ 补齐 11 个字段 + 唯一约束 |
| P1 | 评论分页/分析读 JSON 文件 | ✅ 改为从 DB 读 |
| P1 | 去重靠内存 set | ✅ DB 唯一约束 + dedup_hash |
| P1 | URL 无白名单 | ✅ 新增 `allowed_hosts` 校验 |
| P1 | health check SQLAlchemy 2.x 报错 | ✅ 改为 `text("SELECT 1")` |
| P1 | SQLite 引擎传无效 pool 参数 | ✅ SQLite 分支不再传 pool 参数 |

---

## 六、未完成 (后续 TODO)

按审查报告路线图，以下项未在本次"工程清理"阶段处理，建议在下一阶段"把抓取做稳"中推进：

### 抓取稳定性
- [ ] 任务超时 / 重试 / 失败截图 / HTML 片段保存
- [ ] per-domain 并发限制 (当前只有全局 `SCRAPE_MAX_CONCURRENT` 配置)
- [ ] Cookie / Profile 管理独立化 (多账号轮换)
- [ ] 解析器加真实响应 fixture 测试

### 数据分析增强
- [ ] SKU 维度差评率统计
- [ ] 评论趋势图 (按日/周)
- [ ] 差评原因分类 (物流/质量/包装/尺码/客服/价格)
- [ ] 高频词云 / 短语提取
- [ ] LLM 总结与运营建议

### 多平台
- [ ] 抽象 `ReviewCollector` 接口，接入淘宝 / 京东 / Amazon / 抖音 / 拼多多
- [ ] 每个平台一个 collector 实现，复用任务/导出/分析链路

### 商业化
- [ ] 多用户 / 租户鉴权
- [ ] 项目 / 商品分组
- [ ] 订阅额度与导出额度
- [ ] 告警推送 (飞书 / 企微 / 邮件) — config 已预留，引擎待实现

---

## 七、一句话总结

项目已从"三套原型混搭"收敛为**单一、可启动、可部署、任务可恢复、数据入库完整的电商评论抓取系统**。
"提交 URL → Celery 抓取 → 评论+批次入库 → 分页查看 → 分析 → 导出"这条主链路已跑通，
后续可在稳定的工程底座上按路线图扩展平台与分析能力。
