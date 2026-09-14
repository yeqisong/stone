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
| 前端开发 | `cd web-v2 && npm run dev` (port 3000, proxy → 8000) |
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
  - `app/db/` — 数据库连接（connection.py 双引擎）+ 47 张表 DDL（feature_values 按年分区，库内实际 70 张）+ init_db 幂等迁移（schema.py）
  - `app/api/` — REST API 路由：
    - `portfolio.py` / `treemap.py` / `signals.py` / `stock.py` / `stocks.py` — 持仓/树图/信号/详情/列表
    - `risk.py` — 风控规则与告警查询（/api/risk/*）
    - `status.py` — 数据状态/DAG 触发/WS/系统指标/**tushare_quota**
    - `settings.py` / `models.py` / `functions.py` / `features.py` / `kepl.py` — 配置/模型/函数/特征/KEPL
    - `dag_types.py` / `dag_flows.py` — DAG 节点类型 / 动态流程编排 + 执行
  - `app/risk.py` — 风控规则引擎（止损/回撤熔断/行业上限；规则存 strategy_config，paper_portfolio 节点每日评估，告警走 WS 推送）
  - `app/auth/` — JWT 认证 + 登录（单用户，环境变量密码）
  - `app/task/` — TaskManager 统一任务机制（并发≤5、同流程互斥、WS 推送）
  - `app/feishu/` — 飞书 Bot webhook（签名校验、对话历史、确认卡片闭环）
  - `app/ai/` — DeepSeek AI 客户端（8 个 Function Calling 工具 + 降级菜单）
  - `app/kepl/` — KEPL 表达式解析器（Lark LALR(1) + AST + 时间序列/截面函数）
  - `app/signal.py` — DAG ↔ WebSocket 解耦桥梁（asyncio Event + 线程安全唤醒）
  - `app/lineage.py` — 数据血缘台账（旁路观测：factor_heal/零价修复/特征重算/复评/训练/滚动重训事件落 `data_lineage` 表，MB 级元数据；`log_event` 失败绝不影响主链路）；`model_data_drift` 回答「模型训练后数据又被改过吗」；API 在 `app/api/lineage.py`（GET /api/lineage、/api/lineage/model/{ver}）

- **`crawler/`** — 数据采集层
  - `adapters/` — `tushare_adapter.py`（主源，按交易日全市场）+ `baostock_adapter.py`（补充器）+ `tushare_quota.py`（配额计数）+ `manager.py`（主源直取 + 补充器健康缓存）+ `base.py`（标准化 dataclass）
  - `backfill.py` — 历史补数管理器（按交易日逐日拉取、断点续传按日期完整度 80% 阈值、配额预算、补充器分批提交+可取消）
  - `trade_calendar.py` — 交易日历同步（baostock 唯一源）
  - `writers.py` — 批量 UPSERT（kline/fundamentals）+ 市值补全 + 基本面 ROE 补充

- **`scripts/`** — 调度与计算层
  - `pipeline.py` — 全部 dag_task_* 节点函数 + NODE_FN_MAP（14 节点）+ 模型训练 + generate_stats；**模型评估链路已抽为模块级函数**（`prepare_model_frame` 预处理+标签 / `build_targets` 标签 / `run_training_backtest` 回测内核 / `pred_score` 预测入口），训练节点与复评脚本共用同一实现
  - `dag.py` — 轻量 DAG 调度器（DagExecutor + DagNode，计划-执行模型，并行拓扑执行）
  - `feature_compute.py` — KEPL→pandas 特征计算引擎（lookback 扩展、COPY 流式写入、截面函数）
  - `eval_version.py` — 对**已训练**模型重跑评估（不重训），覆盖 backtest_records / backtest_daily_records / model_versions 指标；改引擎口径或修数据后想拿干净数字就用它，不必等 40 分钟重训；`--exec-lag-check` 附带 T+1 次日收盘成交对照（只打印），报告含 sample_domain（三切分/成交时点/成本参数）与逐周期成本拖累
  - `repair_zero_prices.py` — 存量零价停牌行修复（沿用上一收盘；默认试运行，`--apply` 执行）
  - `repair_adj_factor.py` — 复权因子一致性自愈（tushare 回溯性重定基 → 半修正窗口造成跨界假跳变；`--check` 体检、`--apply` 重写 close_hfq、`--apply --features` 连特征重算）。日常由 DAG 节点 `factor_heal` 自动跑（kline 之后、feature_compute 之前）
  - `scan_gate.py` — 闸门前沿重扫（熔断档位 × regime 空仓闸门 × trailing × 持有期），完全复用 eval_version 载入路径与 pipeline 四件套；跑前先复现现行档锚点数字，锚不中说明 harness 有 bug 网格作废；结果 CSV 落 data/，不改生产配置
  - `cron_scheduler.py` — cron 定时触发（last_run_at 幂等，60s 扫描）
  - `daily_crawl.sh` — 触发 dag_flows 已发布流程（不再直接调用采集）

- **`strategy/`** — 策略工具
  - `indicators.py` — 纯 numpy/pandas 技术指标（RSI 除零处理 / BOLL ddof=0 对齐通达信）

- **`web-v2/`** — 前端 V2
  - Vue 3 + Vite + Naive UI + ECharts + Pinia + Vue Flow (DAG 流程图) + Monaco Editor (代码编辑)
  - 页面组件：Login, Portfolio, Treemap, Signals, Stocks, Detail, Status, ModelView, FunctionView, FeatureView/FeatureDetail, DagFlowEdit, FlowRunView, FlowLogView, StockFundView, DagNode
  - 5 个 Pinia Store: `auth`, `nav`, `model`, `market`, `theme`
  - Tab 路由: p=持仓, m=树图, s=信号, l=个股列表, x=状态, a=模型, f=函数, e=特征, g=DAG, u=证券主档, d=详情, v=特征详情, q=日志, r=流程查看
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
                      └─ model_signal / model_health（ACTIVE 模型）
                                      ↓
              Vue 前端 ← REST API + WS 推送（配额/任务/补数进度实时）
                 ↓
             飞书 Bot (DeepSeek AI 对话)
```

### 关键数据表（SQL schema in `app/db/schema.py`, 47 张 DDL / 库内含分区 70 张）
- **行情**: `trade_calendar`, `stock_master`, `daily_quote`, `index_daily_quote`
- **基本面**: `stock_fundamentals`(PK: code+trade_date), `stock_fundamentals_history`
- **业务**: `portfolio`, `portfolio_history`, `signal_history`, `stock_treemap_cache`
- **特征/函数**: `features`, `feature_values`, `functions`, `function_versions`
- **模型**: `model_versions`, `training_trials`, `model_health`, `version_comparisons`, `backtest_records`, `backtest_trades`, `backtest_daily_records`（逐日净值 + 持仓快照，伪回撤复盘用）
- **DAG**: `dag_config`, `dag_run_log`, `dag_flows`, `dag_flow_versions`, `backfill_tasks`
- **统计/系统**: `daily_completeness`, `data_stats_cache`, `entity_stats`, `system_metrics`, `strategy_config`, **`tushare_quota`**, `data_lineage`（血缘台账）
- **风控**: `risk_alerts`（规则引擎告警：止损/回撤熔断/行业上限，WS→Chrome 通知）
- **拓展**: `stock_moneyflow`（已接入：每日 DAG 节点 + KEPL 字段 + 2010 起历史回补完成）、`stock_top_list` / `stock_margin_detail` / `stock_holder_number`（表就绪，采集链路未接入）、`stock_hk_hold`（北向个股披露 2024-08 停止，数据源失效，保留表结构）

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

### 分支策略（2026-09-14 起强制）

**禁止直接在 `main` 上改动。** 每项工作开独立分支（`feat/xxx`、`fix/xxx`），完成后合回 `main`；`main` 只接受合并与用户明确要求的收口提交。

```bash
git checkout -b fix/factor-heal      # 开工先建分支
git add <只列自己改的文件> && git commit -m "..."
git push -u origin fix/factor-heal   # 远程：git@github.com:yeqisong/stone.git
```

**多会话并行是本仓库的常态**（曾出现两个会话同时改 `scripts/pipeline.py`）。因此：

1. 动手前必查 `git status` + `git log --oneline -3`：确认工作区有没有他人在途改动、有没有不是自己的新提交。
2. **只 `git add` 自己改的文件**，禁止 `git add -A` / `git commit -a` 扫入他人未提交的工作。
3. 高冲突文件（`scripts/pipeline.py`、`scripts/feature_compute.py`、`web-v2/src/components/*.vue`、`AGENTS.md`）改动前先看 `git status`；Edit 报"文件已被修改"时必须重新 Read 再改，不可用旧快照回写。
4. 发现工作区有他人改动时：不要提交、不要回滚，在回复里说明并继续自己的部分。

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

- **v3.9.1（2026-09-14）**：闸门重扫与运维三件套——①`scan_gate.py` 闸门前沿重扫（干净基线验证 F1 档，锚点复现存档数字后方可采信网格）②`rolling_retrain` DAG 节点：模型新鲜度守护（ACTIVE 超阈值写 risk_alerts kind='model_stale' 走既有 WS/Chrome 通道；超重训阈值且无在途候选/不在冷却期时克隆 ACTIVE 自动起训，产物只落 DRAFT 人工激活，参数在 strategy_config.model_freshness）③数据血缘台账 data_lineage + /api/lineage（模型↔数据漂移一查便知）④评估口径补齐：run_training_backtest 增 exec_lag（T+1 次日收盘成交对照）与 total_cost/cost_pct（只读观测），bt_summary/eval 报告增 sample_domain（三切分+成交时点+成本参数）。教训：psycopg2 读会话长事务持锁，独立脚本读完务必 commit（曾把后端 init_db 的 ALTER 卡进锁队列）
- **v3.8.0（2026-09-02）**：特征引擎并行化与五步拓展——①alpha158_full 并行引擎（每年宽表单次拉取共享 + fork 多进程各自 engine，全量 1140 因子年 30h→1.5h；进度文件迁仓库根 .a158_full_progress.json）②KEPL `neut(字段)` 截面中性化算子（市值+行业哑变量逐日 OLS 残差）+ 训练/评估/预测 `feature_neut` 钩子（先中性化后 cs_rank）+ ModelView 开关 ③资金流接入（moneyflow DAG 节点 + KEPL 5 字段 + 2010 起历史回补 4047 天全量；tushare 真实列名 *_amount，hk_hold 因北向披露停止撤销）④风控引擎 app/risk.py（止损/回撤熔断/行业上限，risk_alerts 表，WS 推送 type='risk_alert' → Chrome 通知，顶栏铃铛开关；paper 持仓口径必须 close_hfq）⑤feature_values 按年分区（零拷贝 legacy-attach 2017-2027 + 前瞻分区 + DEFAULT；init_db 升级 $-quote 感知切分 + 动态分区确保块；删老年份=DROP PARTITION）⑥数据治理：特征库十年保留（13.1 亿→3.25 亿行重建，value float8，砍 idx_fv_stock_date）、WSL 迁 D:\WSL（vhdx compact 444→335G）、ANALYZE 节点上流程（数据页统计过期防治）、start_local.sh pg_isready 等待 + a158/资金流回补自愈续跑、turnover_rev 按修复引擎重算（373.8 万行）
- **v3.7.0（2026-09-01）**：Qlib 移植七里程碑（design/05）——①回测引擎 v2（`strategy/backtest/`：Exchange/Account/engine，涨跌停/T+1/延迟结算/最低佣金/冲击成本/整手/现金约束，与 v1 逐日 0 差异）②TopkDropoutStrategy（合并排序防高卖低买/hold_thresh/n_drop，扫描支持 topk）③纸面组合迁移 v2 内核（删 paper_day_step，173 日回放逐日一致）④绩效报告（report.py：risk_analysis sum/product + IR/α/β/超额/换手，model_health 新列 + 评估页指标条）⑤因子库（KEPL 补 14 算子 + 负字面量，Alpha158 生成器 114 因子入库 2519 万行，IC 绿75/黄20/红19，dedup 精选 41；`scripts/alpha158.py`）⑥DoubleEnsemble 实验（`strategy/models/double_ensemble.py` LightGBM，v12.0 PENDING，WF 3/4 胜但 RankIC 未达标 FAIL）⑦评估入口默认 engine=v2（`_backtest` 兼容层 v1 口径：归因/扫描/WF/置换检验）。附带：算子目录单一事实源 `/api/kepl/functions`（强校验）+ Monaco 补全 + 函数页「系统内置算子」分类 + AI 上下文动态化；`_fetch_ohlcv` 过滤零价停牌行（修因子面板污染）；`predict_for_version` 多周期均值 + NaN 缺失分支（信号统一入口）
- **v3.2.1（2026-08-22）**：补数链路修复（index_code VARCHAR(16)/SAVEPOINT 批次隔离/上市日感知续传阈值）+ 特征计算 2000 全量（23 特征 3.98 亿行完成，ATR 自动补 high/low）+ XGBoost 训练 GPU 优先（RTX 5060 cuda，失败回退 CPU）+ 前端 popstate 监听泄漏修复（详见 design/01-04）
- **数据现状**：daily_quote 1766 万行（2000→今，重复 0）；index_daily_quote 63 万行（2000 起续跑中）；ETF/基本面（ROE 等）补数挂起，用页面补数按钮续跑（tushare 8000 次/天配额预算），勿写临时脚本
- **夜间补数 cron**：每天 00:05 北京时间触发（需机器开机）
