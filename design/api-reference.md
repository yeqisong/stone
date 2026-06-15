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
  "latest_trade_date": "2026-06-12", "close": 13.20,
  "close_hfq": 15.80, "volume": 50000000, "turnover": 2.5,
  "latest_signals": [{ "direction": "buy", "strength": 3, "strategy_name": "bollinger_daily", ... }],
  "history_count": 15, "latest_signal_date": "2026-06-12",
  "fundamentals": { "industry": "金融业", "pe_ttm": 8.5, "pb_mrq": 1.2, "roe": 12.3 }
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
PE/PB/ROE 历史时序 + 分位数。

**响应 200**
```json
{
  "stock_code": "000001",
  "data": [{ "date": "2024-03", "pe_ttm": 7.5, "pb_mrq": 1.1, "roe": 14.2, "pe_percentile": 25.0 }]
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
DAG 状态实时推送。

**连接**：`ws://localhost:8000/api/ws/dag` (dev) / `wss://s.pmlab.top/api/ws/dag` (prod)

**客户端 → 服务端**：发送 `ping`，回复 `{"type":"pong"}`

**服务端 → 客户端**：

| type | 携带字段 | 频率 |
|------|----------|------|
| `dag_status` | `run_status`, `current_run_id`, `current_run_latest`, `has_running` | 有任务 2s / 空闲 30s |
| `dag_log` | `nodes` (日志数组) | 日志变化时 |

**新连接**：建立后立即发送当前状态 + 日志。

---

## 11. 健康检查

### GET /health
```json
{ "status": "ok", "database": "connected", "version": "1.0.0", "env": "dev" }
```
