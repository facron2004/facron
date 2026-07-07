# Prometheus Monitoring Integration

This document describes the Prometheus metrics exposed by the review scraper application.

## Metrics Endpoint

Metrics are exposed at: `http://localhost:8000/metrics`

## Available Metrics

### Application Info
- `review_scraper_app_info` - Application version and name

### HTTP Request Metrics
- `http_requests_total` - Total HTTP requests by method, endpoint, and status code
- `http_request_duration_seconds` - HTTP request duration histogram by method and endpoint

### Scraping Task Metrics
- `scrape_tasks_total` - Total scrape tasks created by source (tmall, etc.)
- `scrape_tasks_completed` - Total scrape tasks completed by source and status
- `scrape_task_duration_seconds` - Scrape task duration histogram by source
- `scrape_task_reviews_collected` - Number of reviews collected per task histogram
- `active_scrape_tasks` - Current number of active scrape tasks by source

### Cache Metrics
- `cache_operations_total` - Total cache operations by operation type (get/set/delete) and status
- `cache_hit_ratio` - Cache hit ratio gauge (0-1)

### Database Metrics
- `db_connections_total` - Total database connections created
- `db_query_duration_seconds` - Database query duration histogram by operation

### Browser Pool Metrics
- `browser_contexts_active` - Number of active browser contexts in the pool
- `browser_contexts_created_total` - Total browser contexts created
- `browser_contexts_evicted_total` - Total browser contexts evicted from pool

### Analysis Metrics
- `analysis_operations_total` - Total analysis operations by type
- `analysis_duration_seconds` - Analysis operation duration histogram by type

## Prometheus Configuration

The `prometheus.yml` configuration scrapes metrics from the app every 10 seconds:

```yaml
scrape_configs:
  - job_name: 'review_scraper'
    static_configs:
      - targets: ['app:8000']
    metrics_path: '/metrics'
    scrape_interval: 10s
```

## Grafana Integration

Grafana is available at `http://localhost:3000` (default credentials: admin/admin).

### Recommended Dashboards

1. **Request Performance**
   - Request rate by endpoint
   - Request duration percentiles (p50, p95, p99)
   - Error rate by status code

2. **Scraping Performance**
   - Active tasks gauge
   - Task completion rate
   - Reviews collected per task
   - Task duration trends

3. **Cache Performance**
   - Cache hit ratio over time
   - Cache operations by type
   - Cache miss rate

4. **Browser Pool Health**
   - Active contexts gauge
   - Context creation/eviction rates
   - Context lifetime distribution

## Query Examples

### Average request duration by endpoint
```promql
rate(http_request_duration_seconds_sum[5m]) / rate(http_request_duration_seconds_count[5m])
```

### Scrape task success rate
```promql
sum(rate(scrape_tasks_completed{status="done"}[5m])) / sum(rate(scrape_tasks_completed[5m]))
```

### Cache hit rate
```promql
cache_hit_ratio
```

### Active scraping tasks
```promql
active_scrape_tasks
```

## Docker Deployment

Start all services including Prometheus and Grafana:

```bash
docker-compose up -d
```

Services:
- App: http://localhost:8000
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000
