# 个股买卖点监测系统 (K道 / Stock Monitor)

Python + FastAPI 后端 + Vue 3 前端 + PostgreSQL 的全栈 A 股量化监测系统，支持 K 线自动采集、DAG 流水线调度、KEPL 特征引擎、XGBoost 模型训练与信号、AI 飞书 Bot、实时 WS 推送及 tushare 配额管理。

## Project

- **Stack**: Python 3.11, FastAPI, SQLAlchemy (async+sync), PostgreSQL 15, Redis 7, Vue 3, Vite, Naive UI, ECharts, Vue Flow, Monaco Editor
- **Entry point**: `app/main.py` — FastAPI application, lifespan 中初始化 DB + entity_stats + WS 广播 + cron
- **Config**: `app/config.py` — pydantic-settings, 读取 `.env` 文件（含 TUSHARE_TOKEN）
- **Frontend**: `web-v2/` — Vue 3 + Vite, dev proxy → 后端 :8000
- **生产地址**: https://s.pmlab.top

## Commands

| 用途 | 命令 |
|------|------|
| 启动后端 | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 后端开发(热重载) | `uvicorn app.main:app --reload --port 8000` |
| 前端开发 | `cd web-v2 && npm run dev` (port 3000, proxy → 8000；旁路端口用环境变量 `FRONTEND_PORT=3001 BACKEND_ORIGIN=http://localhost:8101`) |
| 前端构建 | `cd web-v2 && npm run build` |
| 运行测试 | `pytest` |
| 仅单元测试 | `pytest tests/unit/ -v` |
| 仅集成测试 | `pytest tests/integration/ -v` |
| Docker 启动 | `docker-compose up -d` |

> ⚠️ `scripts/pipeline.py <date> <node>` 与 `daily_crawl.sh` 属旧接口：DAG 执行一律通过 dag_flows 动态流程（数据库编排）触发，`daily_crawl.sh` 已改为触发已发布流程。

## Architecture

### 数据源架构（v3.2.1：主源 + 补充器）

```
TuShare（唯一主源，按交易日全市场拉取，3 次调用/天）
  ├─ daily / index_daily / fund_daily  → 日K线（close_hfq=close×adj_factor）
  ├─ daily_basic                       → 基本面 22 字段 + 换手率
  ├─ stock_basic                       → stock_master（1次/小时，daily 降级）
  └─ TushareQuota 配额计数器（50次/分、8000次/天，北京时间自然日）
       └─ tushare_quota 表 + 状态页配额卡片 + 补数配额预算（<10次自动收尾续跑）

Baostock（字段补充器 + 交易日历唯一源；不可用时主字段照常入库）
  ├─ fetch_fundamentals_extra → stock_fundamentals 的 ROE/营收/净利（分批补数）
  ├─ fetch_etf_hfq            → 补充 ETF 后复权 close_hfq（tushare fund_adj 需高积分）
  └─ get_trade_calendar       → 交易日历同步（trade_calendar.py）
```

### 核心模块

- **`app/`** — FastAPI 后端
  - `app/main.py` — 入口，16 个 router + CORS + 前端静态 + lifespan（init_db→entity_stats→模型恢复→WS→cron）
  - `app/config.py` — 配置管理（Settings）
  - `app/db/` — 数据库连接（connection.py 双引擎）+ 59 个 DDL 常量（5 个视图 + 表；feature_values 按年分区，库内 public 实有 88 表 / 5 视图）+ init_db 幂等迁移（schema.py）
  - `app/api/` — REST API 路由：
    - `portfolio.py` / `treemap.py` / `signals.py` / `stock.py` / `stocks.py` — 持仓/树图/信号/详情/列表
    - `market.py` — 大盘/市场详情（/api/market/*：overview 总览、valuation 指数估值+分位、moneyflow 全市场资金聚合、breadth 市场宽度、margin 两融、macro 宏观时序；只读聚合 + 模块级 TTL 缓存）
    - `risk.py` — 风控规则与告警查询（/api/risk/*）
    - `status.py` — 数据状态/DAG 触发/WS/系统指标/**tushare_quota**
    - `settings.py` / `models.py` / `functions.py` / `features.py` / `kepl.py` — 配置/模型/函数/特征/KEPL
    - `dag_types.py` / `dag_flows.py` — DAG 节点类型 / 动态流程编排 + 执行
    - `news.py` — 新闻资讯（GET /api/news 列表筛选分页、/api/news/health 各源三态、/api/news/{id} 详情；个股侧 `/stock/{code}/news` 在 stock.py）
  - `app/risk.py` — 风控规则引擎（止损/回撤熔断/行业上限；规则存 strategy_config，paper_portfolio 节点每日评估，告警走 WS 推送）
  - `app/auth/` — JWT 认证 + 登录（单用户，环境变量密码）
  - `app/task/` — TaskManager 统一任务机制（并发≤5、同流程互斥、WS 推送）
  - `app/feishu/` — 飞书 Bot webhook（签名校验、对话历史、确认卡片闭环）
  - `app/ai/` — DeepSeek AI 客户端（8 个 Function Calling 工具 + 降级菜单）
  - `app/agent/` — 站内问答 Agent（ReAct + SSE）：`runtime.py` 多轮「思考→工具→观察」（默认不限步数 MAX_STEPS=0，可随时中断，同会话串行，轨迹落 agent_sessions/agent_messages）；`tools.py` 13 个工具（只读 SQL/表结构/个股全景/资讯检索/沙箱 Python·Node/pip·npm/DAG 列表·建流程·触发）；`sandbox.py` 每会话工作区 `data/agent_ws/<sid>/`（私有 pylibs/node，超时+2GB 上限，非容器隔离）；`_child.py` 子进程 rlimit 启动器（`preexec_fn` 在多线程 uvicorn 下会 SIGSEGV，必须 `start_new_session`）；API `app/api/agent.py`，前端 `AgentView.vue`（tab i=问答）；`skills.py` 技能体系（`skills/<name>/SKILL.md` 内置随仓库版本管理 + `data/skills/` 用户技能，frontmatter 的 description 进系统提示做渐进披露，命中后 read_skill 读全文；工具 list_skills/read_skill/save_skill；GitHub 导入器 preview/confirm 人工审核后入库；内置含个股尽调/信号复盘/DAG搭建/数据健康/扩展数据(Tushare直查)/仓位计算/回撤纪律/交易复盘/盘前清单/实时分时）；会话工作区 `data/agent_ws/` 生成的图片经 `/api/agent/sessions/{sid}/files/*` 在对话内展示
  - `app/kepl/` — KEPL 表达式解析器（Lark LALR(1) + AST + 时间序列/截面函数）
  - `app/signal.py` — DAG ↔ WebSocket 解耦桥梁（asyncio Event + 线程安全唤醒）
  - `app/lineage.py` — 数据血缘台账（旁路观测：factor_heal/零价修复/特征重算/复评/训练/滚动重训事件落 `data_lineage` 表，MB 级元数据；`log_event` 失败绝不影响主链路）；`model_data_drift` 回答「模型训练后数据又被改过吗」；API 在 `app/api/lineage.py`（GET /api/lineage、/api/lineage/model/{ver}）
  - `app/market_realtime.py` — 实时大盘聚合（展示在实时页 `#/rt` 的盘面区与分析带，design/13·14）：后台 loop 周期刷新模块级缓存（新浪全市场宽度 / 腾讯分时 / 新浪 5 分钟量能 / 东财涨停池·板块·资金流），`/api/market/realtime` 只读缓存 + slot 重放、不触发外部请求；盘面异动告警（`mk%` → risk_alerts → WS，按 `research.market.view` 过滤）；日终涨停梯队归档 `zt_pool_daily`。**数据所属交易日必须校验**（东财/腾讯对无数据日期静默回退，design/13 §10.1）
  - `crawler/em_quote.py` — 东财 push2/push2ex 行情抓取（只许这两个域；push2his 有 TLS 指纹墙），统一走 `crawler.news.sources.em_get` 串行闸门
  - `app/regime_gate.py` — regime 空仓闸门单一事实源（pipeline / eval_version / scan_gate 经别名引用）；`app/buy_check.live_gate` 三源组合实时闸门（市场闸门 × regime × 盘面情绪，目前仅展示，缺数据返回 `unknown` 不兜成放行）
  - `app/peg.py` / `app/api/peg.py` — PEG 纯函数与端点（`/api/market/peg` 指数成分截面、`/api/stock/{code}/peg_history` 个股历史带）：G = G_hist（5 年归母净利 CAGR）打底、一致预期单边向上收缩 50%；**一致预期机构 ≥3 才算完整口径，截面与历史序列同一判定**（`MIN_CONSENSUS_ORGS`）

- **`crawler/`** — 数据采集层
  - `adapters/` — `tushare_adapter.py`（主源，按交易日全市场）+ `baostock_adapter.py`（补充器）+ `tushare_quota.py`（配额计数）+ `manager.py`（主源直取 + 补充器健康缓存）+ `base.py`（标准化 dataclass）
  - `backfill.py` — 历史补数管理器（按交易日逐日拉取、断点续传按日期完整度 80% 阈值、配额预算、补充器分批提交+可取消）
  - `trade_calendar.py` — 交易日历同步（baostock 唯一源）
  - `writers.py` — 批量 UPSERT（kline/fundamentals）+ 市值补全 + 基本面 ROE 补充 + 新闻（news_item/news_stock 批量 UPSERT + 幂等键/跨源哈希查询）
  - `news/` — 资讯采集（`sources.py` 六源公开 JSON 接口 HTTP 抓取 + `em_get()` 东财节流、`match.py` 关注池文本→个股匹配（纯函数）、`collect.py` 编排：快讯主备切换 / 关注池分片轮转 / 巨潮 orgId 映射 / 两层幂等 flush）；连通性自检 `python -m crawler.news.sources`

- **`scripts/`** — 调度与计算层
  - `pipeline.py` — 全部 dag_task_* 节点函数 + NODE_FN_MAP（14 节点）+ 模型训练 + generate_stats；**模型评估链路已抽为模块级函数**（`prepare_model_frame` 预处理+标签 / `build_targets` 标签 / `run_training_backtest` 回测内核 / `pred_score` 预测入口），训练节点与复评脚本共用同一实现；v3.9.2 起训练支持 `selection_cv`（purged time-series CV 超参选择，折前 embargo ≥ 标签前瞻，启用后 val 不参与选择成为诚实 OOS）与 `ensemble.seeds`（多种子平均，SeedEnsemble/ProbaSeedEnsemble 单 pkl 对 pred_score 透明——回归成员不定义 predict_proba 否则 hasattr 探测误入概率分支）；模块级还有 `cross_sectional_rank_ic`/`benchmark_window_stats`/`compute_gap_decomposition`（差距三分解，供训练与复评共用）
  - `dag.py` — 轻量 DAG 调度器（DagExecutor + DagNode，计划-执行模型，并行拓扑执行）
  - `feature_compute.py` — KEPL→pandas 特征计算引擎（lookback 扩展、COPY 流式写入、截面函数）
  - `eval_version.py` — 对**已训练**模型重跑评估（不重训），覆盖 backtest_records / backtest_daily_records / model_versions 指标；改引擎口径或修数据后想拿干净数字就用它，不必等 40 分钟重训。默认跑 T+0/T+1 双口径四遍回测 + RankIC 三窗 + 基准环境，报告带 sample_domain（三切分/成交时点/成本参数）、逐周期成本拖累与 gap_decomposition（Val-Test 差距三分解：执行口径/环境/模型排序力）；`--end` 钉死窗口做复现对照（窗随日历前移时切分日会漂移，数字微动属正常）
  - `repair_zero_prices.py` — 存量零价停牌行修复（沿用上一收盘；默认试运行，`--apply` 执行）
  - `repair_adj_factor.py` — 复权因子一致性自愈（tushare 回溯性重定基 → 半修正窗口造成跨界假跳变；`--check` 体检、`--apply` 重写 close_hfq、`--apply --features` 连特征重算）。日常由 DAG 节点 `factor_heal` 自动跑（kline 之后、feature_compute 之前）
  - `llm_batch.py` — 批量 LLM 公共件（JSON 数组提取 + 幻觉 id 回收 + RPM 滑动窗口闸门 + 线程池 + 折半重试），雪球打分与资讯总结共用；**并发只在 LLM 调用，落库固定回主线程**（psycopg2 连接不可跨线程）
  - `news_summary.py` — 资讯逐条总结（`PROMPT_VER='n1'`：一次调用出摘要/重要度 1-3/主题/情绪；只取未总结的 → 幂等自愈）；`summarize_pending()` 为 CLI 与 DAG 节点共用入口
  - `xueqiu_sentiment.py` — 雪球帖 LLM 情绪打分（`heat≥5` 取热帖；评分逻辑已改为引用 `llm_batch`）
  - `scan_gate.py` — 闸门前沿重扫（熔断档位 × regime 空仓闸门 × trailing × 持有期），完全复用 eval_version 载入路径与 pipeline 四件套；跑前先复现现行档锚点数字，锚不中说明 harness 有 bug 网格作废；结果 CSV 落 data/，不改生产配置
  - `backfill_zt_pool.py` — 涨停梯队历史回补（东财 push2ex 历史日期；深度约 15 个交易日，更早返回空池、跳过不写）
  - 一致预期数据链（PEG 底座）：`crawler/consensus.py`（`compute_consensus_daily`，纯库内聚合：近 180 天 Q4 预测按 (股票, 机构, 分析师, 财年) 取最新一份 → 中位数）+ DAG 节点 `report_rc`（券商预测；tushare 2000 积分档仅 **10 次/天**，配额重置实测在凌晨 02:00 前后）→ `income_daily`（利润表，按披露计划逐票拉全历史）→ `consensus_daily`；流程「一致预期」23:30、「一致预期回补」22:30/00:30/01:30/02:30（命中配额重置的那次用满 10 次）。回补脚本 `backfill_report_rc.py`（历史每 20 交易日抽样一天）/ `backfill_income.py`（逐票，断点 `.backfill_income_progress.json`）/ `backfill_consensus.py`。**拉取失败抛 `SourceFetchError`，不得当成空结果登记**
  - `cron_scheduler.py` — cron 定时触发（last_run_at 幂等，60s 扫描）
  - `daily_crawl.sh` — 触发 dag_flows 已发布流程（不再直接调用采集）

- **`strategy/`** — 策略工具
  - `indicators.py` — 纯 numpy/pandas 技术指标（RSI 除零处理 / BOLL ddof=0 对齐通达信）

- **`web-v2/`** — 前端 V2
  - Vue 3 + Vite + Naive UI + ECharts + Pinia + Vue Flow (DAG 流程图) + Monaco Editor (代码编辑)
  - 页面组件：Login, Portfolio, Treemap, Signals, Stocks, Detail, Status, ModelView, FunctionView, FeatureView/FeatureDetail, DagFlowEdit, FlowRunView, FlowLogView, StockFundView, DagNode, NewsView, RealtimeView(实时页) + `realtime/Rt*`(决策线/盘面区/持仓区/机会区/分析带/图表容器；原 MarketRealtimeView 已拆入), MarketPeg(大盘页 PEG 面板), MarketSentiment(情绪条+闸门 chip)
  - 5 个 Pinia Store: `auth`, `nav`, `model`, `market`, `theme`
  - Tab 路由: p=持仓, rt=实时(盘中作战台 design/14; `#/kr`、`#/stocks?tab=radar`、树图 `mode=intraday` 三种旧链接重定向到此), m=树图, s=信号, l=个股列表, n=资讯, x=状态, a=模型, f=函数, e=特征, g=DAG, u=证券主档, d=详情, v=特征详情, q=日志, r=流程查看
  - 自研 hash 路由（stores/nav.js），无 vue-router；WS 监听 `addWsListener` 返回取消函数（onUnmounted 必清理）

- **`nginx/`** — Nginx 配置（default.conf 容器内 / host-nginx.conf 宿主机）

- **`tests/`** — 测试（unit/integration/e2e）

### 数据流

```
TuShare 按交易日全市场 → crawler/adapters（配额计数）→ PostgreSQL
                                      │
                    DAG flows 动态流程（dag_flows 表编排）
                      ├─ kline/index/etf/fund/stock_master（tushare 主源）
                      ├─ factor_heal（复权因子一致性自愈：假跳变检测→重写 close_hfq→重算污染窗特征）
                      ├─ Baostock 补充器（ROE/ETF复权，补数场景分批）
                      ├─ treemap/stats/completeness/entity_stats（统计表缓存）
                      ├─ feature_compute（KEPL → feature_values）
                      ├─ model_signal / model_health（ACTIVE 模型）
                      └─ news_crawl / news_summary（财联社等六源 → news_item → LLM 总结，每 20 分钟）
                                      ↓
              Vue 前端 ← REST API + WS 推送（配额/任务/补数进度实时）
                 ↓
             飞书 Bot (DeepSeek AI 对话)
```

### 关键数据表（SQL schema in `app/db/schema.py`, 61 个 DDL 常量 / 库内 public 90 表 5 视图）
- **行情**: `trade_calendar`, `stock_master`, `daily_quote`, `index_daily_quote`
- **基本面**: `stock_fundamentals`(PK: code+trade_date), `stock_fundamentals_history`
- **业务**: `portfolio`, `portfolio_history`, `signal_history`, `stock_treemap_cache`
- **特征/函数**: `features`, `feature_values`, `functions`, `function_versions`
- **模型**: `model_versions`, `training_trials`, `model_health`, `version_comparisons`, `backtest_records`, `backtest_trades`, `backtest_daily_records`（逐日净值 + 持仓快照，伪回撤复盘用）
- **DAG**: `dag_config`, `dag_run_log`, `dag_flows`, `dag_flow_versions`, `backfill_tasks`
- **统计/系统**: `daily_completeness`, `data_stats_cache`, `entity_stats`, `system_metrics`, `strategy_config`, **`tushare_quota`**, `data_lineage`（血缘台账）
- **风控**: `risk_alerts`（规则引擎告警：止损/回撤熔断/行业上限，WS→Chrome 通知）
- **资讯**: `news_item`（原文：四源六源幂等键 `(source, source_id)`，**不分区**）、`news_stock`（资讯↔个股，`rel=src|match`）、`news_summary`（LLM 派生：摘要/重要度/主题/情绪，**未总结=NULL 不是 0**）、`news_crawl_state`（各源游标与健康）、`news_poll_state`（关注池轮转 + 巨潮 orgId 缓存）；视图 `v_focus_pool`（持仓∪自选）与 `v_news_feed`（原文 LEFT JOIN 总结）。第 3 层（资讯×雪球交叉）只预留字段未实现，见 `design/07`
- **宏观/估值**: `macro_indicator`（CPI/PPI/GDP/M0M1M2/SHIBOR，value=同比/增速）、`index_valuation`（7 大指数 PE/PB/市值/换手）——`macro_daily` DAG 节点每日 23:05 采集（tushare 2000 积分实测接口）
- **拓展**: `stock_moneyflow`（已接入：每日 DAG 节点 + KEPL 字段 + 2010 起历史回补完成）、`stock_top_list` / `stock_margin_detail` / `stock_holder_number`（表就绪，采集链路未接入）、`stock_hk_hold`（北向个股披露 2024-08 停止，数据源失效，保留表结构）
- **实时盘 / PEG**: `zt_pool_daily`（涨停梯队日终收盘终值，JSONB 全量明细）、`stock_treemap_intraday`（10 分钟全市场 slot 快照，保留 5 日；当日情绪轨迹重放用，个别时段会漏 slot）、`stock_report_rc`（券商盈利预测明细，np 单位元）、`stock_income`（利润表，保留同一报告期的修订版本）、`stock_consensus_daily`（每日一致预期快照，PK 股票+日期）。**一致预期是稀疏样本口径**：历史靠每 20 交易日抽样，180 天窗口内只有 6~7 个发布日，`org_count` 系统性偏低（完整口径覆盖很低），详见 `scripts/backfill_report_rc.py` 文档

## Conventions

- **语言**: 注释/日志使用中文；代码标识符使用英文
- **数据库**: 原生 SQL（SQLAlchemy Core + text()），非 ORM；snake_case 表名，按功能前缀
- **API**: FastAPI Router 模式，统一前缀 `/api`（feishu webhook 除外）
- **错误处理**: loguru 记录，try/except + rollback，不抛未处理异常
- **测试**: pytest + pytest-asyncio；fixtures 提供模拟数据；`Test` 类 + `test_` 方法
- **数据源**: tushare 主源（配额经 `TushareQuota.consume()`），baostock 补充器调用前先 `manager.supplement_healthy()` 检查；补数分批提交 + 可取消
- **DAG**: 流程拓扑全部由 `dag_flows` 表动态编排；DAG 节点函数不做同步 baostock 补充（防阻塞）
- **回测估值**: 停牌/缺行情日沿用最近有效价（`_mark_price` / `Account.mark_price`），**不得按 0 或成本价估值**（曾把 max_dd 从 10% 撑到 47%）；零价行不得进宽表或库（writers 丢弃非正收盘价）；无有效报价日不触发退出。模型评估链路（预处理/标签/回测）只允许走 `pipeline.py` 的四个模块级函数，训练与 `eval_version.py` 共用
- **启动初始化**: lifespan 中 init_db + entity_stats 基线 + TRAINING→DRAFT 恢复 + WS 广播 + cron
- **Docker**: Python 3.11-slim，阿里云镜像加速；docker-compose (app + PostgreSQL + Redis)
- **环境变量**: `.env` + pydantic-settings；`APP_ENV=dev|prod`；TUSHARE_TOKEN 必须配置
- **前端**: Axios 拦截器自动带 JWT Token；401 排除 /api/login 的 reload；日期一律北京时间；WS 监听器务必清理
- **新功能必须判定权限节点（强制）**: 任何新增/修改的页面、菜单、子 tab、按钮、API、Agent 工具、敏感动作，开发前先判定是否抽象为功能节点。属于用户可授权能力 → 同一提交内更新 `app/auth/definitions.py`（权限码/名称/父子/route/API 映射）、`app/auth/route_permissions.py`、角色默认矩阵、前端导航/`v-perm`、`design/10-多用户与RBAC权限设计.md` 与多角色测试矩阵；纯共享只读且无需单独授权的能力，也要在权限表中标注归属父节点或 `public`。**禁止只加后端路由/按钮而不登记权限节点**。
- **权限节点变更审查**: 新增/调整权限码时，必须同时说明：默认授予哪些角色、是否 personal scope / support_only、支持模式是否禁止、旧数据/旧角色如何迁移；至少补一条"有权通过 + 无权 403/隐藏"的双角色测试。内部定时任务、DAG 节点等不暴露 HTTP 的后台能力可不单独建节点，但若提供页面/API 操作入口，入口本身必须有节点。

## Deployment（已下线，历史存档）

> ⚠️ **服务器部署已于 2026-08 拆除**：docker + stone 全部数据已清理，s.pmlab.top 已下线，SSH 别名 `myhuawei` 与 nginx 配置均已移除。**当前仅本地运行**（后端 :8000 + 前端 :3000）。以下为历史流程，仅作参考。

- **SSH**: `ssh myhuawei`（别名，root@114.116.51.107，已配置 id_rsa）【已失效】
- **路径**: `/usr/local/htdoc/stone/`【已删除】
- **容器**: docker compose (app + db + redis)【已卸载】
- **生产域名**: `https://s.pmlab.top`【证书已删，已下线】
- **HTTPS**: certbot，`nginx/host-nginx.conf`
- **Nginx**: 宿主机反代 :8000，`Dockerfile` 内置 nginx

### 目录部署方式

| 目录 | 方式 |
|------|------|
| `app/` `crawler/` `scripts/` `strategy/` | 构建到镜像 → `docker compose build app` |
| `web-v2/dist/` | volume 挂载 → rsync 即可 |
| `nginx/` | volume 挂载 → 重启 nginx |

### 本地运行方式（现行）

```bash
# 后端
venv/bin/python -m uvicorn app.main:app --port 8000

# 前端（dev）
cd web-v2 && npm run dev
# 或直接（无需 npm 包装）：node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 3000
```

### 分支与环境策略（2026-11-23 起强制）

完整规范见 `design/12-开发分支与测试环境规范.md`。核心：

1. **主仓库永远停在 `main`**：`/home/bnbnyu/projects/stone` 只用于 3000/8000 验收，禁止在主仓库 checkout 功能分支。
2. **一任务一分支一工作树**：从最新 `main` 建 `feat/<slug>` / `fix/<slug>`，工作树放 `/home/bnbnyu/projects/stone-worktrees/<slug>`；任务合并后立即删除，不复用旧分支/旧 worktree。
3. **一任务一端口对（2026-11-28 用户拍板）**：主环境 `8000/3000` 是**本地正式端口**，始终跑 `main` 最新合并结果，任务合并后必须及时让 3000 生效（重启后端/前端）。其它分支开发一律用旁路端口对：前端从 `3001`、`3002` 顺延，后端从 `8101`、`8102` 顺延（前后端配对），禁止 kill 他人端口进程、禁止把开发实例留在 3000/8000。
4. **测试库**：非 schema 任务用共享 `stone_test`；涉及 `app/db/schema.py` / `init_db` 迁移的任务从 `stone_test` 克隆 `stone_t_<slug>`，任务结束 drop。自动化测试禁止连生产库 `stock_monitor`；生产库只用于人工验收，迁移前先备份。 **旁路后端注意**：lifespan 会启动 cron 调度器与盘中采集 loop（无开关），连生产库的旁路后端等于多一个调度器——短时人工验收要避开 cron 时隙启停（资讯流程每 20 分钟、`*/N` 类），长跑与自动化一律连 `stone_test`。
5. **收尾必须回收**：测试/构建通过 → 合并 `main` → `git worktree remove` → 删除分支 → drop 任务库 → 停止进程。分支生命周期 ≤ 3 天。
6. 开工先 `git fetch --prune && git switch main && git pull --ff-only && git status`，并查 `git worktree list`；新会话不复用旧分支。
7. **只 `git add` 自己改的文件**，禁止 `git add -A` / `git commit -a`；高冲突文件改动前先看 `git status`，发现他人改动不回滚、不提交，在回复中说明。

### 版本管理（现行）

```bash
git tag vX.Y     # 语义化：特性里程碑进次版本号，纯修复进补丁号；本地 tag + push --tags
```

### 部署流程（历史存档，服务器已拆除，勿再执行）

```bash
# 0. 本地测试
pytest tests/ -v
npm --prefix web-v2 run build

# 1. 打 tag
git add -A && git commit -m "vX.Y: ..."
git tag vX.Y && git push origin vX.Y

# 2. 同步前端（volume，rsync 即可）
rsync -avz web-v2/dist/ myhuawei:/usr/local/htdoc/stone/web-v2/dist/

# 3. 同步后端源码（构建到镜像）
rsync -avz app/ myhuawei:/usr/local/htdoc/stone/app/
rsync -avz scripts/ myhuawei:/usr/local/htdoc/stone/scripts/
rsync -avz strategy/ myhuawei:/usr/local/htdoc/stone/strategy/
rsync -avz crawler/ myhuawei:/usr/local/htdoc/stone/crawler/
rsync -avz Dockerfile myhuawei:/usr/local/htdoc/stone/Dockerfile

# 4. 备份数据库
ssh myhuawei "cd /usr/local/htdoc/stone && docker compose exec -T db pg_dump -U stock stock_monitor > backup-\$(date +%Y%m%d).sql"

# 5. 重建并重启
ssh myhuawei "cd /usr/local/htdoc/stone && docker compose build --no-cache app && docker compose up -d app"

# 6. 验证
ssh myhuawei "curl -s https://s.pmlab.top/health"
ssh myhuawei "docker logs stock-app --tail 20"
```

### 禁止行为
- ❌ SSH 直接改服务器代码（服务器已拆除）
- ❌ 未打 tag 提交
- ❌ 未备份数据库就迁移
- ❌ 覆盖生产 `.env`（生产已下线）

## Notes

- **v3.29.1（2026-11-28）**：实时页多用户适配审查（design/14 §10 归属矩阵）——逐模块盘点「全局/个人」归属后修掉一个真实缺口：**`GET /api/buy/check`、`GET /api/buy/candidates` 会给被查看用户落交易计划**（`persist=True` 一路走到 `get_or_create_plan`，支持模式借身份查看=查看即写入他人账户，违反 trade_intraday 写明的「GET 不得产生写入」承诺；`/api/stock/radar` 早已按 `persist=not principal.support_mode` 传参，buy 端点漏了）。修复：`persist` 贯穿 `trade_intraday.check → _build_state`、`buy_check._risk_gate → intraday_check`、`buy_check.check/candidates → intraday_state`，API 层统一 `persist=not principal.support_mode`（普通用户 GET 落计划是既有产品行为，不变）。另：`/api/stock/radar` 增加 `pool_empty`，前端持仓区空态区分「新用户无自选（给下一步）」与「快照失败（自动重试）」。归属结论（详表见 design/14 §10.1）：盘面/分析带/决策线闸门/今日异动=全局；雷达/持仓信号/候选/买点检查/今日盘中提醒/资金地图持仓描边=个人（`data_user_id`）；支持角色不授实时页入口。已知边界记录不修：关注池扫描上限 50、提醒块随 radar.view 二次控制、支持会话 WS 不实时收 it%。**教训**：①「GET 幂等」要多用户语义下再验一次——单用户时代「查看即生成计划」无害，多用户+支持会话后就是代看写他人账户；②同一承诺（persist=not support_mode）在一个模块里做对了不代表同类端点都对，按端点清单逐个核对。
- **v3.29.0（2026-11-28）**：实时页全链路审查修复（design/14 §9）——URL/路由、个人监测提醒、宏观口径三批逐项修复。①**路由/URL**：放大态与指数分时选中进 URL（`/rt?tab=idx&idx=…&zoom=1`，`nav.rtZoom/rtIdx`）；`nav.js` 补监听 `hashchange`（原先只听 popstate，同页地址栏改 hash 应用不响应）；mk% Chrome 通知点击带 `nav.rtFocus='market'` 直滚「今日盘面异动」（挂载时消费一次，只靠 watch 会丢）。②**个人监测**：持仓区新增「今日盘中提醒」块（复用 `/api/risk/alerts?intraday=true` 与 `risk.alerts.view`，权限零新码；Chrome 通知错过可回看，点行进详情，WS 推送即补进列表）；`/api/stock/radar` 增加 `data_date`（`radar_data_date`）——盘前/休市日腾讯快照回退上一交易日，页面原来拿缓存生成时间冒充数据时间；表头刷新间隔按 `pollDelay` 档位如实显示（不再写死 15s）；缺值不上色（`null >= 0` 会把「—」染成涨色）；雷达/候选/提醒拉取加 seq 守卫；盘面首拉失败按本地时段兜底重试。③**宏观口径**：机会区 allowed 而实时闸门 closed/caution/unknown 时加 `opportunityGateNote` 注明口径差异（候选=日频、决策线=盘中，v3.26 决策不改行为只补展示）；盘面宽度行涨停/跌停（新浪近似）与炸板（东财池口径）加 title 标来源。④**时区补课（v3.27.1 §10.9 规矩）**：`market_sentiment`/`buy_check`/`risk.py` 的「今天」从主机时区 `date.today()` 统一改 `_bj_today()`（UTC 主机在北京凌晨会把今天算成昨天：stale 恒真、盘中触发整段失效、告警记错日期），各模块补回归测试。⑤验证：node --test 20/20（顺带修正 main 上既有 1 条失败断言：v3.28 改 `asofText` 文案漏改断言）；pytest 822 通过/1 既有失败（`test_risk_heat_with_auth`）；build 通过；Playwright 3002 旁路前端零报错、放大/指数选中刷新恢复、hashchange 响应、旧链接重定向回归、390×844 与暗色截图核对。**教训**：①「URL 带上页面路径」之外还要带上**可还原的状态**——放大、选中指数这类易失状态不进 URL，刷新/分享就丢；②hash 路由只听 popstate 不够，地址栏/程序化改 hash 只有 hashchange；③瞬时通知（Chrome Notification）不能当提醒的唯一出口，页内要有可回看的落地列表；④vite 多实例共享 node_modules 软链时用 `VITE_CACHE_DIR` 隔离依赖预构建缓存（vite.config.js 新增）。
- **v3.28.0（2026-11-28）**：实时页（盘中作战台，design/14）——把依靠盘中实时数据的三块功能收进一级页 `#/rt`，按交易纪律顺序重新布局。①**布局**：决策线（时段/数据所属交易日 → 开仓闸门 → 开新仓 → 今日异动）→ 三区（盘面=先看环境 / 持仓=先处理风险 / 机会=后找机会）→ 分析带（资金地图=分时市值树图+行业资金流，可切指数分时/板块与资金/涨停梯队/量能与情绪，可放大）。布局依据全部取自项目内已固化的纪律（盘前清单/回撤熔断技能、盘中执行卡五步、`intraday_signal` 只对持仓给动作、买点四道闸门）。②**旧入口迁移**：`#/kr`、`#/stocks?tab=radar`、树图 `mode=intraday` 三种链接由 `nav.js` 的 `enterRealtime()` 重定向（Chrome 通知点击不失效）；个股页回归纯列表、选股页回归日线树图；`MarketRealtimeView.vue` 拆为 `components/realtime/*` + `utils/realtimeCharts.js`（v3.27.1 审查修复逐条移植）。③**权限**：新增容器节点 `research.realtime.view`（只管一级入口，五个系统角色默认授予，support 不授），三区仍由 `research.market.view` / `research.treemap.view` / `research.radar.view` 二次控制，**无新端点**。④**按纪律做的细节**：机会区被拦时给「下一步」（仓位/纪律类 → 去持仓处理；市场闸门类 → 只管持仓；按 `state.allow_new_buy` 判定不猜文案）；盘面/雷达/候选拉取失败在界面标出不静默；闸门结论不完整（情绪源不可用/仍是上一交易日口径）在决策线标出；收盘后放慢轮询；持仓雷达表列档位随栏宽自适应（放不下整列不显示，不横着截半列数字）。⑤验证：pytest 817 通过（`test_risk_heat_with_auth` 在 stone_test 上既有失败：admin 无持仓不返回 score）/ node 37 / build；Playwright Chromium 三视口两主题零报错；合并后重启 8000 并在 3000 复跑验收。**教训**：①**迁移旧代码必须逐函数对语义并看真实数据截图**——迁出时弄丢 `yiNum` 的 `/1e8`（资金流 y 轴画成「元」）、漏 `loadHist()` 首调、栅格列数写错、放大后画布不跟，全是「构建通过 + 测试通过」之后靠截图才发现；②**非交易日与后端冷缓存是必测场景**（周六暴露地图默认取今天；重启后头一两分钟缓存为空，决策线只剩光秃秃的日期）；③并行会话各动同一块代码时，合并前先 `git worktree list` + 读对方工作树的 diff——本次「我删的文件对方正在改」，靠提前移植对方修复避免了丢失；④本会话没有浏览器工具时，`web-v2/node_modules` 里的 Playwright Chromium 可直接用于截图验收（worktree 里复制一份 node_modules 以免与 3000 的 Vite 缓存互相干扰）。
- **v3.27.1（2026-11-28）**：v3.27 代码审查修复 15 项（逐项修复 + 修复前后对照验证，口径见 design/13 §10）。**必须修的三项（都在批次三的告警里）**：①`mk_height`（连板高度告警）读的是 zt 缓存的原始池、恒不触发——`max_lbc` 只在 `zt_summary()` 输出里，旧单测往缓存塞的是 summary 形状所以一直是绿的；②**数据所属交易日必须校验**：东财 push2ex 对尚无数据的日期（盘前/休市日/未来日）静默返回最近一个交易日的涨停池，返回体 `qdate` 恒为最新交易日、不能用来校验（实测请求 20261128/20261130 均返回 11-27 的 417 家）→ `_pool_trade_date` 只请求确定已有数据的日期（今天 09:31 后才请求今天）、评估只在 `session=open` 且逐规则校验数据是今天的、响应带 `data_date` 并重定义 `stale`、前端标「显示 X（最近交易日）数据」；③告警状态只在**落库后**前移（被 family 冷却/当日上限拦下的信号原先永久丢失，含 critical 的恐慌）+ 宽度首轮只建基线（独立标记 `breadth_seen`，不能用 zone 值代替，否则「开盘中性→盘中转强」会漏）。**应该修的七项**：`mk%` WS 推送按 `research.market.view` 过滤（真实双角色链路：admin/trader 收到、support 收不到）；闸门算不出来返回 `unknown/pass=None`（不兜成放行——升级路径是接到买入候选）、stale 情绪不参与定级；测试不再裸赋值替换 `market_sentiment.get`（同进程 A/B：原始测试跑完后它被永久换成返回 panic 的 lambda，泄漏给后续所有测试）；`refresh_all` 互斥 + 按需刷新让位给采集 loop（窗口内 loop 3 分钟没动静才补、窗口外 10 分钟一次）；`/api/market_sentiment` 拷贝后再附带 gate（`get()` 返回的是共享缓存对象）；前端迷你图紧凑刻度 / x 轴按真实时间对齐（固定 interval 会跳过午休漂成 13:29）/ 闸门关闸用 `--c-error` / 情绪轨迹补空点断线并跟随数据日期；日终归档重拉收盘终值、「今天」按北京时间算。**peg-chart 五项**：一致预期抽样口径如实披露（页面 + 回补脚本文档；**不改算法，见下**）、历史序列与截面同用「机构 ≥3」、`as_of` 的成分股快照也 PIT、两个端点登记权限节点（此前只靠前缀规则兜底）、拉取失败抛 `SourceFetchError` 不再被登记成空结果 + report_rc 当天发布时点前不请求（省 1/10 配额）+ 整条链路补测试（此前没有）。**审查外发现**：ECharts `visualMap.dimension: 0` 是 x 轴序号——大盘页「恐慌指数趋势」自 v3.22 起的分档着色实际是沿横轴切四段（x=100 的 17 分与 x=2999 的 88.5 分同色），实时页轨迹图照搬了同一配置，一并去掉 dimension（默认取最后一个维度即数值）。**PEG 口径现状（决策待用户拍板）**：一致预期是稀疏样本——report_rc 每天 10 次配额、历史只能每 20 交易日抽样一天，180 天窗口内实际只有 6~7 个发布日，`org_count` 系统性偏低（2026-09-11 复算 518 只里有预测、机构 ≥3 的仅 55 只；沪深300 截面 204 个有效 PEG 里完整口径 31 个，其余是「实际增长口径」占位）。是否因此改算法（如历史带只用 G_hist）未定。**教训**：①给缓存写测试，种子必须是 adapter 的真实输出形状——fixture 直接喂真实解析函数；②外部源「实测可用」还要实测**静默回退**：对无数据日期返回最近交易日的数据，比报错危险得多；③`pytest … | grep … && git commit` 会吞掉测试失败（grep 匹配到 "failed" 也返回 0），提交必须以 pytest 退出码为准；④旁路后端连生产库会多出一个 cron 调度器（lifespan 无开关）——本次已核对 dag_run_log 无重复触发，启停要避开 cron 时隙，已写入分支策略 §4；⑤同进程验证测试泄漏：跑完测试后比对被替换过的函数是否还是原对象；⑥`cd <dir> && nohup cmd &` 整段是一个后台子任务，cd 不会留给后面的命令——旁路前端曾因此跑成了主仓库的旧代码（截图里没有新行为），验收前先确认服务进程的 cwd（`readlink /proc/<pid>/cwd`）或直接抓 dev server 编译出的组件源码比对。
- **v3.27.0（2026-11-28）**：多会话分支收口 + 端口约定——①合并 `feat/peg-chart`（大盘页 PEG 面板与一致预期链路，7 commits：PEG≤1 完整口径默认选中、行业筛选计数、rolling.rank 向量化、回补中/无覆盖提示区分、report_rc 限流不阻断、一致预期历史抽样水位线自动化 10 次/天；合并回 main 后 test_peg/test_peg_api 等 73 测试 + build 全绿）；②删除 30+ 已合并本地分支与 5 个已合并远端分支、清理 stone-multiuser/main-accept 旧 worktree，本地与远端收敛到只剩 `main`；③**端口约定（用户拍板）**：3000/8000 为本地正式端口始终跑 main 最新（合并后及时重启生效），其它分支开发一律前端 3001/3002… + 后端 8101/8102… 旁路顺延，已写入「分支与环境策略」§3；④主仓库切回 main（旧基线 AGENTS.md 未提交噪音确认与 main 重复后丢弃），8000/3000 重启并全功能验收（PEG 面板 / #/kr 实时盘 / gate 横幅 / 异动卡）。
- **v3.26.0（2026-11-27）**：实时大盘批次三——盘面异动告警 + 空仓闸门联动（design/13 §9）。①**盘面异动告警**：`evaluate_market_alerts`（挂 market_realtime._cycle 交易窗口内，指标全读内存缓存零新增请求）七类 kind（指数 15min 急涨急跌 side 状态切换+滞回、宽度上穿 0.65/下穿 0.35 one-shot、连板高度 ≥5 当日一次、情绪进 panic(critical)/strong）→ `risk_alerts`（user_id NULL + `mk%` 前缀）→ WS **广播全员**（`_alert_visible` 谓词给 mk% 开分支——原 NULL 行只发 model.alerts.view）→ Chrome 通知点击跳 #/kr + #/kr「今日盘面异动」卡；冷却 DB-backed（重启不重发）+ family 10min + 当日 cap 15，配置 `strategy_config.market_alerts`（缺行默认全开）。②**实时空仓闸门（仅展示）**：调研确认三道闸门互不相通（regime 只在信号 DAG、buy_check 日频 EOD、实时情绪仅展示、盘中买入判定不看盘面）——`compute_regime_gates` 下沉 `app/regime_gate.py`（pipeline 别名保 test_regime_gate）、`buy_check.live_gate(db)` 组合三源取最严（closed>caution>open，60s 缓存）→ `/api/market/realtime.gate` + `/api/market_sentiment.gate` → #/kr 闸门横幅 + 情绪条 chip（大盘/持仓页）。**买入候选行为不变**（actionable 仍日频口径；升级一行接线已记录）。③权限零新码（无新端点，definitions 注记）。测试：17 单测（FakeDB 状态机/滞回/冷却/cap/WS 谓词/live_gate 组合）+ 集成 shape；WS 广播链路实测 PASS；gate 三源与真实行情核对（market_gate caution + regime enabled 未禁买 + strong → 组合 caution）。教训：①FakeDB 模拟 `strategy_config` 行要把 enabled 造对（返回 (None,None) 被解释为"行存在但禁用"，与"缺行默认启用"语义相反，曾让 8 个用例假失败）；②共享 stone_test 需跟进上任务的建表（zt_pool_daily 幂等补建一次）。
- **v3.25.0（2026-11-27）**：实时大盘分析页（design/13）——大盘页子页面 `#/kr`（无新导航项，入口在 `#/k` 工具栏「⚡ 实时盘」），聚合盘面实时视角：①指数分时组（腾讯 minute/query 当日逐分钟，4 mini+1 大图）②盘面宽度卡（新浪全市场快照聚合：涨跌/涨跌停/炸板/中位数/两市成交额+全天预估）③当日情绪轨迹（stock_treemap_intraday slot 重放，`_verdict` 三成分口径与日频恐慌指数序列可比）④量能对比（新浪 5 分钟 K 沪综指+深证综指，今日/昨日/近4日同期累计+预估）⑤涨停梯队（东财 push2ex 涨停池/炸板池：天梯/炸板率/连板高度，**日终 15:05 幂等落库 zt_pool_daily**+收盘重启补档+`scripts/backfill_zt_pool.py` 历史回补——东财历史深度约 1 个月，空池日跳过不写）⑥板块雷达（东财行业板块 496 个含细分，涨幅/主力净流入双维度）⑦大盘主力资金分时（push2 fflow/kline secid2 聚合沪深两市）。后端 `app/market_realtime.py`：后台线程 60s 刷新模块级缓存（`strategy_config.market_realtime` 可调），端点只读缓存+slot 重放、缺源时 90s 节流按需整刷（treemap `_ensure_async` 模式）；新表 `zt_pool_daily`（JSONB 全量明细）；API `/api/market/realtime`+`/realtime/zt-history`，权限归属既有 `research.market.view` 前缀兜底无新码。**教训：①push2his 域有指纹墙**——trends2 首测全通后当晚开始对非命令行-curl 的 TLS 指纹差别对待（requests/curl_cffi 全超时、压缩流中断，Accept-Encoding: identity/强制 IPv4 均无效），已弃用改走腾讯+新浪，「实测可用」有时效性，单源失败不得拖垮整页（refresh_all 每 key 独立 try/except）；②东财 clist pz 锁 100 行/页，全市场 56 页走 em 闸门不可行，宽度类聚合用新浪直连；③fflow 分钟资金必须走 push2 域（push2his 返回空 klines）；④外部接口解析层做形态兼容（东财 SSE `data:` 前缀流取首个非空 data 行）。
- **v3.23.1（2026-11-25）**：全量复权特征迁移收口——`scripts/rebuild_features_adjusted_full.py` 按年+多进程+断点续跑，把 137 个价格/量相关 enabled 特征 2017→2026 全部重算为后复权口径，共 **1,428,798,384 行 / 0 错误 / 约 2h22m**；DAG `feature_compute`/`feature_backfill` 显式 `adjust_prices=True`。配套向量化 `ts_corr`（分组 rolling.corr）、`ts_rank`（rolling.rank(method='max')）、`imax/imin`（滑动窗口 argmax/argmin）、`slope/rsquare/resi`（窗口矩阵回归），与旧逐窗口实现逐值一致。全量重算后 v21.0 对 2026-11-23 的 5 个 active 特征预测与原值 **差异 0**（无需重训）。备份：`_bak_fv_p0_20261124`（active 5 原始 raw 值）+ `/home/bnbnyu/backups/stone-p0-20261124/`；全量特征值本身可由 `daily_quote` + `features.formula` 重建。注意：`feature_values` 重写产生大量 dead tuples，autovacuum 正在回收，短期内按特征 `COUNT(*)` 较慢属正常；`_rebuild_adjusted_progress.json` 为断点文件（已 ignore）。
- **v3.23.0（2026-11-24）**：P0/P1 安全与口径整批修复 + 复权特征迁移——①RBAC/JWT/Agent 沙箱与工具权限、飞书回调签名/加密/去重/回复 API、Agent SQL `stone_ro` 最小权限角色；②数据链路修复 fresh 库 `feature_values` 分区、`dag_task_fund` ROE 填充、交易日空数据误报成功；③模型修复三重切分标签 purge、交易日持有/禁止同日回补、双周期模型、规则回退 NameError、IC DEGRADED 严重度；④交易修复交割单多账户重放、减仓 T+1、盘中 `max_per_tick` 冷却顺序、个人实盘风控评估、现金未知提示；⑤前端修复支持模式 401 登出、启动鉴权失败误登出、模型配置保存丢字段、详情深链权限、WS 重连、ECharts 清理。**复权特征迁移（P0-4）**：`FEATURE_PRICE_ADJUST=true`；已用 `scripts/rebuild_features_adjusted.py` 重算 active 模型（v21.0）使用的 5 个量价特征（a158_CORR_5/10/20、a158_VOLUME_20/60，2017 起 5170 万行），备份表 `_bak_fv_p0_20261124` + `/home/bnbnyu/backups/stone-p0-20261124/`；v21.0 以 purge 切分重训并激活为主模型（RankIC 5/10/20d 0.062/0.074/0.091，Sharpe 1.04，旧 v20 的 2.22 含自然日持有/同日回补虚高，不可直接比）。**注意**：其余约 132 个已注册特征仍是 raw 价口径，若新模型选用须先按同一脚本重算；`ts_corr` 已改分组 `rolling.corr`（千万行级从小时降到秒级），全量重算可行。
- **v3.22.0（2026-11-22）**：大盘详情页与市场恐慌指数趋势（design/11）——大盘详情页（一级 tab「大盘」`#/k?idx=`）——①市场级只读 API `app/api/market.py` 六端点（overview/valuation/moneyflow/breadth/margin/macro），全部聚合既有 DAG 落库表，模块级 TTL 缓存（overview 60s/估值 600s/其余 300s）②`index_valuation` 历史回补 `scripts/backfill_index_valuation.py`（tushare index_dailybasic 按 ts_code×8 年分块，7 指数 3.3 万行铺满 2005→今，21 次调用；macro_daily 节点只滚 40 日窗口不够画分位）③前端 `MarketView.vue`：指数切换（7 大指数，选中态存 nav.midx）+ K线/成交额/MACD/RSI 联动组（`echarts.connect` + `chart.group='mk'`，缺 group 声明 connect 空转）+ 区间档位 1月→20年（3/5/8/10/15/20 年，「全部」10000 装下 1990 起全量）+ PE·PB 分位/换手率/市场宽度/全市场资金流（2010 起）/两融（2014 起）/行业资金流 + 侧栏估值对比表·宏观卡。④历史全量回补：`scripts/backfill_index_kline.py`（上证 1990-12-19 起 8768 行、深成 1991 起；幂等，tushare vol×100/amount×1000 换算）、`backfill_index_valuation.py --start 20040101`（估值最早 2004-01-02）、`backfill_macro.py`（CPI/PPI/M0M1M2/GDP 铺满 1990 起 1434 行 + `--shibor` 按日长跑回补 2006-10 起隔夜 SHIBOR，约 5200 次调用 2h）。教训：**extra JSONB 列不能带 NaN**——json.dumps 会输出裸 NaN 字面量被 PostgreSQL 拒收（早期宏观数据常见），value 列有 fv 守卫而 extra 漏了；多进程并行跑回补脚本会竞态 TushareQuota 计数（1.3s 限流是进程内的），回补一律串行。教训：**同一数据在库里的代码格式不统一**——index_daily_quote.index_code 无交易所后缀、index_valuation.ts_code 带后缀，跨表 JOIN 必须先统一；tushare moneyflow 单位是万元（×1e4 对齐 sector_flow 口径），而 stock_margin_detail 直接是元。
  ⑤恐慌指数趋势：新表 `market_sentiment_daily`（PK trade_date，score 三成分口径 + score_rt 实时四成分参考列）；DAG 节点 `sentiment_daily`（独立流程「市场情绪日度」15:30 收盘后，照 macro_daily 外壳 + 雪球播种风格，当日无 treemap 快照跳过）；历史回补 `scripts/backfill_sentiment_daily.py`（从 daily_quote 重算 2000 起 6513 行，LAG(close_hfq) + percentile_cont 中位数 + 涨跌停代码段阈值，判级复用 `market_sentiment._verdict`——公式单点维护）；API `/api/market/sentiment-history` + 大盘页「恐慌指数趋势」卡（visualMap 按值分档着色）。**口径决策**：序列统一三成分口径（VWAP 项恒按 _verdict 缺省 10 分），历史与增量可比；score_rt 仅当日有值。验证：2007-5-30/2020-2-3 暴跌 12.3 panic、2024-9-30 行情 89.9 strong，与真实历史吻合。
  ⑥收口修复：行业资金流卡取数结构（sector_flow_history 为预计算平铺 cumulative/sum20，按 data dict 取数抛 TypeError 曾致图表空白）、两融披露残缺日邻日防御（当日行数 < 邻日 70% 剔除；11-06/11-13 已重采，11-20 上游残缺靠端点过滤）。教训：**tag 序列以 git tag 现实为准**——Notes 手写版本号曾与并行会话的 tag 序列（v3.12 已被新闻资讯占用）脱节，收口前先 `git tag -l` 查重。
- **v3.11.0（2026-11-21）**：可转债模块（design/09）——①独立数据域三表（`cb_basic`/`cb_daily`/`cb_share`，不进 `daily_quote`/`stock_master`，避免污染 ETF 分类与模型扫描域）②Tushare `cb_basic/cb_daily/cb_share`（2000 积分可用；`cb_call` 无权限，强赎观察用停止转股/退市日期 + 130% 强赎线近似）③同步链路 `crawler/cb.py`（basic/daily/share/sync_range 断点续跑 + 配额熔断）+ CLI `scripts/cb_sync.py` + DAG 节点 `cb_daily`（独立流程「可转债数据」18:40）④规则分析 `app/cb_analytics.py`（转股价值/溢价/双低/纯债溢价/换手/强赎观察/评分/策略池）⑤API `/api/cb/*` + 正股关联 `/api/stock/{code}/convertible-bonds` ⑥前端一级导航「可转债」`#/bonds`（列表/策略/散点）+ 独立详情 `#/bond/:code` + 个股详情「可转债」卡片。教训：**tushare 的 `stk_code` 是带交易所后缀的完整代码**，入库前必须截 6 位，否则 `VARCHAR(6)` 批量 UPSERT 整批炸掉；通用补数 `_EXT_COLLECTORS` 只登记存在性 SQL，不走 0 行登记机制。
- **v3.10.0（2026-09-29）**：新闻资讯模块第 1+2 层（design/07）——①四类六源采集：快讯（财联社主 / 东财7×24 / 华尔街见闻**主备切换**，三源全收会把近重复内容与 LLM 成本翻三倍）、关注池个股新闻、研报（qType 0 个股 / 1 行业）、巨潮公告；全部公开 JSON 接口零 key，东财侧统一走 `em_get()` 节流（实测风控线 5/s、200/min）。②五表两视图（`news_item` 不分区；`news_stock` src/match；`news_summary` 含第 3 层预留的 `sentiment`；`news_crawl_state`/`news_poll_state`；`v_focus_pool`/`v_news_feed`）。③LLM 逐条总结（`PROMPT_VER='n1'` 一次出摘要/重要度/主题/情绪），抽公共件 `scripts/llm_batch.py` 与雪球打分共用（RPM 闸门 + 线程池 + 折半重试）。④DAG 流程「新闻资讯」`*/20 * * * *`（cron→news_crawl→news_summary）+ 资讯页 tab / 详情页「相关资讯」块 / 状态页卡片。冒烟：六源全部取到数据、连续两轮幂等（第二轮「跳过已存在 109」）、冷启动积压 199→18（首轮积压清空；剩余 18 条的根因见下）。教训：**幂等主键必须是 `(source, source_id)`，`content_hash` 只能做跨源去重**（正文回填会改 content，用哈希判幂等会静默丢同源多份文档）；巨潮 orgId 兜底猜测（`gssz0302874`）返回 0 条与「没公告」无法区分，已改为缺失即报错；未总结必须是 NULL 而非 0，且按重要度过滤要保留 NULL 行，否则「刚采集完还没总结」的窗口里列表空空而库里有数据。**「整批静默消失」**：模型在同股同题多份研报上会合并条目并丢掉整个 `id` 字段，而 `retry_split` 用 `res is not None` 判成功 → 空列表被当成功收下（不重试、零日志、整批静默计失败）。修法=空结果按失败折半重试 + 空数组即失败 + 回收不全留痕 + prompt 加 id 硬约束（`n1`→`n2`）。泛化教训：**「空结果」与「无结果」是两种失败**，前者的返回值是个合法容器，最容易被当成成功
- **v3.9.2（2026-09-15）**：过拟合治理两件套——①评估报告 Val-Test 差距三分解（gap_decomposition：执行口径 T+0/T+1、环境 基准年化三窗、模型 RankIC 三窗 + per_label 归因结论），eval_version 默认双口径四遍回测；ModelEval 拟合度判读改按 T+1 诚实口径并新增「差距分解」卡。定论（v15.0 实测）：gap 2.50 里 10d 周期在 T+1 下消失（执行口径伪影）、RankIC val≈test 无退化（环境切换主导）、train 段回测反而最差（平市）——真模型侧份额仅 ~0.3-0.5 选择偏差。②训练侧 purged CV（selection_cv，治选择偏差）+ 多种子集成（ensemble.seeds，治方差）。冒烟 v18.0 验证全链路（CV 选择→集成 pkl→分解报告→血缘）。教训：X_train 是 DataFrame，非连续行集必须 .iloc；回归集成不得定义 predict_proba；实时库上「逐位复现存档」只在数据静态时成立（每日采集/特征补窗会让窗口内自然长行，昨 3,453,426 → 今 3,458,568）
- **v3.9.1（2026-09-14）**：闸门重扫与运维三件套——①`scan_gate.py` 闸门前沿重扫（干净基线验证 F1 档，锚点复现存档数字后方可采信网格）②`rolling_retrain` DAG 节点：模型新鲜度守护（ACTIVE 超阈值写 risk_alerts kind='model_stale' 走既有 WS/Chrome 通道；超重训阈值且无在途候选/不在冷却期时克隆 ACTIVE 自动起训，产物只落 DRAFT 人工激活，参数在 strategy_config.model_freshness）③数据血缘台账 data_lineage + /api/lineage（模型↔数据漂移一查便知）④评估口径补齐：run_training_backtest 增 exec_lag（T+1 次日收盘成交对照）与 total_cost/cost_pct（只读观测），bt_summary/eval 报告增 sample_domain（三切分+成交时点+成本参数）。教训：psycopg2 读会话长事务持锁，独立脚本读完务必 commit（曾把后端 init_db 的 ALTER 卡进锁队列）
- **v3.8.0（2026-09-02）**：特征引擎并行化与五步拓展——①alpha158_full 并行引擎（每年宽表单次拉取共享 + fork 多进程各自 engine，全量 1140 因子年 30h→1.5h；进度文件迁仓库根 .a158_full_progress.json）②KEPL `neut(字段)` 截面中性化算子（市值+行业哑变量逐日 OLS 残差）+ 训练/评估/预测 `feature_neut` 钩子（先中性化后 cs_rank）+ ModelView 开关 ③资金流接入（moneyflow DAG 节点 + KEPL 5 字段 + 2010 起历史回补 4047 天全量；tushare 真实列名 *_amount，hk_hold 因北向披露停止撤销）④风控引擎 app/risk.py（止损/回撤熔断/行业上限，risk_alerts 表，WS 推送 type='risk_alert' → Chrome 通知，顶栏铃铛开关；paper 持仓口径必须 close_hfq）⑤feature_values 按年分区（零拷贝 legacy-attach 2017-2027 + 前瞻分区 + DEFAULT；init_db 升级 $-quote 感知切分 + 动态分区确保块；删老年份=DROP PARTITION）⑥数据治理：特征库十年保留（13.1 亿→3.25 亿行重建，value float8，砍 idx_fv_stock_date）、WSL 迁 D:\WSL（vhdx compact 444→335G）、ANALYZE 节点上流程（数据页统计过期防治）、start_local.sh pg_isready 等待 + a158/资金流回补自愈续跑、turnover_rev 按修复引擎重算（373.8 万行）
- **v3.7.0（2026-09-01）**：Qlib 移植七里程碑（design/05）——①回测引擎 v2（`strategy/backtest/`：Exchange/Account/engine，涨跌停/T+1/延迟结算/最低佣金/冲击成本/整手/现金约束，与 v1 逐日 0 差异）②TopkDropoutStrategy（合并排序防高卖低买/hold_thresh/n_drop，扫描支持 topk）③纸面组合迁移 v2 内核（删 paper_day_step，173 日回放逐日一致）④绩效报告（report.py：risk_analysis sum/product + IR/α/β/超额/换手，model_health 新列 + 评估页指标条）⑤因子库（KEPL 补 14 算子 + 负字面量，Alpha158 生成器 114 因子入库 2519 万行，IC 绿75/黄20/红19，dedup 精选 41；`scripts/alpha158.py`）⑥DoubleEnsemble 实验（`strategy/models/double_ensemble.py` LightGBM，v12.0 PENDING，WF 3/4 胜但 RankIC 未达标 FAIL）⑦评估入口默认 engine=v2（`_backtest` 兼容层 v1 口径：归因/扫描/WF/置换检验）。附带：算子目录单一事实源 `/api/kepl/functions`（强校验）+ Monaco 补全 + 函数页「系统内置算子」分类 + AI 上下文动态化；`_fetch_ohlcv` 过滤零价停牌行（修因子面板污染）；`predict_for_version` 多周期均值 + NaN 缺失分支（信号统一入口）
- **v3.2.1（2026-08-22）**：补数链路修复（index_code VARCHAR(16)/SAVEPOINT 批次隔离/上市日感知续传阈值）+ 特征计算 2000 全量（23 特征 3.98 亿行完成，ATR 自动补 high/low）+ XGBoost 训练 GPU 优先（RTX 5060 cuda，失败回退 CPU）+ 前端 popstate 监听泄漏修复（详见 design/01-04）
- **数据现状**：daily_quote 1766 万行（2000→今，重复 0）；index_daily_quote 63 万行（2000 起续跑中）；cb_daily 74.5 万行 / 1912 个交易日（Tushare cb_daily 最早 2019-01-02，已全量回补）；ETF/基本面（ROE 等）补数挂起，用页面补数按钮续跑（tushare 8000 次/天配额预算），勿写临时脚本
- **夜间补数 cron**：每天 00:05 北京时间触发（需机器开机）；可转债由独立流程「可转债数据」每日 18:40 增量采集
