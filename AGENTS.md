# 个股买卖点监测系统 (K道 / Stock Monitor)

Python + FastAPI 后端 + Vue 3 前端 + PostgreSQL 的全栈 A 股量化监测系统，支持 K 线自动采集、多数据源适配、DAG 流水线调度、特征/模型引擎、AI 飞书 Bot 以及实时 WS 推送。

## Project

- **Stack**: Python 3.11, FastAPI, SQLAlchemy (async+sync), PostgreSQL 15, Redis 7, Vue 3, Vite, Naive UI, ECharts, AntV X6, Monaco Editor
- **Entry point**: `app/main.py` — FastAPI application, lifespan 中初始化 DB + DAG + entity_stats
- **Config**: `app/config.py` — pydantic-settings, 读取 `.env` 文件
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
| DAG 流水线 | `python3 scripts/pipeline.py <trade_date> [start_node]` |
| 每日采集 | `bash scripts/daily_crawl.sh` |

## Architecture

### 核心模块

- **`app/`** — FastAPI 后端
  - `app/main.py` — 入口，注册 15 个 router + CORS + 前端静态
  - `app/config.py` — 配置管理（Settings）
  - `app/db/` — 数据库连接（connection.py）+ 30+ 张表 DDL（schema.py）
  - `app/api/` — REST API 路由：
    - `portfolio.py` — 持仓 CRUD + 盈亏/历史
    - `treemap.py` — 市值树图（4 种指标 drill-down）
    - `signals.py` — 买点扫描
    - `stock.py` / `stocks.py` — 个股详情/K线/PE/列表
    - `status.py` — 数据状态/DAG 触发/WS/系统指标
    - `settings.py` — 策略配置/偏好/API Key
    - `models.py` — XGBoost 模型训练/评估/实盘
    - `functions.py` — 自定义函数 CRUD + 安全校验
    - `features.py` — 特征 CRUD + KEPL 公式 + 批量计算
    - `kepl.py` — KEPL 公式解析
    - `dag_types.py` / `dag_flows.py` — DAG 节点类型 / 流程 CRUD + 可视化执行
  - `app/auth/` — JWT 认证 + 登录（单用户，环境变量密码）
  - `app/feishu/` — 飞书 Bot webhook 集成（签名校验、对话历史、确认卡片）
  - `app/ai/` — DeepSeek AI 客户端（8 个 Function Calling 工具 + 降级菜单）
  - `app/kepl/` — KEPL 表达式解析器（Lark LALR(1) + AST + 20+ 内置函数）
  - `app/signal.py` — DAG ↔ WebSocket 解耦桥梁（asyncio Event + 线程安全唤醒）

- **`crawler/`** — 数据采集层
  - `baostock_crawler.py` — baostock 原生爬虫
  - `adapters/` — 数据源适配器模式（抽象基类 DataSourceAdapter + AKShare/Baostock 实现 + DataSourceManager 自动 fallback）
  - `backfill.py` — 历史补数管理器（≈50KB，含进度/断点续传）
  - `catch_up.py` — 缺失交易日补采
  - `trade_calendar.py` — 交易日历同步
  - `writers.py` — 统一 UPSERT 写入
  - `progress.py` — 下载进度管理（JSON 文件 + 断点续传）

- **`scripts/`** — 定时任务与流水线
  - `pipeline.py` — DAG 流水线主文件（~100KB），含所有任务函数 + NODE_FN_MAP
  - `dag.py` — 轻量 DAG 调度器（DagExecutor + DagNode，计划-执行模型）
  - `feature_compute.py` — KEPL→pandas 向量化特征计算引擎
  - `daily_crawl.sh` — 每日 cron 采集（交易日检查→补采→数据→DAG）
  - `health_check.sh` — 每小时健康检查
  - `import_data.sh` — 数据导入

- **`strategy/`** — 策略工具
  - `indicators.py` — 纯 numpy/pandas 技术指标（无 TA-Lib）

- **`web-v2/`** — 前端 V2
  - Vue 3 + Vite + Naive UI + ECharts + Pinia + AntV X6 (DAG 流程图) + Monaco Editor (代码编辑)
  - 21 个页面组件（含 Login, Portfolio, Treemap, Signals, Stocks, Detail, Status, Settings, SignalStats, ModelView/ModelTraining/ModelEval/ModelLive, FunctionView, FeatureView/FeatureDetail, DagFlowEdit/DagFlowView/DagView, BackfillModal, MonacoEditor）
  - 6 个 Pinia Store: `auth`, `nav`, `dag`, `model`, `market`, `theme`
  - Tab 路由: p=持仓, m=树图, s=信号, l=个股列表, x=状态, a=模型, f=函数, e=特征, g=DAG, o=设置, d=详情

- **`nginx/`** — Nginx 配置
  - `default.conf` — Docker 内部 nginx（SSL + 反代）
  - `host-nginx.conf` — 宿主机 nginx

- **`tests/`** — 测试
  - `conftest.py` — 共享 fixtures（sample_daily_data, sample_downtrend_data, divergence_data, short_data）
  - `unit/` — 单元测试（test_dag.py, test_signal.py）
  - `integration/` — 集成测试（test_app.py）
  - `e2e/` — Playwright 前端冒烟

### 数据流

```
baostock/akshare → crawler/adapters → PostgreSQL → DAG pipeline (kline→fund→treemap→stats)
                                                     ↕
                                              feature_compute (KEPL 引擎)
                                                     ↕
                                              models (XGBoost 训练/信号)
                                                     ↓
                    Vue 前端 ← FastAPI REST API + WebSocket DAG 广播
                       ↓
                   飞书 Bot (DeepSeek AI 对话)
```

### 关键数据表 (SQL schema in `app/db/schema.py`, ~30 张表)
- **行情**: `trade_calendar`, `stock_master`, `daily_quote`, `index_daily_quote`, `corporate_actions`
- **基本面**: `stock_fundamentals`, `stock_fundamentals_history`
- **持仓/信号**: `portfolio`, `portfolio_history`, `signal_history`, `stock_treemap_cache`
- **指标/特征**: `features`, `feature_values`, `feature_stats`
- **函数**: `functions`
- **模型**: `model_versions`, `model_signals`
- **DAG**: `dag_config`, `dag_run_log`, `dag_flows`, `dag_flow_versions`
- **系统**: `strategy_config`, `data_stats_cache`, `entity_stats`, `daily_completeness`, `system_metrics`, `backfill_logs`
- **其他**: `stock_industry`, `failed_downloads`

## Conventions

- **语言**: 注释/日志使用中文；代码标识符使用英文
- **数据库**: 原生 SQL（SQLAlchemy Core + text()），非 ORM；snake_case 表名，按功能前缀
- **API**: FastAPI Router 模式，统一前缀 `/api`（feishu webhook 除外）
- **错误处理**: loguru 记录，try/except + rollback，不抛未处理异常
- **测试**: pytest + pytest-asyncio；fixtures 提供模拟数据；`Test` 类 + `test_` 方法
- **DAG**: `DagNode(name, deps, fn)` 注册，配置存 `dag_config` 表；`dag.start()` 统一入口
- **启动初始化**: lifespan 中同步 DB schema + DAG 加载 + entity_stats 基线 + 恢复异常模型
- **Docker**: Python 3.11-slim，阿里云镜像加速；docker-compose (app + PostgreSQL + Redis)
- **环境变量**: `.env` + pydantic-settings；`APP_ENV=dev|prod`
- **前端**: Axios 拦截器自动带 JWT Token；401 自动清 token 跳转登录

## Notes

<!-- 临时记录区 -->
