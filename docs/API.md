# API Documentation

评论抓取与分析系统 REST API 文档

## 基础信息

- **Base URL**: `http://localhost:8000/api`
- **API 文档**: `http://localhost:8000/api/docs` (Swagger UI)
- **ReDoc 文档**: `http://localhost:8000/api/redoc`
- **版本**: 1.0.0

## 认证

当前版本暂不需要认证。

## 速率限制

- 全局限制: 100 req/min
- 抓取任务创建: 10 req/min
- 下载操作: 20 req/min

超出限制时返回 `429 Too Many Requests`。

## 接口列表

### 评论抓取 (Reviews)

#### 创建抓取任务

```http
POST /api/reviews/tmall/scrape
```

**请求体**:
```json
{
  "url": "https://detail.tmall.com/item.htm?id=123456789",
  "max_pages": 0,
  "sort": "default",
  "headless": true,
  "browser": "chrome",
  "scan_all_responses": false
}
```

**参数说明**:
- `url` (必填): 天猫商品详情页URL
- `max_pages` (可选, 默认0): 抓取的最大页数，0表示全部
- `sort` (可选, 默认default): 排序方式 - default/time/useful
- `headless` (可选, 默认true): 是否使用无头浏览器
- `browser` (可选, 默认chrome): 浏览器类型
- `scan_all_responses` (可选, 默认false): 是否扫描所有网络响应

**响应**:
```json
{
  "task_id": "12345678-1234-1234-1234-123456789abc",
  "status": "queued",
  "item_id": "123456789",
  "review_count": 0,
  "page_count": 0,
  "error": null,
  "created_at": "2024-01-01T00:00:00Z",
  "completed_at": null
}
```

#### 查询任务状态

```http
GET /api/reviews/tmall/tasks/{task_id}
```

**路径参数**:
- `task_id`: 任务ID

**响应**: 同上，状态会实时更新

**状态类型**:
- `queued`: 排队中
- `running`: 运行中
- `done`: 完成
- `failed`: 失败

#### 获取任务列表

```http
GET /api/reviews/tmall/tasks?limit=20
```

**查询参数**:
- `limit` (可选, 默认20): 返回数量 (1-100)

**响应**:
```json
[
  {
    "task_id": "...",
    "status": "done",
    "item_id": "123456789",
    "review_count": 500,
    "page_count": 25,
    ...
  }
]
```

#### 下载任务结果

```http
GET /api/reviews/tmall/tasks/{task_id}/download?format=csv
```

**路径参数**:
- `task_id`: 任务ID

**查询参数**:
- `format` (可选, 默认csv): 文件格式 - csv/xlsx/json

**响应**: 文件流下载

**注意**: 仅当任务状态为 `done` 时可用

#### WebSocket 实时更新

```
ws://localhost:8000/api/reviews/tmall/tasks/{task_id}/ws
```

连接后会立即收到当前状态，任务状态变化时会自动推送更新。

**消息格式**:
```json
{
  "type": "status",
  "task_id": "...",
  "status": "running",
  "review_count": 150,
  "page_count": 8
}
```

### 数据分析 (Analysis)

#### 全量分析

```http
GET /api/analysis/tasks/{task_id}
```

**路径参数**:
- `task_id`: 任务ID

**响应**:
```json
{
  "keywords": [
    ["质量", 0.85],
    ["物流", 0.72],
    ["好评", 0.68]
  ],
  "sentiment": {
    "total": 500,
    "positive": 350,
    "negative": 50,
    "neutral": 100,
    "sentiment_score": 0.6
  },
  "statistics": {
    "total_reviews": 500,
    "avg_rating": 4.5,
    "rating_distribution": {
      "1": 10,
      "2": 20,
      "3": 80,
      "4": 150,
      "5": 240
    },
    "has_images": 200
  }
}
```

#### 提取关键词

```http
GET /api/analysis/tasks/{task_id}/keywords?top_n=20
```

**查询参数**:
- `top_n` (可选, 默认20): 返回数量 (1-100)

**响应**:
```json
{
  "keywords": [
    ["质量", 0.85],
    ["物流", 0.72]
  ]
}
```

#### 情感分析

```http
GET /api/analysis/tasks/{task_id}/sentiment
```

**响应**:
```json
{
  "sentiment": {
    "total": 500,
    "positive": 350,
    "negative": 50,
    "neutral": 100,
    "sentiment_score": 0.6
  }
}
```

情感得分范围: [-1, 1]
- -1: 完全负面
- 0: 中性
- 1: 完全正面

## 监控指标

Prometheus 指标端点:

```http
GET /metrics
```

详见 [MONITORING.md](./MONITORING.md)

## 错误响应

所有错误遵循标准格式:

```json
{
  "detail": "错误描述信息"
}
```

**常见状态码**:
- `400 Bad Request`: 请求参数错误
- `404 Not Found`: 资源不存在
- `429 Too Many Requests`: 超出速率限制
- `500 Internal Server Error`: 服务器内部错误

## 使用示例

### Python (requests)

```python
import requests
import time

# 创建抓取任务
resp = requests.post('http://localhost:8000/api/reviews/tmall/scrape', json={
    'url': 'https://detail.tmall.com/item.htm?id=123456789',
    'max_pages': 5
})
task_id = resp.json()['task_id']

# 轮询任务状态
while True:
    resp = requests.get(f'http://localhost:8000/api/reviews/tmall/tasks/{task_id}')
    status = resp.json()['status']
    if status in ['done', 'failed']:
        break
    time.sleep(5)

# 下载结果
resp = requests.get(
    f'http://localhost:8000/api/reviews/tmall/tasks/{task_id}/download',
    params={'format': 'csv'}
)
with open('reviews.csv', 'wb') as f:
    f.write(resp.content)

# 分析数据
resp = requests.get(f'http://localhost:8000/api/analysis/tasks/{task_id}')
analysis = resp.json()
print(f"情感得分: {analysis['sentiment']['sentiment_score']}")
print(f"Top关键词: {analysis['keywords'][:5]}")
```

### JavaScript (WebSocket)

```javascript
const taskId = '12345678-1234-1234-1234-123456789abc';
const ws = new WebSocket(`ws://localhost:8000/api/reviews/tmall/tasks/${taskId}/ws`);

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log('任务状态:', data.status);
    console.log('评论数:', data.review_count);
    
    if (data.status === 'done') {
        ws.close();
        // 下载结果
        window.location.href = `/api/reviews/tmall/tasks/${taskId}/download?format=xlsx`;
    }
};
```

### cURL

```bash
# 创建任务
curl -X POST http://localhost:8000/api/reviews/tmall/scrape \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://detail.tmall.com/item.htm?id=123456789",
    "max_pages": 3
  }'

# 查询状态
curl http://localhost:8000/api/reviews/tmall/tasks/{task_id}

# 下载CSV
curl -O http://localhost:8000/api/reviews/tmall/tasks/{task_id}/download?format=csv

# 获取分析结果
curl http://localhost:8000/api/analysis/tasks/{task_id}
```

## 最佳实践

1. **使用 WebSocket 代替轮询**: 实时获取任务更新，减少API调用
2. **缓存分析结果**: 分析接口结果会缓存1小时，无需重复调用
3. **合理设置 max_pages**: 大量评论时建议分批抓取
4. **异常处理**: 始终检查任务的 error 字段
5. **速率限制**: 批量操作时注意速率限制

## 更多信息

- WebSocket 演示页面: http://localhost:8000/static/websocket_demo.html
- Grafana 仪表盘: http://localhost:3000
- Prometheus 监控: http://localhost:9090
