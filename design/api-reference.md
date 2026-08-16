# API 参考文档

> 个股买卖点监测系统 (K道) — 所有前端可用的 HTTP API + WebSocket 端点。
>
> **权威来源**：FastAPI 自动生成的交互式文档 [`http://localhost:8000/docs`](http://localhost:8000/docs)（Swagger UI）。
> 本文档为补充，方便离线查看和搜索。如有不一致，以 Swagger UI 为准。

---

## 1. 认证

### POST /api/login
登录获取 JWT Token。

| 属性 | 值 |
|------|-----|
| Content-Type | `application/json` |
| Auth | 无 |

**请求体**
```json
{ "username": "admin", "password": "***" }
```

**响应 200**
```json
{ "ok": true, "token": "eyJ...", "username": "admin" }
```

**响应 401**
```json
{ "detail": "用户名或密码错误" }
```

> dev 环境 (`APP_ENV=dev`) 不校验 Token，任意用户名密码均可登录。

---

## 2. 持仓管理

### GET /api/portfolio
获取当前活跃持仓列表（含浮动盈亏、当日信号）。

**响应 200**
```json
{
  "positions": [{
    "stock_code": "000001",
    "stock_name": "平安银行",
    "exchange": "SZSE",
    "quantity": 1000,
    "cost_price": 12.50,
    "current_price": 13.20,
    "market_value": 13200.00,
    "pnl": 700.00,
    "pnl_pct": 5.60,
    "created_at": "2026-01-15",
    "updated_at": "2026-06-10 14:30:00",
    "notes": "测试仓位",
    "signal_direction": "buy",
    "signal_date": "2026-06-12",
    "signal_strength": 2
  }],
  "total_value": 13200.00,
  "total_pnl": 700.00,
  "count": 1
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `signal_direction` | `"buy"` / `"sell"` / `""` | 最近交易日该股的融合信号方向，无信号为空串 |
| `signal_date` | `string` / `""` | 信号日期 (YYYY-MM-DD) |
| `signal_strength` | `int` | 信号强度 1-3 |

### POST /api/portfolio
新增持仓。已存在则加权平均更新数量和成本。

**请求体**
```json
{ "stock_code": "000001", "quantity": 1000, "cost_price": 12.50, "notes": "" }
```

**响应 200**
```json
{ "ok": true }
```

### PUT /api/portfolio/{stock_code}
更新持仓数量/成本/备注。

**请求体**
```json
{ "quantity": 1500, "cost_price": 13.00, "notes": "加仓" }
```

### DELETE /api/portfolio/{stock_code}
软删除持仓（设置 `is_active=false`）。

**响应 200**
```json
{ "ok": true, "deleted": "000001" }
```

### GET /api/portfolio/{stock_code}/history
获取指定持仓的加减仓历史。

**响应 200**
```json
{
  "records": [{
    "action": "add",
    "qty_before": 0, "qty_after": 1000,
    "cost_before": 0, "cost_after": 12.50,
    "created_at": "2026-01-15 10:00:00"
  }],
  "position": { "stock_code": "000001", "stock_name": "平安银行", "quantity": 1000, "cost_price": 12.50, "notes": "" }
}
```

---

## 3. 树图

### GET /api/treemap_data
获取市值树图数据。`parent` 为空返回全量树 (L1 → 个股)，指定 `parent` 返回该节点下子级。

| 参数 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `trade_date` | string | ✅ | 日期 YYYY-MM-DD |
| `metric` | string | | 指标：`mcap`(市值) / `volume`(成交量) / `amount`(成交额) / `pe`(PE分位)，默认 `mcap` |
| `parent` | string | | 父节点 ID：空=全量，`A`=农林牧渔行业，`C15`=饮料制造二级 |

**响应 200**
```json
{
  "trade_date": "2026-06-12",
  "children": [{
    "id": "C", "name": "制造业", "value": 1.2e12, "chg_pct": 1.5,
    "type": "l1", "children": [...]
  }]
}
```

### GET /api/treemap_latest_date
获取 `stock_treemap_cache` 中最新有数据的日期。

**响应 200**
```json
{ "latest_date": "2026-06-12" }
```
无数据返回 `{"latest_date": null}`。

---

## 4. 信号

### GET /api/buy_signals
获取指定日期的买点扫描结果（仅融合信号 `combined_signal=true`）。

| 参数 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `signal_date` | string | | 日期 YYYY-MM-DD，默认最近有信号的日期 |
| `top_n` | int | | 返回前 N 只，1-100，默认 20 |

**响应 200**
```json
{
  "signal_date": "2026-06-12",
  "scanned": 5850,
  "total_signals": 42,
  "top_n": 20,
  "signals": [{
    "stock_code": "000001", "stock_name": "平安银行",
    "direction": "buy", "strength": 3,
    "reason": "收盘价从下轨下方反弹回通道内 + ...",
    "price": 13.20,
    "suggested_action": "关注建仓",
    "source_strategies": ["bollinger_daily", "weekly_trend"],
    "preference": "balanced"
  }]
}
```

---

## 5. 个股

### GET /api/stock/{code}/detail
个股详情：最新行情 + 最新策略信号 + 基本面。

**响应 200**
```json
{
  "stock_code": "000001", "stock_name": "平安银行",
  "latest_trade_date": "2026-06-12",
  "open": 12.80, "high": 13.50, "low": 12.60, "close": 13.20,
  "close_hfq": 15.80, "volume": 50000000, "turnover": 2.5,
  "amount": 650000000,
  "latest_signals": [{ "direction": "buy", "strength": 3, "strategy_name": "bollinger_daily", ... }],
  "history_count": 15, "latest_signal_date": "2026-06-12",
  "fundamentals": { "industry": "金融业", "pe_ttm": 8.5, "pb_mrq": 1.2, "roe": 12.3,
                    "revenue_yoy": 5.2, "profit_yoy": 8.1 }
}
```

### GET /api/stock/{code}/history
个股历史策略信号（分页，不含最新交易日）。

| 参数 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `page` | int | | 页码，默认 1 |
| `page_size` | int | | 每页条数，5-50，默认 20 |

### GET /api/stock/{code}/kline
K 线 + 技术指标。

| 参数 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `days` | int | | 返回天数，30-500，默认 120 |
| `adjust` | string | | 复权：`none`(不复权) / `qfq`(前复权) / `hfq`(后复权)，默认 `none` |

**响应 200**
```json
{
  "stock_code": "000001", "stock_name": "平安银行",
  "kline": [{
    "trade_date": "2026-01-04", "open": 12.00, "high": 12.50,
    "low": 11.80, "close": 12.30, "volume": 40000000,
    "boll_mid": 12.10, "boll_upper": 12.80, "boll_lower": 11.40,
    "rsi": 55.2, "dif": 0.15, "dea": 0.10, "macd_bar": 0.10
  }]
}
```

### GET /api/stock/{code}/pe_history
PE/PB/ROE 历史时序 + 分位数（数据来源：`stock_fundamentals_history` 表）。

**响应 200**
```json
{
  "stock_code": "000001",
  "data": [
    {
      "date": "2024-03",
      "pe_ttm": 7.5,
      "pb_mrq": 1.1,
      "roe": 14.2,
      "pe_percentile": 25.0
    }
  ]
}
```

---

## 6. 股票列表

### GET /api/stocks
分页股票列表，支持搜索和排序。

| 参数 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `page` | int | | 页码，默认 1 |
| `page_size` | int | | 每页条数，10-100，默认 20 |
| `keyword` | string | | 搜索代码或名称 |
| `category` | string | | `stock` / `index` / `etf` / `bond` / `all`，默认 `stock` |
| `order_by` | string | | `price` / `chg_pct` / `pe_ttm` / `trade_date` / `market_cap`，默认 `trade_date` |
| `order_dir` | string | | `asc` / `desc`，默认 `desc` |

**响应 200**
```json
{
  "page": 1, "page_size": 20, "total": 5850, "total_pages": 293,
  "stocks": [{
    "stock_code": "000001", "stock_name": "平安银行", "exchange": "SZSE",
    "stock_type": "stock", "price": 13.20, "prev_close": 13.00,
    "chg_pct": 1.54, "trade_date": "2026-06-12", "data_rows": 1250,
    "pe_ttm": 8.5, "pb_mrq": 1.2, "industry": "金融业", "roe": 12.3
  }]
}
```

---

## 7. 数据状态与 DAG

### GET /api/data_status
数据完整性总览（按月份）。返回日历、数据明细、DAG 日志等。

| 参数 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `month` | string | | YYYY-MM，默认当月 |

**响应 200**

见 [§15 前后端关键字段与枚举值](../design/architecture.md#15-前后端关键字段与枚举值) 中的 WS 消息类型和数据字段定义。

### POST /api/data_status/sync_date
手动触发某日数据采集。

**请求体**
```json
{ "date": "2026-06-12", "node": "daily_update", "mode": "quick" }
```

| 字段 | 说明 |
|------|------|
| `mode` | `quick`=跳过已有 / `force`=全量覆盖 |

### POST /api/dag_trigger
手动触发 DAG 节点（后台执行，自动传播下游）。

**请求体**
```json
{
  "node": "kline",
  "date": "2026-06-12",
  "include_downstream": true
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `node` | string | | 节点名，默认 `stats`。可选: `kline` `index` `etf` `fund` `treemap` `strategy` `stats` `daily_completeness` `all` |
| `date` | string | | YYYY-MM-DD，默认今天 |
| `include_downstream` | bool | | 是否传播下游，默认 `true`。树图/信号单独生成时传 `false` |

**响应 200**
```json
{ "ok": true, "task_id": "a1b2c3d4", "node": "treemap", "date": "2026-06-12", "status": "started" }
```

**响应 200 (busy)**
```json
{ "ok": false, "error": "待上一个任务完成后再进行", "busy": true }
```

### GET /api/dag_status
DAG 当前状态（含看门狗超时检测）。

**响应 200**
```json
{
  "structure": [{ "name": "kline", "deps": ["daily_update"], "label": "A股日K线" }],
  "run_status": { "kline": { "status": "success", "rows": 5200, "detail": "", "time": "2026-06-12 17:36:00", "date": "2026-06-12" }},
  "current_run_id": "a1b2c3d4",
  "current_run_latest": "2026-06-12 17:36:00"
}
```

### GET /api/dag_config
DAG 流程结构（纯拓扑，无运行状态）。

### POST /api/dag_terminate
终止运行中的任务。

**请求体**
```json
{ "run_id": "a1b2c3d4" }
```

### GET /api/dag_logs
当前任务的完整节点日志。

### POST /api/refresh_stats
手动触发全库数据统计（stats 节点）。

### GET /api/trade_calendar
交易日历。

| 参数 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `year` | int | | 年份，默认当前年 |

---

## 7.5 历史补数

### POST /api/data_status/backfill
启动补数任务（后台异步执行，WebSocket 推送进度）。

| 属性 | 值 |
|------|-----|
| Content-Type | `application/json` |

**请求体**
```json
{
  "type": "kline",
  "start_date": "2021-06-16",
  "end_date": "2026-06-18",
  "force": false,
  "batch_size": 20
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `type` | string | ✅ | `kline`(个股) / `index`(指数) / `etf` / `fund`(基本面) |
| `start_date` | string | | 起始日期 YYYY-MM-DD，默认 5 年前。fund 类型按季度对齐 |
| `end_date` | string | | 截止日期 YYYY-MM-DD，默认今天 |
| `force` | bool | | 强制更新（跳过断点续传），默认 false |
| `batch_size` | int | | 每批拉取股票数，1-500，默认 20 |

**响应 200**
```json
{ "ok": true, "task_id": "bf_kline_20260618_143022" }
```

**响应 409 (busy)**
```json
{
  "ok": false,
  "error": "已有补数任务运行中，请等待完成或取消后再试",
  "busy": true,
  "current_task": { "task_id": "...", "type": "index", "status": "running" }
}
```

**响应 400**
```json
{ "ok": false, "error": "不支持的补数类型" }
```

### POST /api/data_status/backfill/{task_id}/cancel
取消运行中的补数任务（当前批次完成后停止）。

**响应 200**
```json
{
  "ok": true,
  "message": "终止信号已发送，当前批次完成后停止（预计 2 分 30 秒）",
  "current_batch": 5,
  "total_batches": 28,
  "eta_seconds": 150
}
```

**响应 400**
```json
{ "ok": false, "error": "任务不在运行中，无法取消" }
```

### GET /api/data_status/backfill/history
补数历史记录。

| 参数 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `limit` | int | | 返回条数，默认 20 |

**响应 200**
```json
{
  "tasks": [{
    "task_id": "bf_kline_20260618_143022",
    "task_type": "kline",
    "task_label": "个股日K线",
    "status": "completed",
    "start_date": "2021-06-16",
    "end_date": "2026-06-18",
    "force": false,
    "progress": { "rows": 3450000, "errors": 12 },
    "elapsed_seconds": 3600
  }]
}
```

### GET /api/data_status/backfill/logs
补数任务日志（分页，按时间倒序，含进行中任务）。

| 参数 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `page` | int | | 页码，默认 1 |
| `page_size` | int | | 每页条数，默认 20 |

**响应 200**
```json
{
  "items": [{
    "task_id": "bf_kline_20260618_143022",
    "task_type": "kline",
    "task_label": "个股日K线",
    "status": "running",
    "start_date": "2021-06-16",
    "end_date": "2026-06-18",
    "force": false,
    "progress": {
      "current_batch": 8,
      "total_batches": 28,
      "stocks_done": 400,
      "stocks_total": 5533,
      "rows": 245000,
      "errors": 2,
      "failed_codes": ["600001", "000002"]
    },
    "started_at": "2026-06-18T14:30:22",
    "completed_at": null,
    "elapsed_seconds": 750,
    "eta_seconds": 1080,
    "error_message": null
  }],
  "total": 5,
  "page": 1,
  "page_size": 20,
  "total_pages": 1
}
```

### GET /api/data-sources/health
数据源健康状态。

**响应 200**
```json
{
  "sources": [
    { "name": "akshare", "priority": 10, "healthy": true, "checked_at": "2026-06-18T08:00:12" },
    { "name": "baostock", "priority": 20, "healthy": false, "checked_at": "2026-06-18T08:00:14" }
  ],
  "active_source": "akshare"
}
```

---

## 8. 设置

### GET /api/settings
获取所有策略配置。

**响应 200**
```json
{
  "strategies": [{ "name": "bollinger_daily", "display": "日布林线", "enabled": true, "params": { "period": 20 } }],
  "deepseek_configured": false
}
```

### POST /api/settings/toggle_strategy
启用/停用策略。
```json
{ "strategy_name": "bollinger_daily", "enabled": true }
```

### POST /api/settings/preference
切换全局交易偏好。
```json
{ "mode": "left" }
```
`mode`: `left`(左侧) / `right`(右侧) / `balanced`(均衡)

### POST /api/settings/update_params
更新策略参数。
```json
{ "strategy_name": "bollinger_daily", "params": { "period": 14 } }
```

### POST /api/settings/deepseek_key
设置 DeepSeek API Key。
```json
{ "api_key": "sk-..." }
```

---

## 9. 飞书

### POST /webhook/feishu
飞书事件回调（消息 + 卡片按钮）。签名校验仅生产环境生效。

---

## 10. WebSocket

### WS /ws/dag
DAG 状态、补数进度、系统指标实时推送。

**连接**：`ws://localhost:8000/api/ws/dag` (dev) / `wss://s.pmlab.top/api/ws/dag` (prod)

**客户端 → 服务端**：发送 `ping`，回复 `{"type":"pong"}`

**服务端 → 客户端**：

| type | 携带字段 | 频率 |
|------|----------|------|
| `dag_status` | `run_status`, `current_run_id`, `current_run_latest`, `has_running` | 有任务 2s / 空闲 30s |
| `dag_log` | `nodes` (日志数组) | 日志变化时 |
| `backfill_progress` | `task_id`, `task_type`, `task_label`, `status`, `progress`, `elapsed_seconds`, `eta_seconds` | 运行中 2s / 状态变更立即 |
| `sys_metrics` | `data` (内存/磁盘/CPU) | 每 30s |

**新连接**：建立后立即发送当前 DAG 状态 + 日志 + 补数任务状态。

**backfill_progress 消息体**：
```json
{
  "type": "backfill_progress",
  "task_id": "bf_kline_20260618_143022",
  "task_type": "kline",
  "task_label": "个股日K线",
  "status": "running",
  "start_date": "2021-06-16",
  "end_date": "2026-06-18",
  "force": false,
  "progress": {
    "current_batch": 8,
    "total_batches": 28,
    "stocks_done": 400,
    "stocks_total": 5533,
    "rows": 245000,
    "errors": 2,
    "failed_codes": ["600001"]
  },
  "started_at": "2026-06-18T14:30:22",
  "elapsed_seconds": 750,
  "eta_seconds": 1080
}
```

**sys_metrics 消息体**：
```json
{
  "type": "sys_metrics",
  "data": {
    "memory_total_mb": 1843,
    "memory_avail_mb": 890,
    "memory_used_pct": 51.7,
    "disk_total_gb": 39,
    "disk_used_gb": 22,
    "disk_avail_gb": 17,
    "disk_used_pct": 56.4,
    "cpu_pct": 23
  }
}
```

---

## 11. 系统监控

### GET /api/system/metrics
获取服务器基本指标（内存、磁盘、CPU），每 30 秒通过 WS 自动推送。

**响应 200**
```json
{
  "memory_total_mb": 1843,
  "memory_avail_mb": 890,
  "memory_used_pct": 51.7,
  "disk_total_gb": 39,
  "disk_used_gb": 22,
  "disk_avail_gb": 17,
  "disk_used_pct": 56.4,
  "cpu_idle": 12345678,
  "cpu_total": 98765432
}
```

---

## 12. 健康检查

### GET /health
```json
{ "status": "ok", "database": "connected", "version": "1.0.0", "env": "dev" }
```

---

## 13. 模型版本管理

### GET /v1/models
模型版本列表，返回所有未软删除版本（按 created_at 倒序）。

**响应 200**
```json
{
  "versions": [{
    "version": "v1.0", "model_name": "BOLL+MACD", "status": "DRAFT",
    "config": {"features":["boll","macd"],"train_start":"2021-01-01",...},
    "best_params": {"5d":{"r2":0.85,"model_path":"data/models/v1.0/xgb_5d.pkl"},...},
    "evaluation_report": {"r2_5d":0.85,"r2_avg":0.82},
    "sharpe": 2.15, "win_rate": 0.58,
    "max_drawdown": null, "annual_return": null,
    "created_at": "2026-06-21 00:08:37", "trained_at": null, "activated_at": null
  }],
  "count": 1
}
```

### GET /v1/models/{version}
单个模型版本详情（含 archived_at）。

### POST /v1/models
创建新模型版本（状态 DRAFT，自动生成版本号 v{major}.0）。

**请求体**
```json
{
  "model_name": "BOLL+MACD多策略",
  "train_start": "2021-01-01", "train_end": "2025-12-31",
  "test_start": "2026-01-01", "test_end": "",
  "features": ["boll","macd","rsi","atr","ma","volume"],
  "ml_enabled": false, "model_type": "xgboost",
  "stop_loss_pct": 8.0, "signal_timeout_days": 20
}
```

**响应 200** `{"ok":true,"version":"v1.0","model_name":"...","status":"DRAFT"}`  
**响应 400** 模型名称为空

### PUT /v1/models/{version}/config
更新 DRAFT 状态模型的四层配置（仅 DRAFT 可编辑）。

**请求体** `{...config fields...}`（完整配置对象，`...cfg` 合并保留现有字段）  
**响应 200** `{"ok":true,"version":"v1.0"}`  
**响应 400** 非 DRAFT 状态

### POST /v1/models/{version}/approve
审批上线：原 ACTIVE → ARCHIVED，当前 PENDING → ACTIVE。  
**响应 400** 非 PENDING 状态

### POST /v1/models/{version}/reject
拒绝模型：PENDING → REJECTED。

### DELETE /v1/models/{version}?mode=soft|hard
删除模型。soft=逻辑删除，hard=物理删除（需无关联数据）。

### GET /v1/models/{version}/delete-check
检查是否可物理删除。返回关联数据统计。

### GET /v1/models/{version}/health
模型健康度最新记录。

### GET /v1/models/{version}/signals
模型信号明细（最近 50 条）。

### GET /v1/indicators/{name}/status
指标表状态（行数+最新日期）。name ∈ {boll,macd,rsi,atr,ma,volume}。

---

## 14. 特征管理

### GET /api/features
特征列表（分页+筛选）。支持 `entity`/`status`/`search` 筛选。

### POST /api/features
新增特征。KEPL 公式自动解析依赖，循环依赖检测。

### GET /api/features/{id}
特征详情（含上游依赖 + 下游引用）。

### PUT /api/features/{id}
更新特征。修改公式时重新解析依赖并级联标记下游 `pending_recalc`。

### DELETE /api/features/{id}
软删除特征。

### POST /api/features/validate
验证 KEPL 公式语法（不保存），返回依赖列表。

### GET /api/features/groups
特征族列表。

### GET /api/features/tags
标签列表。

### GET /api/features/dependency-graph
全量依赖图（nodes + edges），用于 ECharts/D3 渲染。

### GET /api/features/{id}/quality
特征数据质量指标（完整度、新鲜度、stale 警告）。

### GET /api/features/{id}/data
分页预览特征在 `feature_values` 中的实际数值。支持 `code` 筛选。

### POST /api/features/{id}/stats
DAG 回写特征统计。

### PATCH /api/features/{id}/status
手动切换特征状态（含下游检查）。

### POST /api/features/{id}/compute-range
**手动补数**：对单特征在指定日期范围内重新计算。

**请求体**
```json
{ "start_date": "2026-01-01", "end_date": "2026-07-07", "force": false }
```

force=true 全量覆盖已有数据；force=false 跳过已有（ON CONFLICT DO UPDATE）。

**响应**
```json
{ "ok": true, "task_id": "a1b2c3d4", "feature_name": "ma_5", "status": "started" }
```

进度通过 WebSocket `feature_compute_progress` 消息实时推送。

### GET /api/features/{id}/compute-status
查询补数进度。

### POST /api/features/{id}/recompute-stats
**原子级重新诊断**：基于 `feature_values` 现有数据重算总格子/完整度/缺失，不触发计算。

### GET /api/features/{id}/missing-heatmap
缺失热力图数据：最近 N 日 × 缺失率最高 M 只股票的缺失矩阵。

```
GET /api/features/1/missing-heatmap?days=120&top_n=50
→ { days_labels: [...], stock_labels: [...], matrix: [[di,si,0|1],...] }
```

### POST /api/features/check-stats-integrity
全量校验：对比 `features` 表元数据与 `feature_values` 实际行数，不一致的自动标记 `data_anomaly` + 归零。

## 15. 统计模型

### 总格子计算规则
```
总格子 = Σ 每只股票[上市日, min(退市日,今天)] 之间的交易日数
正常缺失 = 停牌天数×股票数 + 股票数×依赖函数最大 lookback
异常缺失 = 总格子 - 正常缺失 - 已计算
完整度 = 已计算 / 总格子
```
- 已剔除未上市/已退市日期
- 停牌和 lookback 窗口期归入正常缺失
- 计算完成后自动恢复 `data_anomaly` → `enabled`（完整度 ≥ 60%）

---

## 20. 统一任务日志 (v2.8)

### GET /api/dag/logs
获取最近任务日志（合并 TaskManager 内存 + dag_run_log 历史表）。

| 属性 | 值 |
|------|-----|
| Auth | 无 |
| Query | `limit`(默认50), `flow_id`(可选) |

**响应 200**
```json
{
  "items": [
    {
      "task_id": "task-a1b2c3d4",
      "task_type": "dag_flow",
      "flow_id": 2,
      "flow_name": "test-2node",
      "status": "completed",
      "progress_pct": 100,
      "nodes": [
        {"node_name": "cron", "status": "success", "rows": 0},
        {"node_name": "kline", "status": "success", "rows": 4520}
      ]
    }
  ],
  "total": 37
}
```

### GET /api/dag/flows/{flow_id}/task-status
查询流程当前是否有活跃任务。

| 属性 | 值 |
|------|-----|
| Auth | Bearer Token |

**响应 200**
```json
{"has_task": true, "task_id": "task-...", "status": "running", "progress_pct": 60}
```

## 21. 模型特征预检 (v2.8)

### GET /api/v1/models/{version}/feature-check
训练前检查特征数据覆盖情况。

| 属性 | 值 |
|------|-----|
| Auth | 无 |

**响应 200**
```json
{
  "ready": false,
  "warnings": ["特征'ma_5'数据截至2026-06-22，早于训练结束2026-07-05"],
  "features": [
    {"name": "ma_5", "start": "2020-01-02", "end": "2026-06-22", "stocks": 5391}
  ]
}
```

## 22. 全局偏好 (v2.8)

### GET /api/settings/preference
读取全局交易偏好。

| 属性 | 值 |
|------|-----|
| Auth | Bearer Token |

**响应 200**
```json
{"mode": "balanced"}
```
