# 03 — API 接口文档

> K道（Stone）· 文档基准：代码版本 v3.2 · 共 **103 个 HTTP 端点 + 1 个 WebSocket**
>
> 认证标记：🔒 = `Depends(get_current_user)` / `optional_auth`（实际同样强制）；无标记 = 无需认证。
> 路由注册：`app/main.py` L110-123；dag_types/dag_flows 自带 `/api/dag` 前缀；飞书挂 `/webhook/feishu`。

---

## 1. 应用入口（main.py）

| 方法+路径 | 功能 | 认证 | 请求 | 响应 |
|---|---|---|---|---|
| `POST /api/login` | 登录，签发 JWT（限 5次/分/IP） | — | `{username, password}` | `{ok, token, username}`；401/429 |
| `GET /health` | 健康检查 | — | — | `{status:"ok"/"degraded", database, version, env}` |

## 2. 飞书 Webhook（feishu/webhook.py）

| 方法+路径 | 功能 | 认证 | 说明 |
|---|---|---|---|
| `POST /webhook/feishu` | 事件回调（消息→AI、url_verification、卡片按钮） | 签名校验（配置 SECRET 时 HMAC-SHA256） | challenge 应答 / AI 回复 / 卡片回调落库 |

## 3. 持仓 portfolio（api/portfolio.py）

| 方法+路径 | 功能 | 认证 | 请求 | 响应 |
|---|---|---|---|---|
| `GET /api/portfolio` | 当前持仓+浮动盈亏 | — | — | `{positions[...], total_value, total_pnl, count}` |
| `POST /api/portfolio` | 新增持仓（已存在加权平均） | 🔒 | `{stock_code, quantity, cost_price, notes}` | `{ok}` |
| `PUT /api/portfolio/{code}` | 更新数量/成本/备注 | 🔒 | `{quantity?, cost_price?, notes?}` | `{ok, updated}` |
| `DELETE /api/portfolio/{code}` | 删除（软删除） | 🔒 | — | `{ok, deleted}` |
| `GET /api/portfolio/{code}/history` | 加减仓历史（≤50 条） | — | — | `{records, position}` |

## 4. 市值树图 treemap（api/treemap.py）

| 方法+路径 | 功能 | 认证 | 请求 | 响应 |
|---|---|---|---|---|
| `GET /api/treemap_latest_date` | 树图最新数据日期 | — | — | `{latest_date}` |
| `GET /api/treemap_data` | 树图数据（parent 空=全量，指定=下钻） | — | `trade_date`(必填), `metric=mcap`(mcap/volume/amount/pe), `parent=""` | `{trade_date, children[{id,name,value,chg_pct,...}]}` |

## 5. 买点信号 signals（api/signals.py）

| 方法+路径 | 功能 | 认证 | 请求 | 响应 |
|---|---|---|---|---|
| `GET /api/buy_signals` | 指定日期融合买点（combined=true） | — | `signal_date`(默认最近), `top_n=20`(1-100) | `{signal_date, scanned, total_signals, signals[...]}` |

## 6. 个股详情 stock（api/stock.py）

| 方法+路径 | 功能 | 认证 | 请求 | 响应 |
|---|---|---|---|---|
| `GET /api/stock/{code}/detail` | 详情（行情+信号+基本面） | — | `type?`(stock/index/etf) | 详情 JSON；404 |
| `GET /api/stock/{code}/history` | 历史信号（分页） | — | `page=1, page_size=20`(5-50) | `{signals[], total, ...}` |
| `GET /api/stock/{code}/kline` | K线+BOLL/RSI/MACD（复权切换） | — | `days=120`(30-500), `adjust=none`(qfq/hfq), `type?` | `{kline[{trade_date,open,...,boll_*,rsi,dif,dea,macd_bar}]}` |
| `GET /api/signal/stats` | 信号效果统计（胜率/趋势/分布/行业） | — | `days=90`(30-365) | `{overview, daily_trend, return_distribution, by_industry, top_stocks}` |
| `GET /api/stock/{code}/pe_history` | 历史 PE/PB/ROE 时序 | — | — | `{data[{date, pe_ttm, pb_mrq, roe}]}` |

## 7. 证券列表 stocks（api/stocks.py）

| 方法+路径 | 功能 | 认证 | 请求 | 响应 |
|---|---|---|---|---|
| `GET /api/stocks` | 分页列表（排序） | — | `page, page_size=20`(10-100), `keyword`, `category=stock`(stock/index/etf/bond/all), `order_by=trade_date`(price/chg_pct/pe_ttm/market_cap), `order_dir=desc` | `{stocks[], total, total_pages}` |

## 8. 系统设置 settings（api/settings.py）

| 方法+路径 | 功能 | 认证 | 请求 | 响应 |
|---|---|---|---|---|
| `GET /api/settings` | 全部配置（Key 脱敏） | — | — | `{indicators, strategies, preference, deepseek_configured}` |
| `POST /api/settings/toggle_strategy` | 启停策略 | 🔒 | `{strategy_name, enabled}` | `{ok}` |
| `GET /api/settings/preference` | 读偏好 | 🔒 | — | `{mode}` |
| `POST /api/settings/preference` | 切偏好（保留 deepseek_key） | 🔒 | `{mode: left/right/balanced}` | `{ok, mode}` |
| `POST /api/settings/update_params` | 更新策略参数 | 🔒 | `{strategy_name, params}` | `{ok}` |
| `POST /api/settings/deepseek_key` | 设置 AI Key | 🔒 | `{api_key}` | `{ok}` |

## 9. 数据状态/DAG/监控 status（api/status.py）

| 方法+路径 | 功能 | 认证 | 请求 | 响应 |
|---|---|---|---|---|
| `GET /api/data_status` | 数据状态总览（日历+完整度） | — | `month?`(YYYY-MM) | `{overview, calendar, missing_dates, download_log, today_strategy, data_tables}` |
| `POST /api/dag_trigger` | 手工触发 DAG 节点 | 🔒 | `{node="stats", date, include_downstream=true}` | `{ok, task_id}` 或 `{busy:true}` |
| `POST /api/refresh_stats` | 全库统计（TaskManager 任务） | 🔒 | — | `{ok, task_id}` |
| `GET /api/dag_status` | DAG 状态+看门狗（>10min 标超时） | — | — | `{run_status, current_run_id}` |
| `GET /api/trade_calendar` | 交易日历 | — | `year?` | `{year, total, calendar}` |
| `POST /api/data_status/sync_date` | 指定日期采集 | 🔒 | `{date, node="cron", mode="quick"}` | `{ok, task_id}` |
| `POST /api/dag_terminate` | 终止任务 | 🔒 | `{run_id}` | `{ok, status:"terminated"}` |
| `GET /api/dag_config` | （已废弃） | — | — | `{structure: []}` |
| `GET /api/dag_logs` | 节点日志（log_id 单查/最新组） | — | `log_id?` | `{ok, nodes[]}` |
| `GET /api/data_status/sync_status` | sync_date 任务状态 | — | `task_id` | `{ok, task}` |
| `GET /api/data-sources/health` | 数据源健康+活跃源 | — | — | `{sources[], active_source}` |
| `POST /api/data_status/backfill` | 启动历史补数 | ⚠️无 | `{type(kline/index/etf/fund/calendar/stock_master), start_date, end_date, force=false, batch_size=20}` | `{ok, task_id}` / `{busy:true}` |
| `POST /api/data_status/backfill/{task_id}/cancel` | 取消补数 | ⚠️无 | — | 取消结果 |
| `GET /api/data_status/backfill/history` | 补数历史 | — | `limit=20` | `{tasks[]}` |
| `GET /api/data_status/backfill/logs` | 补数日志（分页倒序） | — | `page, page_size=20` | 任务列表 |
| `GET /api/stock_fund_list` | 证券主档+最新基本面联表 | 🔒 | `stock_type=stock, page=1, page_size=50, search=""` | `{items[], total}` |
| `GET /api/system/metrics` | 服务器指标（内存/磁盘/CPU/DB） | ⚠️无 | — | 指标 JSON（含 db.tables 明细） |

> ⚠️ = 审查确认的无认证敏感端点，待加固（见 01 文档 §9）。

### WebSocket：`WS /api/ws/dag`

- 认证：`?token=<JWT>`（prod 强制，无效 close 4001；dev 放行）。心跳：客户端发 `"ping"` → 回 `{"type":"pong"}`。
- 服务端推送消息类型：

| type | 内容 | 触发 |
|---|---|---|
| `dag_status` | `{run_status{node:{status,rows,detail}}, current_run_id, has_running}` | 状态变化 / 2s |
| `dag_log` | `{nodes[{date,node,status,rows,detail,started_at,finished_at}]}` | 日志变化 |
| `task_progress` | TaskManager 任务 to_dict（含 nodes 进度） | 任务状态变更 |
| `backfill_progress` | 活跃补数任务（批次/行数/ETA） | 每批完成 |
| `feature_compute_progress` | `{feature_id, progress_pct, current_date, status, error}` | 特征补数 |
| `sys_metrics` | `{memory_*, disk_*, cpu_pct, db_size_mb}` | 每 30s |

## 10. 模型管理 models（api/models.py，前缀 /v1/models）

| 方法+路径 | 功能 | 认证 | 要点 |
|---|---|---|---|
| `GET /api/v1/models` | 版本列表 | — | Query `entity=stock` |
| `GET /api/v1/models/{v}` | 版本详情 | — | 404 |
| `POST /api/v1/models` | 创建（DRAFT，自动版本号） | 🔒 | Body：model_name 必填 + entity/train-test 区间/feature_names/optuna_trials/资金参数/六层策略配置 |
| `GET .../{v}/health` | 健康度最新记录 | — | 无记录默认 HEALTHY |
| `GET .../{v}/diagnosis` | 质量诊断（拟合度/收益/盈亏比/vs指数/集中度） | — | — |
| `GET .../{v}/signals` | 信号明细（最近 50） | — | — |
| `GET .../{v}/delete-check` | 删除前关联数据检查 | — | `{can_physical_delete, related_data}` |
| `DELETE .../{v}` | 删除（mode=soft/hard） | 🔒 | ACTIVE 不可删 |
| `GET .../{v}/feature-check` | 特征预检（60/20/20 三阶段），缓存 config | — | Query `force=false` |
| `POST .../{v}/approve` | 审批上线（旧 ACTIVE→ARCHIVED） | 🔒 | 仅 PENDING |
| `PUT .../{v}/config` | 更新配置（仅 DRAFT） | 🔒 | — |
| `POST .../{v}/stop` | 停止训练→DRAFT | 🔒 | 仅 TRAINING |
| `POST .../{v}/train` | 触发训练（TaskManager 跟踪） | 🔒 | DRAFT/REJECTED |
| `POST .../{v}/retrain` | REJECTED 重训→DRAFT | 🔒 | — |
| `POST .../{v}/reject` | 拒绝→REJECTED | 🔒 | 仅 PENDING |
| `GET .../{v}/quality-dashboard` | 特征质量仪表盘 | — | 当前占位数据 |
| `POST .../{v}/strategy-scan` | 策略参数网格扫描（后台） | 🔒 | `{param_grid, val_start, val_end, hold_days=10}` |
| `GET .../{v}/strategy-scan/{task_id}` | 扫描进度+热力图 | — | — |
| `POST .../{v}/strategy-scan/{task_id}/apply` | 应用最优参数 | 🔒 | 扫描完成后 |
| `POST .../{v}/attribution` | 归因分析（三基线+Brinson） | 🔒 | `{val_start, val_end}` |

## 11. 函数管理 functions（api/functions.py，前缀 /functions）

| 方法+路径 | 功能 | 认证 | 要点 |
|---|---|---|---|
| `GET /api/functions` | 列表（分页筛选） | — | `category/status/search/page/page_size` |
| `POST /api/functions` | 新增（AST 安全校验：禁 for/while、import、eval/exec/getattr 等） | 🔒 | `{name, source_code, parameters, lookback...}` |
| `GET /api/functions/{id}` | 详情 | — | — |
| `PUT /api/functions/{id}` | 编辑（内置不可改，发布自动版本快照） | 🔒 | — |
| `POST /api/functions/ai-chat` | AI 辅助生成代码 | ⚠️无 | `{messages}` |
| `POST /api/functions/test-run-temp` | 临时试运行（隔离子进程） | 🔒 | `{source_code, parameters, category}` → `{ok, status, elapsed_ms, preview}` |
| `POST /api/functions/{id}/test-run` | 沙箱试运行（需先保存） | 🔒 | 同上 |
| `GET /api/functions/{id}/versions` | 版本历史 | — | — |
| `POST /api/functions/{id}/rollback/{ver}` | 回滚 | 🔒 | — |
| `DELETE /api/functions/{id}` | 删除（仅草稿） | 🔒 | ⚠️ 已知 bug：被 L485 装饰器堆叠遮蔽，实际返回版本历史（见 01 §9） |

## 12. 特征管理 features（api/features.py，前缀 /features）

| 方法+路径 | 功能 | 认证 | 要点 |
|---|---|---|---|
| `GET /api/features` | 列表（实体/状态筛选+分页） | — | items 含质量统计列 |
| `POST /api/features/validate` | 验证 KEPL 公式+提取依赖 | — | `{formula, target_entity}` |
| `POST /api/features` | 新增（唯一性/解析/循环检测） | 🔒 | `{feature_name, formula, target_entity, feature_group, tags...}` |
| `GET /api/features/groups` / `tags` | 特征族/标签列表 | — | — |
| `GET /api/features/dependency-graph` | 全量依赖图 | — | `{nodes[], edges[]}` |
| `GET /api/features/{id}` | 详情（上游+下游引用） | — | — |
| `GET /api/features/{id}/quality` | 质量指标 | — | 完整度/缺失格/过期警告 |
| `PUT /api/features/{id}` | 更新（改公式→级联 pending_recalc） | 🔒 | — |
| `DELETE /api/features/{id}` | 软删除（deprecated，有下游拒绝） | 🔒 | — |
| `GET /api/features/{id}/data` | 数据预览（分页） | — | `code?, page, page_size=50` |
| `POST /api/features/{id}/stats` | DAG 回写质量统计 | 🔒 | <0.6 标 data_anomaly |
| `PATCH /api/features/{id}/status` | 手动切状态（含下游检查） | 🔒 | — |
| `POST /api/features/{id}/compute-range` | 单特征区间补数（后台+WS） | 🔒 | `{start_date, end_date, force}` |
| `GET /api/features/{id}/missing-heatmap` | 缺失热力图（120日×TopN） | — | `days=120, top_n=50, mode=top_missing` |
| `POST /api/features/{id}/recompute-stats` | 重算诊断（不触发计算） | 🔒 | — |
| `GET /api/features/{id}/compute-status` | 补数进度查询 | — | — |
| `POST /api/features/check-stats-integrity` | 统计一致性校验修复 | 🔒 | — |

## 13. KEPL 解析（api/kepl.py）

| 方法+路径 | 功能 | 认证 | 请求 |
|---|---|---|---|
| `POST /api/kepl/parse` | 解析公式返回 AST+错误 | — | `{formula, entity="stock"}` |

## 14. DAG 节点类型（api/dag_types.py，前缀 /api/dag）

| 方法+路径 | 功能 | 认证 |
|---|---|---|
| `GET /api/dag/node-types` | 已注册节点类型（含 has_function 状态） | — |
| `GET /api/dag/node-types/{name}` | 单节点详情（含 sub_steps 内部依赖） | — |

## 15. DAG 流程编排（api/dag_flows.py，前缀 /api/dag）

| 方法+路径 | 功能 | 认证 | 要点 |
|---|---|---|---|
| `GET /api/dag/flows` | 流程列表（含节点数） | — | — |
| `POST /api/dag/flows` | 创建（7 项校验+版本快照） | 🔒 | `{flow_name, nodes[{node_name, deps, position}], cron_expr}` |
| `GET /api/dag/flows/{id}` | 详情（含版本历史） | — | — |
| `PUT /api/dag/flows/{id}` | 更新（自动新版本） | 🔒 | `{nodes?, cron_expr?, change_log}` |
| `DELETE /api/dag/flows/{id}` | 删除（级联版本） | 🔒 | — |
| `POST /api/dag/flows/{id}/publish` | 发布 draft→published | 🔒 | — |
| `POST /api/dag/flows/{id}/unpublish` | 下线 | 🔒 | — |
| `GET /api/dag/flows/{id}/task-status` | 活跃任务查询 | — | `{has_task, ...}` |
| `GET /api/dag/flows/{id}/versions` | 版本历史 | — | — |
| `POST /api/dag/flows/validate` | 仅校验不保存 | ⚠️无 | `{nodes[{node_name, deps}]}` |
| `POST /api/dag/flows/{id}/execute` | 触发执行（后台+TaskManager） | 🔒 | `{trade_date?}`；429 同流程互斥 |
| `GET /api/dag/logs` | 任务日志（内存+历史合并） | — | `limit=50(≤200), flow_id?` |

---

## 附：错误码约定

- `400` 参数校验失败（Pydantic / 业务白名单）
- `401` 未认证 / 密码错误（前端拦截器：非 login 接口的 401 → 清 token 刷新登录页）
- `404` 资源不存在
- `429` 任务互斥（同流程运行中 / 补数 busy / 登录限流）
- `500` 服务端异常（loguru 记录完整堆栈）
