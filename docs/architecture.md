# 竞品动态监控系统技术方案

## 一、系统整体架构

### 1.1 目标

系统用于持续监控竞品商品链接，自动抓取价格、评分、评论数、标题等关键字段，对比“昨日 vs 今日”的变化，生成 AI 变化摘要，并通过邮件或消息渠道通知用户。

### 1.2 架构原则

- 以“快照”为核心数据模型，所有分析都基于快照演进。
- 采集、Diff、AI、推送全部解耦，便于横向扩容。
- 每一步都要可重试、可审计、可追溯。
- V1 先支持 Amazon 等公开商品详情页，后续扩展站点解析器。

### 1.3 文本结构图

```text
+-----------------------+         +----------------------------+
|   Web Console / API   | ----->  |   FastAPI Application      |
|  添加URL/查看变化/配置  |         |  Product / Snapshot APIs   |
+-----------------------+         +-------------+--------------+
                                                |
                                                v
                                    +-------------------------+
                                    |      PostgreSQL         |
                                    | products / snapshots    |
                                    | changes / users         |
                                    +-----------+-------------+
                                                ^
                                                |
                  +-----------------------------+-----------------------------+
                  |                             |                             |
                  v                             v                             v
        +------------------+         +------------------+         +------------------+
        | Celery Beat/Cron | ----->  |   Redis Broker   | ----->  | Celery Workers   |
        | 定时下发采集任务   |         | 任务队列/重试缓冲  |         | 多队列消费         |
        +------------------+         +------------------+         +----+----+----+---+
                                                                                |    |
                                             +----------------------------------+    +----------------------+
                                             |                                                           |
                                             v                                                           v
                                  +-----------------------+                                +-----------------------+
                                  | Collector Workers     |                                | Diff / AI / Notify    |
                                  | Playwright 抓取页面    |                                | 比较/总结/推送         |
                                  +-----------+-----------+                                +-----------+-----------+
                                              |                                                        |
                                              v                                                        v
                                  +-----------------------+                                +-----------------------+
                                  | snapshots/raw_payload |                                | changes / send_logs    |
                                  | HTML/结构化字段/截图    |                                | diff结果/AI摘要/状态    |
                                  +-----------------------+                                +-----------+-----------+
                                                                                                        |
                                                                                                        v
                                                                                            +-----------------------+
                                                                                            | Email / Slack / 企业微信 |
                                                                                            | Webhook / App Push       |
                                                                                            +-----------------------+
```

### 1.4 服务边界

- API 服务：负责用户录入 URL、管理监控对象、查询变化记录。
- Collector Worker：专注抓取页面和解析结构化字段。
- Diff Worker：比较两个快照并生成差异结果。
- AI Worker：消费差异结果，输出业务可读摘要。
- Notifier Worker：根据通知策略向用户发送结果。

## 二、模块拆解

### 2.1 数据采集模块

#### 职责

- 接收待抓取产品任务。
- 使用站点适配器访问页面并提取结构化字段。
- 生成原始快照和标准化快照。
- 写入抓取日志与错误信息。

#### 输入

- `product_id`
- `source_url`
- 站点类型（amazon / shopify / custom）

#### 输出

- `snapshots` 新增一条成功或失败记录
- 更新 `products.last_crawled_at`

#### 设计要点

- 优先使用 Playwright，支持 JS 渲染、电商站点动态加载、页面截图。
- 采集器按域名做限流，例如 `amazon.com` 单域并发固定。
- 每个站点采用 Parser 插件模式：
  - `BaseProductParser`
  - `AmazonProductParser`
  - `ShopifyProductParser`
- 对原始字段和标准字段同时存储：
  - 原始字段便于审计
  - 标准字段便于下游 Diff
- 抓取失败也要落库，不能只写日志，否则无法统计失败率。

### 2.2 数据存储模块

#### 职责

- 维护竞品主数据、快照数据、变化记录。
- 提供高效查询：
  - 最新快照
  - 某日变化
  - 某商品价格趋势
- 支撑多租户隔离。

#### 设计要点

- PostgreSQL 为主库。
- `snapshots` 表按时间分区或按月分表，避免大表膨胀。
- 大字段优先放 `jsonb`，例如 `raw_payload`、`diff_payload`。
- 对高频查询字段建复合索引：
  - `product_id + captured_at desc`
  - `tenant_id + status`
  - `change_date + tenant_id`

### 2.3 变更检测模块（Diff Engine）

#### 职责

- 比较当前快照与上一条成功快照。
- 识别字段级变化和业务级变化。
- 生成结构化差异结果供 AI 与通知模块复用。

#### 比较字段

- 标题 `title`
- 售价 `price`
- 原价 `list_price`
- 评分 `rating`
- 评论数 `review_count`
- 库存状态 `availability_status`
- 卖家 `seller_name`

#### 输出示例

```json
{
  "changed": true,
  "fields": {
    "price": { "from": 39.99, "to": 34.99, "delta": -5.00, "delta_pct": -12.5 },
    "review_count": { "from": 1203, "to": 1258, "delta": 55 },
    "rating": { "from": 4.3, "to": 4.5, "delta": 0.2 }
  },
  "severity": "high"
}
```

#### 设计要点

- 首次快照只建基线，不推送变化。
- 支持阈值配置：
  - 价格变化超过 5% 才通知
  - 评论数增长超过 20 才通知
- Diff 输出必须结构化，不能只保存文本。
- `severity` 由规则引擎先给出，AI 只负责可读化总结，不参与事实判断。

### 2.4 AI 分析模块

#### 职责

- 读取 `diff_payload`
- 生成业务摘要、变化重点、可能影响
- 输出适合邮件/消息渠道展示的短文案

#### 输入

- 商品基本信息
- 上一次与当前快照
- Diff 结果

#### 输出

- `summary_title`
- `summary_text`
- `risk_level`
- `action_hint`

#### Prompt 设计

- 系统提示词固定：要求只基于提供的 diff 数据总结，不得编造。
- 用户提示词输入结构化 JSON：
  - 商品标题
  - 当前价格
  - 变化字段
  - 最近 7 天趋势摘要（可选）

#### 设计要点

- AI 调用失败不能阻塞通知链路。
- 若 AI 失败，通知模块使用规则模板兜底：
  - “价格下降 12.5%，评论增加 55 条”
- AI 输出建议保存原文和模型名，便于回溯质量。

### 2.5 推送模块

#### 职责

- 根据用户通知策略分发变化消息。
- 支持去重、节流、失败重试。

#### 支持渠道

- Email
- Slack / 飞书 / 企业微信 Webhook
- 站内消息（后续）

#### 设计要点

- 推送不直接从采集任务里发送，必须走独立队列。
- 每条通知记录要有状态机：
  - `pending`
  - `sent`
  - `failed`
  - `retrying`
- 对同一商品短时间多次变化做聚合，避免消息轰炸。

## 三、数据库设计

### 3.1 建模原则

- `products` 记录“监控对象”
- `snapshots` 记录“时间点事实”
- `changes` 记录“快照之间的差异”

### 3.2 核心表：products

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | `uuid` PK | 商品监控对象主键 |
| `tenant_id` | `uuid` | 多租户隔离 ID，单用户版可先固定 |
| `created_by` | `uuid` | 创建人 |
| `source_type` | `varchar(32)` | 来源站点，如 `amazon` |
| `source_url` | `text` | 用户输入原始 URL |
| `normalized_url` | `text` | 规范化 URL，用于去重 |
| `external_product_id` | `varchar(128)` | 外部商品 ID，如 ASIN |
| `marketplace` | `varchar(64)` | 站点区域，如 `amazon_us` |
| `title_current` | `text` | 当前最新标题缓存 |
| `brand` | `varchar(255)` | 品牌 |
| `currency` | `char(3)` | 默认币种，如 `USD` |
| `status` | `varchar(32)` | `active / paused / archived` |
| `crawl_interval_minutes` | `integer` | 抓取频率，分钟 |
| `last_crawled_at` | `timestamptz` | 最近抓取时间 |
| `last_snapshot_id` | `bigint` | 最近成功快照 ID |
| `created_at` | `timestamptz` | 创建时间 |
| `updated_at` | `timestamptz` | 更新时间 |

#### 关键索引

- `unique (tenant_id, normalized_url)`
- `index (tenant_id, status)`
- `index (marketplace, status)`

### 3.3 核心表：snapshots

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | `bigserial` PK | 快照主键 |
| `tenant_id` | `uuid` | 多租户隔离 ID |
| `product_id` | `uuid` FK | 关联 `products.id` |
| `captured_at` | `timestamptz` | 抓取完成时间 |
| `crawl_status` | `varchar(32)` | `success / failed / blocked` |
| `title` | `text` | 抓取到的标题 |
| `price` | `numeric(12,2)` | 当前售价 |
| `list_price` | `numeric(12,2)` | 划线价/原价 |
| `currency` | `char(3)` | 币种 |
| `rating` | `numeric(3,2)` | 评分 |
| `review_count` | `integer` | 评论数 |
| `availability_status` | `varchar(32)` | 在售、缺货、下架 |
| `seller_name` | `varchar(255)` | 卖家名 |
| `page_hash` | `char(64)` | 页面结构摘要，用于快速判断 |
| `raw_payload` | `jsonb` | 原始解析结果 |
| `error_message` | `text` | 失败原因 |
| `screenshot_url` | `text` | 页面截图地址，可选 |
| `created_at` | `timestamptz` | 入库时间 |

#### 关键索引

- `index (product_id, captured_at desc)`
- `index (tenant_id, captured_at desc)`
- `index (product_id, crawl_status, captured_at desc)`

### 3.4 核心表：changes

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | `bigserial` PK | 变化记录主键 |
| `tenant_id` | `uuid` | 多租户隔离 ID |
| `product_id` | `uuid` FK | 关联商品 |
| `baseline_snapshot_id` | `bigint` FK | 基准快照 |
| `current_snapshot_id` | `bigint` FK | 当前快照 |
| `change_date` | `date` | 变化所属业务日期 |
| `has_change` | `boolean` | 是否发生变化 |
| `change_types` | `text[]` | 变化字段类型列表 |
| `severity` | `varchar(16)` | `low / medium / high` |
| `diff_payload` | `jsonb` | 结构化变化详情 |
| `ai_summary_title` | `varchar(255)` | AI 摘要标题 |
| `ai_summary_text` | `text` | AI 变化说明 |
| `ai_model` | `varchar(64)` | 模型标识 |
| `notification_status` | `varchar(32)` | `pending / sent / failed / skipped` |
| `pushed_at` | `timestamptz` | 推送时间 |
| `created_at` | `timestamptz` | 创建时间 |

#### 关键索引

- `index (product_id, change_date desc)`
- `index (tenant_id, notification_status, created_at desc)`
- `index using gin (diff_payload)`

### 3.5 推荐补充表

虽然本次重点是 `products / snapshots / changes`，但生产可用版本建议同时建设以下表：

- `users`
- `watchlists`
- `notification_channels`
- `notification_logs`
- `crawl_jobs`
- `domain_rate_limits`

这些表不影响核心闭环，但会显著提升多用户和运维能力。

## 四、核心流程

### 4.1 从“用户添加 URL”到“推送通知”的完整流程

1. 用户在前端提交竞品 URL 和抓取频率。
2. API 层执行 URL 规范化：
   - 去掉无关追踪参数
   - 识别站点类型
   - 提取外部商品 ID，例如 ASIN
3. 系统检查 `(tenant_id, normalized_url)` 是否已存在：
   - 已存在：返回已有监控对象
   - 不存在：写入 `products`
4. 调度器创建首次采集任务，推入 `collector` 队列。
5. Collector Worker 使用 Playwright 打开页面，等待关键 DOM 稳定后解析字段。
6. 写入 `snapshots`：
   - 若是首次成功抓取：标记为基线快照，不进入通知链路
   - 若抓取失败：记录失败快照与错误原因
7. 对非首次成功快照，Diff Worker 取上一条成功快照进行字段对比。
8. Diff Engine 输出结构化 `diff_payload`，写入 `changes`。
9. 若 `has_change = true` 且命中通知阈值：
   - 推送 AI Summary 任务
   - AI Worker 生成变化摘要并回写 `changes`
10. Notifier Worker 根据用户渠道配置发送通知。
11. 推送结果写入通知日志，并更新 `changes.notification_status`。

### 4.2 失败处理策略

- 采集失败：指数退避重试 3 次，仍失败则标记 `failed`
- AI 失败：使用模板化摘要继续发送
- 通知失败：按渠道重试，超限后记录失败
- 任一步骤都不回滚历史快照，保证审计完整

## 五、技术选型

### 5.1 后端框架（Python）

选择：

- `FastAPI`
- `SQLAlchemy 2.x`
- `Pydantic v2`
- `Alembic`

原因：

- FastAPI 天然适合构建 API + 后台管理接口，异步 IO 友好。
- SQLAlchemy 2.x 对 PostgreSQL 支持成熟，适合复杂查询和 ORM/SQL 混合开发。
- Pydantic v2 适合输入校验、DTO 和配置管理。
- Alembic 负责数据库版本迁移，适合后续迭代。

### 5.2 爬虫方案

主选：

- `Playwright`

不作为主选但可保留：

- `Selenium`

原因：

- Amazon 等商品页普遍存在前端渲染、懒加载、动态内容，Playwright 更稳定。
- Playwright 支持无头浏览器、等待策略、截图、上下文隔离。
- Selenium 适合作为兼容方案，但维护成本更高，不建议作为首选。

### 5.3 定时任务

生产推荐：

- `Celery + Celery Beat + Redis`

开发环境可选：

- `Cron` 触发内部抓取 API

原因：

- 当监控对象上升到上千条时，Cron 无法优雅处理重试、并发控制、任务隔离。
- Celery 可拆分多队列：
  - `collector`
  - `diff`
  - `ai`
  - `notify`

### 5.4 数据库

选择：

- `PostgreSQL 15+`

原因：

- 支持 `jsonb`、数组、GIN 索引，适合保存 `diff_payload` 和原始抓取字段。
- 对复杂查询、事务、一致性要求更友好。
- 后期做分区、归档、读写分离更成熟。

### 5.5 AI 调用方式

推荐做法：

- 建一个统一的 `LLMProvider` 抽象层
- 默认实现接入 OpenAI 兼容接口或 Azure OpenAI 兼容接口
- 仅传入结构化 diff 数据，不直接传整页 HTML

接口建议：

```python
class LLMProvider(Protocol):
    async def summarize_change(self, payload: dict) -> dict: ...
```

调用策略：

- 输入：商品信息 + `diff_payload`
- 输出：结构化 JSON
  - `summary_title`
  - `summary_text`
  - `risk_level`
  - `action_hint`
- 失败兜底：规则模板

## 六、扩展性设计

### 6.1 如何支持多用户

#### 方案

- 所有核心表带 `tenant_id`
- 用户和监控对象通过 `watchlists` 或 `subscriptions` 建关系
- 通知配置按用户维度管理

#### 关键点

- 查询接口默认按 `tenant_id` 过滤
- Worker 消费任务时也要校验 `tenant_id`
- 日志、报表、限流都要支持租户维度

### 6.2 如何支持上千竞品

#### 调度层

- 抓取任务分队列
- 按域名和租户做限流
- 每次只抓取到期商品，不全量轮询

#### 采集层

- Worker 横向扩容
- Playwright Browser Context 复用
- 站点级 parser 插件隔离，减少单点故障

#### 存储层

- `snapshots` 分区
- 老快照归档到冷存储
- 只缓存 `products` 最新态，趋势数据走聚合查询

#### Diff/AI 层

- Diff 先规则化筛选，只有真实变化才进 AI
- AI 使用独立队列，避免拖慢采集主链路
- 低优先级变化可批量总结

### 6.3 推荐的生产部署

```text
Nginx
  -> FastAPI API Pods
  -> Celery Worker Pods
  -> Celery Beat
  -> PostgreSQL
  -> Redis
  -> Object Storage (可选，用于截图/原始HTML)
  -> Email/Webhook Gateway
```

## 七、直接开发建议

### V1 必做

- 新增监控对象 API
- 单站点 Amazon Parser
- 手动抓取 + 定时抓取
- 快照落库
- Diff 引擎
- Email 通知

### V1.5

- AI 摘要
- Slack / 飞书通知
- 价格趋势图
- 抓取失败告警

### V2

- 多站点 Parser 市场
- 多租户管理后台
- 监控规则自定义
- 报表导出

## 八、结论

这个系统最核心的设计不是“爬虫”，而是“快照事实层 + Diff 层 + 异步任务编排”。只要这三层设计稳，后续无论扩展更多站点、更多用户、更多通知渠道，都可以在现有架构上平滑演进。
