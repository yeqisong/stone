您说得对，我需要彻底检查文档完整性，不能有任何“保持不变”、“同原文档”、“略”等偷懒表述。以下是逐项核验后重新提供的完整优化文档：

---

# 策略模型工程化平台 — 开发设计文档（优化版）

> **版本**：v1.1  
> **基于**：策略模型工程化平台设计文档 v1.3 及原开发设计文档 v1.0  
> **状态**：已评审

⚠️ **风险警示**：本系统所有投资分析内容仅供理论学习和理论练习使用，不构成投资建议。量化交易存在本金损失风险，历史回测表现不代表未来收益。

---

## 1. 文档信息与变更记录

| 项目 | 内容 |
|------|------|
| 文档名称 | 策略模型工程化平台开发设计文档 |
| 版本 | v1.1 |
| 基于设计文档 | 策略模型工程化平台设计文档 v1.3 |
| 目标读者 | 后端开发、前端开发、DBA、运维、安全审计 |
| 配套文档 | 回测模型指导详细手册_v1.1、训练与落地实现手册_v1.0、系统运维手册_v1.0 |

### 变更记录

| 日期 | 版本 | 变更内容 | 作者 |
|------|------|----------|------|
| 2026-06-14 | v1.0 | 基于设计文档v1.3，重构为开发设计文档 | — |
| 2026-06-15 | v1.1 | 架构评审优化：修正指标重建矛盾、补充安全设计、完善状态机、增加配置差异追踪、优化数据模型 | 架构组 |

### 关键约束（开发必读）

| 编号 | 约束 | 说明 |
|------|------|------|
| C‑01 | 时间顺序切分 | 训练/验证/测试数据必须按时间顺序划分，严禁随机打乱 |
| C‑02 | 人工审批 | 模型从PENDING到ACTIVE必须经人工审批，不可自动上线 |
| C‑03 | 单一活跃版本 | 同一策略模型族（同一model_name）同一时刻只允许一个ACTIVE版本；未来可扩展为多策略并行，届时通过配置开关控制 |
| C‑04 | 配置创建时冻结 | 四层配置在创建模型版本时确定，后续只读；修改任何配置须创建新版本；继承创建时记录配置差异到config_diff字段 |
| C‑05 | 指标参数固定 | 指标计算使用行业标准参数：BOLL(20,2)、MACD(12,26,9)、RSI(14)、ATR(14)。全量重建仅用于数据修复，不因参数变更触发 |
| C‑06 | 风险警示 | 所有面向用户的投资分析输出必须附带“仅供理论学习和理论练习使用，不构成投资建议” |
| C‑07 | 指标与版本解耦 | 指标计算在共享指标池完成，与模型版本生命周期无关 |

---

## 2. 系统概述

### 2.1 系统定位

策略模型工程化平台负责量化策略模型的版本管理、训练流水线、实盘信号生成、健康度监控与告警。核心能力：

- 模型版本全生命周期管理（创建→训练→验证→审批→上线→归档）
- 共享指标池（全量预计算 + 每日增量ETL）
- 自动化训练流水线（Optuna参数搜索 + Walk‑Forward验证 + ML增强层）
- 实盘信号生成与推送
- 模型健康度5维度监控与重训触发

### 2.2 技术栈

> 🔗 = 复用 K道现有基础设施。

| 层级 | 技术选型 | 说明 |
|------|----------|------|
| 后端 | Python 3.10+ / FastAPI 🔗 | 复用 `app/main.py`，新增 `app/api/models.py` 路由 |
| 数据库 | PostgreSQL 15+ 🔗 | 复用 `app/db/`，model 表加入 `schema.py` |
| 缓存 | Redis 7+ 🔗 | 复用现有 Redis 实例 |
| 定时任务 | DAG 流水线 🔗 | 新增 indicator_incr / model_signal / model_health / model_train 节点，复用心跳/WS/终止 |
| 训练框架 | Optuna 3.x + XGBoost/LightGBM | 新增 pip 依赖 |
| 数据源 | baostock 🔗 | 复用 `crawler/baostock_crawler.py` |
| 前端 | Naive UI 🔗 | 复用 `web-v2/`，暗色/浅色主题 |
| 通知 | 飞书Bot Webhook 🔗 | 复用 `app/feishu/`，审批走卡片-回调模式 |
| 文件存储 | 本地文件系统 🔗 | 训练产物存 `data/models/{version}/` |
| 认证 | JWT 🔗 | 复用 `app/auth/auth.py` |
| 指标计算 | `stock_indicators` 表 + `strategy/indicators.py` 🔗 | 预计算存入表，训练/信号直接读 |
| 调度推送 | DAG-WS 🔗 | 训练进度通过现有 WS 推送 |

> 详细复用分析见 [§11 架构复用审查](#11-架构复用审查2026-06-13)。

### 2.3 系统边界

**平台职责范围内**：模型版本管理、指标计算与存储、训练流水线、信号生成、健康度监控、告警推送。

**平台职责范围外**：实盘交易执行、资金管理、券商对接（由外部交易系统负责）。

---

## 3. 数据库设计

### 3.1 实体关系描述

系统包含以下核心实体及其关系：

| 实体 | 主键 | 核心关系 |
|------|------|----------|
| daily_quote 🔗 | (stock_code, trade_date) | 复用 K道现有表，被 stock_indicators 计算引用 |
| stock_indicators_* (6 张表) | (stock_code, trade_date) | 每类指标一张表（boll/macd/rsi/atr/ma/volume），共享主键；全量/增量均可按表并行计算；新增指标 = 新建表，不触碰已有表 |
| indicator_calc_log | id (SERIAL) | 记录 stock_indicators 的每次批量更新任务 |
| model_versions | version (VARCHAR, UK) | 核心实体；生成信号（存 signal_history 🔗）、training_trials、model_health、version_comparisons |
| training_trials | (version, trial_number) (UK) | 外键关联 model_versions，记录 Optuna 迭代 |
| version_comparisons | id (SERIAL) | 关联两个 model_versions（version_a, version_b） |
| signal_history 🔗 | (复用) | 复用 K道现有表，新增 ml_confidence / forward_*_return / actual_return 列 |
| model_health | id (SERIAL) | 属于当前 ACTIVE 版本，每日评估一条记录 |

**简化关系图**：

```
                  daily_quote 🔗
                       |
                       v
   indicator_calc_log ─ stock_indicators
                       |
                       v
              model_versions (ACTIVE) ───── signal_history 🔗
                  |         |
                  |         └── training_trials
                  |
                  ├── model_health
                  └── version_comparisons (跨版本)
```

### 3.2 完整DDL

#### 3.2.1 daily_quote — 日K线行情表（复用）

> 🔗 复用 K道现有 `daily_quote` 表，不新建。表结构见 `app/db/schema.py::CREATE_DAILY_QUOTE`。

本模块使用以下字段：

| 字段 | 用途 |
|------|------|
| `stock_code` / `trade_date` | 主键 |
| `open` / `high` / `low` | OHLC |
| `close_hfq` | 后复权收盘价（替代原设计的 `close`） |
| `volume` / `amount` / `turnover` | 量能数据 |
| `is_st` | 复用 `stock_master.status` 判断 |
| `list_date` | 复用 `stock_master.ipo_date` |

**需新增字段**：

```sql
ALTER TABLE daily_quote ADD COLUMN IF NOT EXISTS pct_change DECIMAL(8,4);
COMMENT ON COLUMN daily_quote.pct_change IS '涨跌幅(%)，当日 close 相对前日 close';
```

数据写入由 DAG `kline` 节点（`crawler/baostock_crawler.py`）负责，本模块不重复实现。

#### 3.2.2 stock_indicators — 共享指标池表

用途：存储所有股票的技术指标计算结果，与模型版本解耦，全局共享。

写入时机：首次部署 DAG `indicator_full` 节点全量预计算；每日 `indicator_incr` 节点增量追加（UPSERT）。

查询场景：模型训练读取特征、实盘信号生成、指标数据查看。

关键设计：每类指标独立一张表，共享主键 `(stock_code, trade_date)`。指标参数固定为行业标准，同一条K线算出的指标值是确定性的，因此可与版本解耦。指标计算函数复用 `strategy/indicators.py`（纯 numpy 实现）。

**拓展性设计**：新增指标 = 新建一张表 + 全量初始化该表即可，不触碰已有表。全量和增量均可按表并行计算。

```sql
-- 布林线 (period=20, std_mult=2)
CREATE TABLE stock_indicators_boll (
    stock_code    VARCHAR(10) NOT NULL,
    trade_date    DATE NOT NULL,
    upper         DECIMAL(12,4),
    mid           DECIMAL(12,4),
    lower         DECIMAL(12,4),
    pct_b         DECIMAL(8,4),
    width         DECIMAL(8,4),
    created_at    TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (stock_code, trade_date)
);

-- MACD (fast=12, slow=26, signal=9)
CREATE TABLE stock_indicators_macd (
    stock_code    VARCHAR(10) NOT NULL,
    trade_date    DATE NOT NULL,
    dif           DECIMAL(12,4),
    dea           DECIMAL(12,4),
    hist          DECIMAL(12,4),
    created_at    TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (stock_code, trade_date)
);

-- RSI (period=14)
CREATE TABLE stock_indicators_rsi (
    stock_code    VARCHAR(10) NOT NULL,
    trade_date    DATE NOT NULL,
    rsi           DECIMAL(8,4),
    created_at    TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (stock_code, trade_date)
);

-- ATR (period=14)
CREATE TABLE stock_indicators_atr (
    stock_code    VARCHAR(10) NOT NULL,
    trade_date    DATE NOT NULL,
    atr           DECIMAL(12,4),
    created_at    TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (stock_code, trade_date)
);

-- 均线 (5/20/60/250)
CREATE TABLE stock_indicators_ma (
    stock_code    VARCHAR(10) NOT NULL,
    trade_date    DATE NOT NULL,
    ma5           DECIMAL(12,4),
    ma20          DECIMAL(12,4),
    ma60          DECIMAL(12,4),
    ma250         DECIMAL(12,4),
    created_at    TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (stock_code, trade_date)
);

-- 成交量
CREATE TABLE stock_indicators_volume (
    stock_code    VARCHAR(10) NOT NULL,
    trade_date    DATE NOT NULL,
    vol_ma5       DECIMAL(18,4),
    vol_ratio     DECIMAL(8,4),
    obv           DECIMAL(18,4),
    obv_ma5       DECIMAL(18,4),
    obv_ma10      DECIMAL(18,4),
    created_at    TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (stock_code, trade_date)
);

-- 通用索引模板（每张表）
CREATE INDEX idx_boll_date ON stock_indicators_boll (trade_date);
CREATE INDEX idx_macd_date ON stock_indicators_macd (trade_date);
CREATE INDEX idx_rsi_date ON stock_indicators_rsi (trade_date);
CREATE INDEX idx_atr_date ON stock_indicators_atr (trade_date);
CREATE INDEX idx_ma_date ON stock_indicators_ma (trade_date);
CREATE INDEX idx_volume_date ON stock_indicators_volume (trade_date);
```

**查询示例**（JOIN 获取多指标）：
```sql
SELECT b.stock_code, b.trade_date,
       b.upper, b.mid, b.lower, b.pct_b,
       m.dif, m.dea, m.hist,
       r.rsi, a.atr,
       ma.ma5, ma.ma20, ma.ma60,
       v.vol_ma5, v.vol_ratio
FROM stock_indicators_boll b
JOIN stock_indicators_macd m USING (stock_code, trade_date)
JOIN stock_indicators_rsi r USING (stock_code, trade_date)
JOIN stock_indicators_atr a USING (stock_code, trade_date)
JOIN stock_indicators_ma ma USING (stock_code, trade_date)
JOIN stock_indicators_volume v USING (stock_code, trade_date)
WHERE b.trade_date = '2026-06-12';
```

**新增指标示例**（零侵入）：
```sql
-- 新增 KDJ 指标，完全不影响已有表
CREATE TABLE stock_indicators_kdj (
    stock_code VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    k DECIMAL(8,4), d DECIMAL(8,4), j DECIMAL(8,4),
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (stock_code, trade_date)
);
-- 仅全量初始化这一张表
INSERT INTO stock_indicators_kdj (stock_code, trade_date, k, d, j)
SELECT ... FROM daily_quote ...;
```

#### 3.2.3 indicator_calc_log — 指标计算日志表

用途：记录每次指标计算/更新的执行日志，用于运维排查和数据回溯。

写入时机：每次指标计算任务完成后写入（全量初始化、增量更新、补数据）。

查询场景：指标池状态页、ETL运维排查。

```sql
CREATE TABLE indicator_calc_log (
    id              SERIAL PRIMARY KEY,
    calc_type       VARCHAR(20) NOT NULL,
    trade_date      DATE,
    stocks_updated  INTEGER,
    total_records   INTEGER,
    calc_params     JSONB,
    duration_seconds INTEGER,
    status          VARCHAR(10) DEFAULT 'success',
    error_message   TEXT,
    created_at      TIMESTAMP DEFAULT NOW()
);

COMMENT ON TABLE indicator_calc_log IS '指标计算日志 — 记录每次指标计算任务的执行情况';
COMMENT ON COLUMN indicator_calc_log.calc_type IS 'FULL_INIT=首次全量计算, INCREMENTAL=每日增量, BACKFILL=补数据';
COMMENT ON COLUMN indicator_calc_log.calc_params IS '使用的计算参数快照（固定值，用于审计追溯）';
```

#### 3.2.4 model_versions — 模型版本表（核心表）

用途：存储模型版本的完整配置、状态、训练结果和审批信息。是平台的核心表。

写入时机：创建版本时写入四层配置；训练/验证过程中更新状态和结果；审批时更新审批信息。

查询场景：版本列表、版本详情、训练进度、评估报告、信号生成读取配置、健康度检查。

关键设计：四层配置体系（身份/训练方法/数据/信号风控）在DDL中以独立列存储，创建时冻结，后续只读。继承创建时记录config_diff。

```sql
CREATE TABLE model_versions (
    id              SERIAL PRIMARY KEY,
    version         VARCHAR(20) NOT NULL UNIQUE,
    model_name      VARCHAR(100) NOT NULL,
    description     TEXT,
    created_by      VARCHAR(100),

    -- 状态机
    status          VARCHAR(20) NOT NULL DEFAULT 'DRAFT',

    -- ========== 第二层：训练方法配置 ==========
    optimizer       VARCHAR(20) NOT NULL DEFAULT 'bayesian',
    objective       VARCHAR(20) NOT NULL DEFAULT 'sharpe',
    n_trials        INTEGER NOT NULL DEFAULT 300,
    ml_enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    ml_algorithm    VARCHAR(20) NOT NULL DEFAULT 'xgboost',
    wf_folds        INTEGER NOT NULL DEFAULT 5,
    robustness_tolerance DECIMAL(5,3) DEFAULT 0.1,

    -- ========== 第三层：数据配置 ==========
    rolling_window_years INTEGER DEFAULT 5,
    window_start_date    DATE,
    window_end_date      DATE,
    stock_list      JSONB,
    feature_set     VARCHAR(50) DEFAULT 'all',
    target_variable VARCHAR(50) DEFAULT 'next_day_return',
    split_ratio     VARCHAR(20) DEFAULT '60/20/20',
    data_split_meta JSONB,

    -- ========== 第四层：信号与风控配置 ==========
    buy_score_threshold   INTEGER DEFAULT 4,
    sell_rule             VARCHAR(30) DEFAULT 'reverse_signal',
    stop_loss_pct         DECIMAL(5,3) DEFAULT 0.08,
    take_profit_pct       DECIMAL(5,3),
    signal_timeout_days   INTEGER DEFAULT 20,
    ml_confidence_threshold DECIMAL(5,3) DEFAULT 0.5,

    -- 参数搜索空间与固定参数（创建时填写）
    param_search_space JSONB NOT NULL,
    fixed_params       JSONB NOT NULL,
    risk_params        JSONB NOT NULL,

    -- 训练结果（训练/验证完成后填入）
    best_params        JSONB,
    final_params       JSONB,
    param_importance   JSONB,
    weak_params        JSONB,
    ml_model_path      VARCHAR(500),
    ml_threshold       DECIMAL(5,3),

    -- 绩效摘要
    train_sharpe       DECIMAL(8,4),
    val_sharpe         DECIMAL(8,4),
    test_sharpe        DECIMAL(8,4),
    wf_avg_sharpe      DECIMAL(8,4),
    val_train_ratio    DECIMAL(5,3),

    -- 评估报告
    evaluation_report  JSONB,
    recommendation     VARCHAR(50),

    -- 配置继承与差异追踪
    inherit_from       VARCHAR(20),
    config_diff        JSONB,

    -- 时间戳
    created_at          TIMESTAMP DEFAULT NOW(),
    training_started_at TIMESTAMP,
    training_ended_at   TIMESTAMP,
    validated_at        TIMESTAMP,
    activated_at        TIMESTAMP,
    archived_at         TIMESTAMP,

    -- 审批
    approved_by         VARCHAR(100),
    approval_note       TEXT,

    -- 数据路径
    data_dir            VARCHAR(500) NOT NULL
);

COMMENT ON TABLE model_versions IS '模型版本表 — 核心表，存储四层配置、状态机、训练结果、审批信息';
COMMENT ON COLUMN model_versions.status IS '状态机：DRAFT→PREPARING→TRAINING→VALIDATING→PENDING→ACTIVE/REJECTED→ARCHIVED；失败时可回退至DRAFT';
COMMENT ON COLUMN model_versions.optimizer IS '优化方法，决定参数搜索策略，同数据不同优化方法结果可能完全不同';
COMMENT ON COLUMN model_versions.rolling_window_years IS '滚动窗口年数，决定训练数据范围，推荐5年';
COMMENT ON COLUMN model_versions.robustness_tolerance IS '参数扰动范围，百分比格式，0.1表示±10%';
COMMENT ON COLUMN model_versions.stop_loss_pct IS '止损线，百分比格式，0.08表示8%';
COMMENT ON COLUMN model_versions.val_train_ratio IS '验证/训练夏普比率，<0.5严重过拟合，0.5~0.7可能过拟合，>0.7合理';
COMMENT ON COLUMN model_versions.recommendation IS '自动建议基于过拟合检测+鲁棒性+WF+测试集综合判定';
COMMENT ON COLUMN model_versions.config_diff IS '当通过继承创建时，记录与源版本的配置差异（overrides详情）';

ALTER TABLE model_versions ADD CONSTRAINT valid_status
    CHECK (status IN ('DRAFT', 'PREPARING', 'TRAINING', 'VALIDATING',
                      'PENDING', 'ACTIVE', 'REJECTED', 'ARCHIVED'));

CREATE UNIQUE INDEX idx_active_version ON model_versions (model_name, status)
    WHERE status = 'ACTIVE';

CREATE INDEX idx_model_status ON model_versions (status);
CREATE INDEX idx_model_created ON model_versions (created_at DESC);
```

#### 3.2.5 training_trials — 训练迭代日志表

用途：记录Optuna每次试验的参数和结果，用于训练可视化、参数重要性分析。

写入时机：每次Optuna trial完成时写入。

查询场景：训练监控页（实时进度）、参数重要性图、等高线图、平行坐标图。

```sql
CREATE TABLE training_trials (
    id              SERIAL PRIMARY KEY,
    version         VARCHAR(20) NOT NULL REFERENCES model_versions(version),
    trial_number    INTEGER NOT NULL,
    params          JSONB NOT NULL,
    score           DECIMAL(10,4),
    sharpe          DECIMAL(8,4),
    max_drawdown    DECIMAL(8,4),
    win_rate        DECIMAL(5,3),
    trade_count     INTEGER,
    duration_ms     INTEGER,
    created_at      TIMESTAMP DEFAULT NOW(),

    UNIQUE(version, trial_number)
);

COMMENT ON TABLE training_trials IS '训练迭代日志 — 记录Optuna每次试验的参数和评估结果';
COMMENT ON COLUMN training_trials.score IS '目标函数值，由optimizer配置决定（默认优化夏普比率）';
```

#### 3.2.6 version_comparisons — 版本比较表

用途：存储两个模型版本之间的对比结果（参数对比、绩效对比、鲁棒性对比）。

写入时机：用户触发版本对比时生成并写入。

查询场景：版本对比页。

```sql
CREATE TABLE version_comparisons (
    id              SERIAL PRIMARY KEY,
    version_a       VARCHAR(20) NOT NULL,
    version_b       VARCHAR(20) NOT NULL,
    comparison_type VARCHAR(20) NOT NULL,
    comparison_data JSONB NOT NULL,
    created_at      TIMESTAMP DEFAULT NOW()
);

COMMENT ON TABLE version_comparisons IS '版本比较结果 — 存储两个版本间的参数/绩效/鲁棒性对比数据';
COMMENT ON COLUMN version_comparisons.comparison_type IS 'params=参数对比, performance=绩效对比, robustness=鲁棒性对比';
```

#### 3.2.7 signal_history — 信号记录表（复用扩展）

> 🔗 复用 K道现有 `signal_history` 表，不新建 `signal_outputs`。表结构见 `app/db/schema.py::CREATE_SIGNAL_HISTORY`。

**需新增列**（模型训练模块专用）：

```sql
ALTER TABLE signal_history ADD COLUMN IF NOT EXISTS ml_confidence DECIMAL(5,3);
ALTER TABLE signal_history ADD COLUMN IF NOT EXISTS forward_5d_return DECIMAL(8,4);
ALTER TABLE signal_history ADD COLUMN IF NOT EXISTS forward_10d_return DECIMAL(8,4);
ALTER TABLE signal_history ADD COLUMN IF NOT EXISTS forward_20d_return DECIMAL(8,4);
ALTER TABLE signal_history ADD COLUMN IF NOT EXISTS actual_return DECIMAL(8,4);
ALTER TABLE signal_history ADD COLUMN IF NOT EXISTS close_reason VARCHAR(30);
```

| 字段 | 用途 |
|------|------|
| `ml_confidence` | ML 模型置信度（0-1），用于信号过滤 |
| `forward_*_return` | 信号发出后 N 日收益率，用于评估信号质量 |
| `actual_return` | 了结后实际收益率 |
| `close_reason` | 了结原因（`reverse_signal` / `stop_loss` / `timeout`） |

#### 3.2.8 model_health — 模型健康度监控表

用途：存储模型健康度的5维度评估结果和告警信息。

写入时机：每日ETL流程中健康度评估完成后写入。

查询场景：健康度页、健康度趋势图、重训建议检查。

```sql
CREATE TABLE model_health (
    id                      SERIAL PRIMARY KEY,
    version                 VARCHAR(20) NOT NULL,
    check_date              DATE NOT NULL,

    -- 维度1：胜率退化
    backtest_win_rate       DECIMAL(5,3),
    live_win_rate           DECIMAL(5,3),
    live_win_rate_ratio     DECIMAL(5,3),

    -- 维度2：信号频率
    recent_30d_signal_count INTEGER,
    recent_20d_signal_count INTEGER,

    -- 维度3：ML置信度
    ml_avg_confidence       DECIMAL(5,3),

    -- 维度4：实盘回撤
    current_drawdown        DECIMAL(8,4),

    -- 维度5：模型年龄
    days_since_training     INTEGER,

    -- 综合评估
    health_status           VARCHAR(20) NOT NULL,
    should_retrain          BOOLEAN DEFAULT FALSE,
    checks_detail           JSONB,
    alerts                  JSONB,

    created_at              TIMESTAMP DEFAULT NOW()
);

COMMENT ON TABLE model_health IS '模型健康度监控 — 5维度评估结果';
COMMENT ON COLUMN model_health.health_status IS 'HEALTHY=所有正常, WATCH=1个黄灯, CAUTION=2+黄灯, WARNING=1个红灯, CRITICAL=2+红灯';
COMMENT ON COLUMN model_health.live_win_rate_ratio IS '实盘胜率/回测胜率，<0.5严重退化, 0.5~0.7有所退化, >0.7正常';
COMMENT ON COLUMN model_health.current_drawdown IS '回撤幅度（正值），0.15表示15%回撤，用于阈值比较';
COMMENT ON COLUMN model_health.checks_detail IS 'JSON数组，每个元素含dimension/level/message/trigger_retrain';

CREATE INDEX idx_health_version_date ON model_health (version, check_date DESC);
```

### 3.3 新建表 vs 复用表汇总

| 表名 | 状态 | 说明 |
|------|:--:|------|
| `daily_quote` | 🔗 复用 | K道现有，由 `crawler/baostock_crawler.py` 写入 |
| `stock_master` | 🔗 复用 | K道现有，含 IPO 日期/ST 状态 |
| `trade_calendar` | 🔗 复用 | K道现有，训练窗口对齐交易日 |
| `signal_history` | 🔗 复用扩展 | K道现有，新增 6 列（§3.2.7） |
| `stock_indicators_*` (6 张) | 🆕 新建 | 按指标类型分表（boll/macd/rsi/atr/ma/volume），可并行计算、独立新增 |
| `indicator_calc_log` | 🆕 新建 | 本模块新建，指标计算审计 |
| `model_versions` | 🆕 新建 | 本模块新建，核心版本管理 |
| `training_trials` | 🆕 新建 | 本模块新建，Optuna 迭代日志 |
| `version_comparisons` | 🆕 新建 | 本模块新建，跨版本对比 |
| `model_health` | 🆕 新建 | 本模块新建，健康度 5 维度 |

---

## 4. 核心流程时序图

### 4.1 模型创建与训练全流程

**流程步骤**：

1. 用户调用 `POST /api/v1/models` 提交四层配置+参数搜索空间。
2. 系统验证配置，INSERT `model_versions`（status=DRAFT），返回版本号v1.3。
3. 用户调用 `POST /api/v1/models/v1.3/train` 启动训练。
4. 系统更新状态为PREPARING，从 `stock_indicators` 读取指标数据。
5. 系统保存划分后数据到 `data/v1.3/split/`，更新状态为TRAINING，记录 `training_started_at`。
6. 进入Optuna迭代循环（共n_trials次）：
   - 每次trial生成参数组合，执行回测计算目标函数值
   - INSERT `training_trials`（trial_number, params, score）
   - 通过 DAG-WS（现有 `app/api/status.py::broadcast_dag_status`）推送训练进度
7. 所有trial完成后，更新 `best_params`、`final_params`、`param_importance`、`weak_params`。
8. 保存训练产物到 `data/v1.3/training/`。
9. 更新状态为VALIDATING，记录 `training_ended_at`。
10. 从 `stock_indicators` 读取验证/测试数据，执行验证集评估、鲁棒性检验、Walk-Forward。
11. 如果 `ml_enabled=true`，执行ML增强层训练。
12. 在测试集上执行一次评估。
13. 更新 `evaluation_report`、`recommendation` 及各sharpe字段。
14. 保存评估报告到 `data/v1.3/evaluation/`。
15. 更新状态为PENDING，记录 `validated_at`。
16. 飞书推送“模型v1.3训练完成，等待审批”。
17. 用户调用 `POST /api/v1/models/v1.3/approve`。
18. 系统校验状态为PENDING，在事务内：旧ACTIVE版本→ARCHIVED，v1.3→ACTIVE，记录 `activated_at`。
19. 飞书推送“模型v1.3已上线”。
20. 返回200 OK。

### 4.2 每日增量流程（DAG 节点）

> 🔗 复用 K道 DAG 流水线，不引入 APScheduler/Celery。以下节点挂载到现有 DAG 拓扑中。

**DAG 拓扑扩展**：

```
原有: cron → daily_update → kline → fund → treemap/strategy → stats → daily_completeness

新增节点（插入在 kline 之后、treemap/strategy 之前）:
  ... → kline → indicator_incr → model_signal → model_health → treemap/strategy → ...
                      ↓ (按需触发)
                model_train (仅当触发训练时执行，不参与每日自动流水线)
```

**indicator_incr 节点**（每日自动，6 张表并行计算）：
1. 从 `daily_quote` 读取今日 + 前 260 交易日 K 线。
2. 数据清洗：过滤 ST（`stock_master.status`）、过滤停牌（`volume=0`）、过滤上市不足 1 年（`stock_master.ipo_date`）。
3. 并行调用 `strategy/indicators.py` 计算 6 类指标（BOLL / MACD / RSI / ATR / MA / VOLUME），各自写入对应表。
4. INSERT `indicator_calc_log`（每表一条，INCREMENTAL 类型）。

> 全量初始化 `indicator_full` 同样按表并行，6 张表可同时计算，充分利用多核。

**model_signal 节点**（每日自动）：
1. 读取当前 ACTIVE 模型配置（`model_versions`）。
2. 读取今日 `stock_indicators`。
3. 执行评分 → ML 置信度过滤 → 生成信号。
4. INSERT `signal_history`（复用现有表，新增 `ml_confidence` 列）。
5. 如有重大信号 → 飞书推送（复用 `app/feishu/`）。

**model_health 节点**（每日自动）：
1. 查询未了结信号，更新 forward 收益。
2. 检查了结条件（反向信号/止损/超时），更新状态。
3. 执行 `evaluate_model_health()` 5 维度评估。
4. INSERT `model_health`。
5. WARNING/CRITICAL → 飞书告警推送。

**model_train 节点**（手动触发，不参与每日自动流水线）：
1. 通过 `/api/dag_trigger {node: "model_train", ...}` 触发。
2. 从 `stock_indicators` 读取训练窗口内全部指标数据。
3. 执行 Optuna 参数搜索 + Walk-Forward 验证。
4. 训练进度通过 DAG-WS 实时推送到前端。
5. 训练完成 → 飞书审批卡片推送。
6. 支持手动终止：`/api/dag_terminate` → 心跳线程检测 `_stop_requests` → `_terminate_node(log_id)`。

### 4.3 实盘信号生成流程

> 🔗 信号存入 `signal_history` 表（复用现有），新增 `ml_confidence` 列。

**流程步骤**：

1. 从 `model_versions` 读取当前 ACTIVE 版本配置。
2. 从 `stock_indicators` 读取今日监控清单股票指标。
3. ML 评分函数作为 `StrategyBase` 子类注册到 `strategy/engine.py`，信号融合复用 `SignalCombiner`。
4. 信号存入 `signal_history`，新增字段 `ml_confidence`。
5. 如有新信号 → 飞书推送（复用 `app/feishu/`）。

### 4.4 健康度检查与告警流程

**流程步骤**：

1. Cron触发（工作日16:00），读取当前ACTIVE版本。
2. **维度1：胜率退化** — 查询近30天已了结信号（`status='closed'`），计算实盘胜率。与回测胜率比较：
   - `live_win_rate / backtest_win_rate < 0.5` → RED
   - `< 0.7` → YELLOW
   - `≥ 0.7` → GREEN
3. **维度2：信号频率** — 查询近20天信号数量：
   - 0信号 → RED
   - <3信号 → YELLOW
   - ≥3信号 → GREEN
4. **维度3：实盘回撤** — 计算近30天最大回撤：
   - 回撤幅度 > 20%（即`current_drawdown > 0.20`） → RED
   - > 15% → YELLOW
   - ≤ 15% → GREEN
5. **维度4：ML置信度** — 查询近30天信号ML置信度，计算平均：
   - 平均 < 0.4 → YELLOW
   - ≥ 0.4 → GREEN
6. **维度5：模型年龄** — 查询模型上线天数：
   - > 365天 → YELLOW
   - ≤ 365天 → GREEN
7. 综合判定：
   - RED≥2 → CRITICAL
   - RED≥1 → WARNING
   - YELLOW≥2 → CAUTION
   - YELLOW≥1 → WATCH
   - 否则 → HEALTHY
8. INSERT `model_health`。
9. 告警通知：
   - CRITICAL或WARNING → 飞书+微信推送告警
   - CAUTION → 飞书消息通知
   - HEALTHY → 无需通知（周报汇总）

### 4.5 版本回退流程

**流程步骤**：

1. 用户调用 `POST /api/v1/models/v1.1/rollback`。
2. 系统查询目标版本状态：`SELECT status FROM model_versions WHERE version='v1.1'`。
3. 校验目标版本状态必须为ARCHIVED或PENDING。
   - 如果状态不允许回退 → 返回400 Bad Request（错误码40006，版本状态不可回退）。
4. 开始数据库事务：
   - `UPDATE model_versions SET status='ARCHIVED', archived_at=NOW() WHERE status='ACTIVE'`
   - `UPDATE model_versions SET status='ACTIVE', activated_at=NOW() WHERE version='v1.1'`
5. 提交事务。
6. 加载v1.1的 `final_params` 和 `ml_model_path`。
7. 调用 `reload_strategy_service('v1.1')` 通知策略服务重新加载参数。
8. 飞书推送“模型已回退至v1.1”。
9. 返回200 OK `{active_version: "v1.1"}`。

### 4.6 模型创建配置继承流程

**流程步骤**：

1. 用户调用 `POST /api/v1/models/inherit`，请求体含 `inherit_from: "v1.2"`、`version: "v1.3"`、`overrides: {...}`。
2. 系统查询源版本配置：`SELECT * FROM model_versions WHERE version='v1.2'`。
3. 按继承规则合并配置：
   - 第二层（训练方法）：完整继承 optimizer/objective/n_trials/ml_enabled/ml_algorithm/wf_folds/robustness_tolerance
   - 第三层（数据配置）：继承 stock_list/feature_set/target_variable/split_ratio，基于 `rolling_window_years` 自动计算 `window_start_date`
   - 第四层（信号风控）：完整继承 buy_score_threshold/sell_rule/stop_loss_pct/take_profit_pct/signal_timeout_days/ml_confidence_threshold
   - param_search_space：继承基础版本的搜索空间
4. overrides中的配置项覆盖继承值。
5. 计算config_diff：记录被overrides修改的配置项（old值→new值）。
6. INSERT `model_versions`（status=DRAFT，合并后的四层配置，config_diff字段记录差异）。
7. 返回201 Created `{version: "v1.3", status: "DRAFT", inherited_from: "v1.2", config_diff: {...}}`。

---

## 5. API详细设计

### 5.1 API清单

**模型版本管理**

| 方法 | 路径 | 说明 | 幂等性 | 鉴权要求 |
|------|------|------|--------|----------|
| POST | /api/v1/models | 创建新模型版本（四层配置） | 否 | researcher |
| POST | /api/v1/models/inherit | 继承已有版本配置创建新版本 | 否 | researcher |
| GET | /api/v1/models | 查询版本列表 | 是 | 认证用户 |
| GET | /api/v1/models/{version} | 查询单个版本详情（四层配置快照） | 是 | 认证用户 |
| POST | /api/v1/models/{version}/train | 启动训练 | 否 | researcher |
| GET | /api/v1/models/{version}/train/progress | 查询训练进度 | 是 | 认证用户 |
| GET | /api/v1/models/{version}/train/visuals | 获取训练可视化数据 | 是 | 认证用户 |
| GET | /api/v1/models/{version}/evaluation | 获取评估报告 | 是 | 认证用户 |
| POST | /api/v1/models/{version}/approve | 审批通过并上线 | 否 | admin（需X-Confirm-Token） |
| POST | /api/v1/models/{version}/reject | 拒绝版本 | 否 | admin |
| POST | /api/v1/models/{version}/rollback | 回退到指定版本 | 否 | admin（需X-Confirm-Token） |
| POST | /api/v1/models/compare | 版本对比（请求体指定version_a/version_b） | 是 | 认证用户 |
| DELETE | /api/v1/models/{version} | 删除版本（仅DRAFT/REJECTED/ARCHIVED） | 是 | admin |

**信号服务**

| 方法 | 路径 | 说明 | 幂等性 | 鉴权要求 |
|------|------|------|--------|----------|
| GET | /api/v1/signals/{stock_code} | 获取当前活跃模型的指定股票信号 | 是 | 认证用户 |
| POST | /api/v1/signals/scan | 全市场信号扫描 | 否 | researcher |
| GET | /api/v1/signals/history | 查询历史信号及后续收益 | 是 | 认证用户 |
| GET | /api/v1/signals/tracking | 信号追踪明细 | 是 | 认证用户 |

**健康度监控**

| 方法 | 路径 | 说明 | 幂等性 | 鉴权要求 |
|------|------|------|--------|----------|
| GET | /api/v1/health | 当前模型健康度 | 是 | 认证用户 |
| GET | /api/v1/health/trend | 健康度历史趋势 | 是 | 认证用户 |
| GET | /api/v1/health/retrain-check | 重训建议检查 | 是 | 认证用户 |

**共享指标池**

| 方法 | 路径 | 说明 | 幂等性 | 鉴权要求 |
|------|------|------|--------|----------|
| GET | /api/v1/indicators/status | 查看指标池状态 | 是 | 认证用户 |
| POST | /api/v1/indicators/update | 手动触发增量更新 | 否 | admin |
| POST | /api/v1/indicators/rebuild | 手动触发全量重建（仅数据修复场景） | 否 | admin（需X-Confirm-Token） |
| GET | /api/v1/indicators/logs | 查看指标计算日志 | 是 | 认证用户 |
| GET | /api/v1/indicators/{stock_code} | 查看指定股票指标数据 | 是 | 认证用户 |

**认证服务**

| 方法 | 路径 | 说明 | 幂等性 | 鉴权要求 |
|------|------|------|--------|----------|
| POST | /api/v1/auth/login | 用户登录，返回access_token+refresh_token | 否 | 无 |
| POST | /api/v1/auth/refresh | 刷新access_token | 否 | 有效refresh_token |
| POST | /api/v1/auth/confirm | 获取二次确认令牌（5分钟有效） | 否 | admin |

### 5.2 API详细Schema

#### 5.2.1 POST /api/v1/models — 创建新模型版本

请求Body：

```json
{
    "model_name": "BOLL+MACD+RSI+VOL策略",
    "version": "v1.3",
    "description": "滚动5年窗口重训，加入2026上半年新数据",
    "created_by": "admin",

    "training_method": {
        "optimizer": "bayesian",
        "objective": "sharpe",
        "n_trials": 300,
        "ml_enabled": true,
        "ml_algorithm": "xgboost",
        "wf_folds": 5,
        "robustness_tolerance": 0.1
    },

    "data_config": {
        "rolling_window_years": 5,
        "stock_list": ["300274", "603986", "300394", "600021", "301358",
                       "603799", "300442", "300748", "600160", "600352", "002475"],
        "feature_set": "all",
        "target_variable": "next_day_return",
        "split_ratio": "60/20/20"
    },

    "signal_risk_config": {
        "buy_score_threshold": 4,
        "sell_rule": "reverse_signal",
        "stop_loss_pct": 0.08,
        "take_profit_pct": null,
        "signal_timeout_days": 20,
        "ml_confidence_threshold": 0.5
    },

    "param_search_space": {
        "rsi_oversold": {"type": "int", "low": 25, "high": 50},
        "rsi_overbought": {"type": "int", "low": 60, "high": 85},
        "buy_score_threshold": {"type": "int", "low": 2, "high": 6},
        "vol_ratio_threshold": {"type": "float", "low": 1.0, "high": 2.5},
        "sell_score_threshold": {"type": "int", "low": -6, "high": -2}
    },

    "inherit_from": "v1.2",
    "confirm_override": true
}
```

响应：

```json
{
    "code": 0,
    "data": {
        "version": "v1.3",
        "model_name": "BOLL+MACD+RSI+VOL策略",
        "status": "DRAFT",
        "created_at": "2026-06-14T10:00:00",
        "inherited_from": "v1.2",
        "config_diff": {
            "data_config.rolling_window_years": {"old": 3, "new": 5}
        },
        "data_dir": "data/v1.3"
    }
}
```

错误码：40001（版本号已存在）、40002（inherit_from版本不存在）、40003（参数校验失败）

#### 5.2.2 POST /api/v1/models/inherit — 继承创建新版本

请求Body：

```json
{
    "inherit_from": "v1.2",
    "version": "v1.3",
    "description": "继承v1.2配置，只改滚动窗口",
    "overrides": {
        "data_config": {
            "rolling_window_years": 5
        }
    }
}
```

响应：

```json
{
    "code": 0,
    "data": {
        "version": "v1.3",
        "model_name": "BOLL+MACD+RSI+VOL策略",
        "status": "DRAFT",
        "created_at": "2026-06-14T10:00:00",
        "inherited_from": "v1.2",
        "config_diff": {
            "data_config.rolling_window_years": {"old": 3, "new": 5}
        },
        "data_dir": "data/v1.3"
    }
}
```

继承规则：
- 第二层（训练方法）：完整继承
- 第三层（数据配置）：继承stock_list/feature_set/target_variable/split_ratio，自动计算window_start_date
- 第四层（信号风控）：完整继承
- param_search_space：继承基础版本的搜索空间
- overrides中的配置项覆盖继承值，差异记录到config_diff

错误码：40001（版本号已存在）、40002（inherit_from版本不存在）

#### 5.2.3 GET /api/v1/models/{version} — 查询版本详情

响应：

```json
{
    "code": 0,
    "data": {
        "version": "v1.3",
        "model_name": "BOLL+MACD+RSI+VOL策略",
        "description": "滚动5年窗口重训",
        "status": "TRAINING",
        "created_at": "2026-06-14T10:00:00",
        "created_by": "admin",
        "inherit_from": "v1.2",
        "config_diff": {
            "data_config.rolling_window_years": {"old": 3, "new": 5}
        },

        "training_method": {
            "optimizer": "bayesian",
            "objective": "sharpe",
            "n_trials": 300,
            "ml_enabled": true,
            "ml_algorithm": "xgboost",
            "wf_folds": 5,
            "robustness_tolerance": 0.1
        },

        "data_config": {
            "rolling_window_years": 5,
            "window_start_date": "2021-06-14",
            "window_end_date": "2026-06-13",
            "stock_list": ["300274", "603986", "300394", "600021", "301358",
                           "603799", "300442", "300748", "600160", "600352", "002475"],
            "feature_set": "all",
            "target_variable": "next_day_return",
            "split_ratio": "60/20/20"
        },

        "signal_risk_config": {
            "buy_score_threshold": 4,
            "sell_rule": "reverse_signal",
            "stop_loss_pct": 0.08,
            "take_profit_pct": null,
            "signal_timeout_days": 20,
            "ml_confidence_threshold": 0.5
        },

        "training_result": {
            "best_params": {"rsi_oversold": 38, "buy_score_threshold": 4, "vol_ratio_threshold": 1.6},
            "final_params": {"rsi_oversold": 38, "buy_score_threshold": 4, "vol_ratio_threshold": 1.6},
            "param_importance": {"rsi_oversold": 0.52, "buy_score_threshold": 0.31, "vol_ratio_threshold": 0.11},
            "weak_params": ["rsi_overbought", "sell_score_threshold"],
            "train_sharpe": 1.20,
            "val_sharpe": 0.95,
            "test_sharpe": 0.45,
            "wf_avg_sharpe": 0.72,
            "val_train_ratio": 0.79,
            "recommendation": "谨慎上线"
        },

        "timestamps": {
            "created_at": "2026-06-14T10:00:00",
            "training_started_at": "2026-06-14T10:05:00",
            "training_ended_at": null,
            "validated_at": null,
            "activated_at": null,
            "archived_at": null
        }
    }
}
```

错误码：40401（版本不存在）

#### 5.2.4 POST /api/v1/models/{version}/approve — 审批通过并上线

请求Header：`X-Confirm-Token: <二次确认令牌>`

请求Body：

```json
{
    "approved_by": "admin",
    "note": "测试集夏普偏低但WF验证通过，谨慎上线"
}
```

响应：

```json
{
    "code": 0,
    "data": {
        "version": "v1.3",
        "status": "ACTIVE",
        "previous_active": "v1.2",
        "previous_active_status": "ARCHIVED",
        "activated_at": "2026-06-14T15:30:00"
    }
}
```

执行逻辑：
- 校验目标版本状态必须为PENDING
- 校验X-Confirm-Token有效
- 事务内：旧ACTIVE版本→ARCHIVED + 当前版本→ACTIVE
- 通知策略服务重新加载参数
- 飞书通知上线消息

错误码：40004（版本状态不是PENDING）、40010（缺少确认令牌）、50001（版本切换事务失败）

#### 5.2.5 POST /api/v1/models/{version}/reject — 拒绝版本

请求Body：

```json
{
    "note": "测试集夏普为负，拒绝"
}
```

响应：

```json
{
    "code": 0,
    "data": {
        "version": "v1.3",
        "status": "REJECTED",
        "current_active": "v1.2"
    }
}
```

错误码：40005（版本状态不是PENDING）

#### 5.2.6 POST /api/v1/models/{version}/rollback — 版本回退

请求Header：`X-Confirm-Token: <二次确认令牌>`

请求Body：无（路径参数指定目标版本）

响应：

```json
{
    "code": 0,
    "data": {
        "active_version": "v1.1",
        "previous_active": "v1.2",
        "previous_active_status": "ARCHIVED",
        "activated_at": "2026-06-14T16:00:00"
    }
}
```

执行逻辑：
- 校验目标版本状态必须为ARCHIVED或PENDING
- 校验X-Confirm-Token有效
- 事务内：当前ACTIVE→ARCHIVED + 目标版本→ACTIVE
- 通知策略服务重新加载参数
- 飞书通知回退消息

错误码：40006（版本状态不允许回退）、40010（缺少确认令牌）、50001（版本切换事务失败）

#### 5.2.7 GET /api/v1/models/{version}/train/progress — 训练进度

响应：

```json
{
    "code": 0,
    "data": {
        "version": "v1.3",
        "status": "TRAINING",
        "current_trial": 187,
        "total_trials": 300,
        "progress_pct": 62.3,
        "best_score_so_far": 1.234,
        "elapsed_minutes": 45,
        "estimated_remaining_minutes": 30,
        "recent_trials": [
            {"trial": 187, "score": 1.234, "params": {"rsi_oversold": 38}},
            {"trial": 186, "score": 1.228, "params": {"rsi_oversold": 40}}
        ]
    }
}
```

#### 5.2.8 GET /api/v1/health — 当前模型健康度

响应：

```json
{
    "code": 0,
    "data": {
        "version": "v1.2",
        "overall_status": "HEALTHY",
        "should_retrain": false,
        "checks": [
            {"dimension": "win_rate", "level": "GREEN", "message": "实盘胜率52%，与回测55%基本一致"},
            {"dimension": "signal_frequency", "level": "GREEN", "message": "近20日5个信号，正常"},
            {"dimension": "drawdown", "level": "GREEN", "message": "近30天回撤-8.2%，可控"},
            {"dimension": "ml_confidence", "level": "GREEN", "message": "ML平均置信度0.68，正常"},
            {"dimension": "model_age", "level": "GREEN", "message": "模型上线45天"}
        ],
        "metrics": {
            "live_win_rate": 0.52,
            "backtest_win_rate": 0.55,
            "degradation_ratio": 0.945,
            "closed_trades_30d": 12,
            "open_signals": 3
        },
        "checked_at": "2026-06-14T16:00:00"
    }
}
```

#### 5.2.9 GET /api/v1/signals/tracking — 信号追踪明细

Query参数：status（open/closed）、version（模型版本）

响应：

```json
{
    "code": 0,
    "data": {
        "open_signals": [
            {
                "id": 142,
                "stock_code": "300274",
                "signal_date": "2026-06-12",
                "signal_type": "BUY",
                "signal_strength": 4,
                "entry_price": 58.20,
                "ml_confidence": 0.72,
                "days_held": 2,
                "forward_5d_return": null,
                "risk_warning": "仅供理论学习和理论练习使用，不构成投资建议"
            }
        ],
        "closed_signals_recent": [],
        "live_win_rate_30d": 0.52,
        "avg_return_30d": 0.032
    }
}
```

#### 5.2.10 GET /api/v1/indicators/status — 指标池状态

响应：

```json
{
    "code": 0,
    "data": {
        "total_records": 8740000,
        "total_stocks": 3800,
        "date_range": "2016-01-04 ~ 2026-06-13",
        "last_update": "2026-06-13T15:35:00",
        "last_update_stocks": 3785,
        "last_calc_type": "INCREMENTAL"
    }
}
```

#### 5.2.11 POST /api/v1/indicators/update — 手动触发增量更新

请求Body：

```json
{
    "trade_date": "2026-06-13",
    "stock_codes": ["300274"]
}
```

响应：

```json
{
    "code": 0,
    "data": {
        "stocks_updated": 1,
        "duration_seconds": 3,
        "calc_type": "BACKFILL"
    }
}
```

错误码：40007（trade_date非交易日）、50002（增量更新执行失败）

#### 5.2.12 POST /api/v1/indicators/rebuild — 手动全量重建

请求Header：`X-Confirm-Token: <二次确认令牌>`

**重要说明**：此接口仅用于数据源切换、数据修复等场景，**不因指标参数变更触发**。指标参数为行业标准硬编码，不可通过接口修改。若未来业务确需变更参数，需经架构评审、代码变更、全量重算，作为主版本升级处理。

请求Body：

```json
{
    "reason": "数据源迁移，修复2026-06-10至2026-06-12缺失数据",
    "confirmation_token": "xxxx"
}
```

响应：

```json
{
    "code": 0,
    "data": {
        "task_id": "rebuild_20260614_001",
        "status": "RUNNING",
        "estimated_duration_minutes": 30
    }
}
```

注意：全量重建为异步任务，预计20-40分钟完成。

错误码：40010（缺少二次确认令牌）、43003（全量重建任务已在执行中）、43004（确认令牌无效或过期）

#### 5.2.13 DELETE /api/v1/models/{version} — 删除版本

路径参数：`version`

响应：

```json
{
    "code": 0,
    "data": {
        "version": "v1.3",
        "status": "deleted"
    }
}
```

错误码：40007（只能删除DRAFT/REJECTED/ARCHIVED状态的版本）、40401（版本不存在）

#### 5.2.14 POST /api/v1/auth/login — 用户登录

请求Body：

```json
{
    "username": "admin",
    "password": "xxx"
}
```

响应：

```json
{
    "code": 0,
    "data": {
        "access_token": "eyJhbGciOi...",
        "refresh_token": "dGhpcyBpcy...",
        "token_type": "bearer",
        "expires_in": 7200,
        "role": "admin"
    }
}
```

错误码：10004（认证失败）

#### 5.2.15 POST /api/v1/auth/confirm — 获取二次确认令牌

请求Header：`Authorization: Bearer <access_token>`

响应：

```json
{
    "code": 0,
    "data": {
        "confirm_token": "xxxx",
        "expires_in": 300
    }
}
```

错误码：10005（权限不足，仅admin角色可获取）

---

## 6. 定时任务设计

### 6.1 任务清单

| 任务名 | Cron表达式 | 说明 | 执行时段 |
|--------|------------|------|----------|
| 每日增量ETL | 0 30 15 * * 1-5 | 收盘后拉数据→清洗→增量指标→信号生成→健康度更新 | 工作日 15:30 |
| 信号超时关闭 | 0 0 9 * * 1-5 | 检查超时未了结信号，自动关闭 | 工作日 09:00 |
| 模型周报生成 | 0 0 10 * * 0 | 生成模型周报（信号汇总+胜率+健康度趋势） | 周日 10:00 |

### 6.2 每日增量ETL任务

| 项目 | 内容 |
|------|------|
| 触发方式 | DAG 节点 `indicator_incr` → `model_signal` → `model_health`（由 `cron` 节点定时触发） |
| 依赖条件 | K线数据已由 DAG `kline` 节点写入 `daily_quote`（复用 baostock） |
| 执行内容 | 1. indicator_incr：读取 K 线 → 数据清洗 → 调用 `indicators.py` 计算 → UPSERT stock_indicators<br>2. model_signal：读取 ACTIVE 模型 + 今日指标 → 评分 → 生成信号 → INSERT signal_history<br>3. model_health：回填 forward 收益 → 检查了结 → 5 维度评估 → INSERT model_health → 告警 |
| 心跳保活 | 各节点复用 DAG 心跳线程（25s），>5min 无进展标记超时 |
| 幂等性 | stock_indicators 使用 UPSERT，signal_history 先 DELETE 再 INSERT，可安全重跑 |

### 6.3 信号超时关闭任务

| 项目 | 内容 |
|------|------|
| Cron | 0 0 9 * * 1-5（工作日09:00） |
| 依赖条件 | 无 |
| 执行内容 | 1. 查询所有status='open'的信号<br>2. 计算持有天数（交易日天数）<br>3. 持有天数 ≥ signal_timeout_days的信号 → status='closed', close_reason='timeout'<br>4. 计算actual_return（以超时日收盘价计算） |
| 超时处理 | 整体超时5分钟 |
| 失败重试 | 重试2次，间隔1分钟 |

### 6.4 模型周报生成任务

| 项目 | 内容 |
|------|------|
| Cron | 0 0 10 * * 0（周日10:00） |
| 依赖条件 | model_health表有近7天数据 |
| 执行内容 | 1. 汇总近7天信号（买入/卖出/胜率/平均收益）<br>2. 健康度趋势图数据<br>3. 重训建议汇总<br>4. 推送飞书周报 |
| 超时处理 | 整体超时10分钟 |
| 失败重试 | 重试1次，间隔5分钟 |

---

## 7. 配置管理设计

### 7.1 默认配置模板（DEFAULT_MODEL_CONFIG）

```python
DEFAULT_MODEL_CONFIG = {
    # ===== 第一层：身份（必填，无默认值） =====
    "model_name": None,                              # 必填
    "version": None,                                 # 必填，格式 v{主}.{次}
    "description": "",                               # 可选
    "created_by": "system",                          # 默认system

    # ===== 第二层：训练方法 =====
    "optimizer": "bayesian",                         # bayesian/grid/random
    "objective": "sharpe",                           # sharpe/win_rate/calmar/sortino
    "n_trials": 300,                                 # Optuna迭代次数
    "ml_enabled": True,                              # 开启ML增强层
    "ml_algorithm": "xgboost",                       # xgboost/lightgbm/logistic
    "wf_folds": 5,                                   # Walk-Forward折数
    "robustness_tolerance": 0.1,                     # ±10%参数扰动

    # ===== 第三层：数据配置 =====
    "rolling_window_years": 5,                       # 5年滚动窗口
    "window_start_date": None,                       # 自动计算（基于rolling_window_years）
    "window_end_date": None,                         # 自动取最新交易日
    "stock_list": "monitor",                         # 监控清单（默认11只）
    "feature_set": "all",                            # 全量指标特征
    "target_variable": "next_day_return",            # 预测次日收益
    "split_ratio": "60/20/20",                       # 训练/验证/测试比例

    # ===== 第四层：信号与风控 =====
    "buy_score_threshold": 4,                        # 买入信号阈值
    "sell_rule": "reverse_signal",                   # 卖出规则
    "stop_loss_pct": 0.08,                           # 止损线8%
    "take_profit_pct": None,                         # 不设止盈
    "signal_timeout_days": 20,                       # 信号超时20天
    "ml_confidence_threshold": 0.5,                  # ML置信度门槛
}
```

### 7.2 默认参数搜索空间

```python
DEFAULT_PARAM_SEARCH_SPACE = {
    "rsi_oversold": {"type": "int", "low": 25, "high": 50},
    "rsi_overbought": {"type": "int", "low": 60, "high": 85},
    "buy_score_threshold": {"type": "int", "low": 2, "high": 6},
    "vol_ratio_threshold": {"type": "float", "low": 1.0, "high": 2.5},
    "sell_score_threshold": {"type": "int", "low": -6, "high": -2},
}
```

### 7.3 指标计算固定参数（行业标准，硬编码，不可修改）

```python
FIXED_INDICATOR_PARAMS = {
    "boll_period": 20,
    "boll_std": 2.0,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "rsi_period": 14,
    "atr_period": 14,
    "ma_periods": [5, 20, 60, 250],
}
# 以上参数为行业标准，系统内硬编码，不可通过接口修改。
# 若未来业务确需变更，需经过架构评审、代码变更、全量数据重算与版本兼容方案，
# 作为主版本升级处理。全量重建接口(POST /api/v1/indicators/rebuild)仅用于
# 数据源切换、数据修复等场景，不因参数变更触发。
```

### 7.4 滚动窗口配置

```python
ROLLING_WINDOW_CONFIG = {
    "default_window_years": 5,
    "min_window_years": 3,
    "max_window_years": 10,
    "window_by_scenario": {
        "routine_retrain": 5,
        "regime_change": 3,
        "stable_market": 7,
        "initial_training": 10,
    },
    "train_ratio": 0.6,
    "val_ratio": 0.2,
    "test_ratio": 0.2,
}
```

### 7.5 健康度评估阈值配置

```python
HEALTH_CHECK_THRESHOLDS = {
    "win_rate": {
        "red_threshold": 0.5,                        # 实盘/回测比率 < 0.5 → RED
        "yellow_threshold": 0.7,                     # < 0.7 → YELLOW
    },
    "signal_frequency": {
        "red_threshold": 0,                          # 近20天0信号 → RED
        "yellow_threshold": 3,                       # 近20天 < 3信号 → YELLOW
        "lookback_days": 20,
    },
    "drawdown": {
        "red_threshold": 0.20,                       # 回撤幅度 > 20% → RED（current_drawdown为正值）
        "yellow_threshold": 0.15,                    # 回撤幅度 > 15% → YELLOW
        "lookback_days": 30,
    },
    "ml_confidence": {
        "yellow_threshold": 0.4,                     # ML平均置信度 < 0.4 → YELLOW
        "lookback_days": 30,
    },
    "model_age": {
        "yellow_threshold_days": 365,                # 上线 > 365天 → YELLOW
    },
}
```

### 7.6 环境变量清单

| 环境变量 | 说明 | 示例值 | 必填 |
|----------|------|--------|------|
| DATABASE_URL | PostgreSQL连接字符串 | postgresql://user:pass@localhost:5432/strategy | 是 |
| REDIS_URL | Redis连接字符串 | redis://localhost:6379/0 | 是 |
| DATA_ROOT_DIR | 数据文件根目录 | /data/strategy | 是 |
| — | 数据源复用 baostock（K道现有），无需额外 API Key | — | — |
| FEISHU_WEBHOOK_URL | 飞书Bot Webhook地址 | https://open.feishu.cn/open-apis/bot/v2/hook/xxx | 是 |
| FEISHU_WEBHOOK_SECRET | 飞书Webhook签名密钥 | — | 否 |
| SCHEDULER_ENABLED | 是否启用定时任务 | true | 是 |
| LOG_LEVEL | 日志级别 | INFO | 否 |
| TRAINING_TIMEOUT_MINUTES | 训练超时时间（分钟） | 180 | 否 |
| ETL_BATCH_SIZE | ETL批量写入大小 | 100 | 否 |
| INDICATOR_LOOKBACK_DAYS | 增量指标计算回溯天数 | 260 | 否 |
| JWT_SECRET_KEY | JWT签名密钥 | 随机字符串 | 是 |
| JWT_ALGORITHM | JWT签名算法 | HS256 | 是 |
| ACCESS_TOKEN_EXPIRE_MINUTES | 访问令牌有效期（分钟） | 120 | 否 |
| CONFIRM_TOKEN_EXPIRE_MINUTES | 二次确认令牌有效期（分钟） | 5 | 否 |

---

## 8. 错误码与异常处理

### 8.1 错误码范围分配

| 模块 | 错误码范围 | 说明 |
|------|------------|------|
| 通用 | 10000-19999 | 系统级错误、参数校验、认证鉴权 |
| 模型版本管理 | 40000-40999 | 版本CRUD、状态转换 |
| 信号服务 | 41000-41999 | 信号生成、追踪 |
| 健康度监控 | 42000-42999 | 健康度评估、重训建议 |
| 共享指标池 | 43000-43999 | 指标计算、ETL |
| 数据层 | 44000-44999 | 数据拉取、清洗 |
| 训练引擎 | 45000-45999 | 训练执行、Optuna |
| 系统内部 | 50000-50999 | 事务失败、服务异常 |

### 8.2 错误码详细定义

**通用错误（10000-19999）**

| 错误码 | HTTP状态码 | 错误信息 | 处理建议 |
|--------|------------|----------|----------|
| 10001 | 400 | 请求参数校验失败 | 检查请求Body字段类型和取值范围 |
| 10002 | 401 | 未授权 | 检查认证Token |
| 10003 | 500 | 内部服务异常 | 查看服务日志，联系运维 |
| 10004 | 401 | 认证失败 | Token无效或过期，重新登录 |
| 10005 | 403 | 权限不足 | 当前用户角色无操作权限 |

**模型版本管理错误（40000-40999）**

| 错误码 | HTTP状态码 | 错误信息 | 处理建议 |
|--------|------------|----------|----------|
| 40001 | 409 | 版本号已存在 | 更换版本号 |
| 40002 | 404 | 继承的源版本不存在 | 检查inherit_from版本号 |
| 40003 | 400 | 配置参数校验失败 | 检查四层配置项取值范围 |
| 40004 | 409 | 版本状态不是PENDING，无法审批 | 等待版本训练完成 |
| 40005 | 409 | 版本状态不是PENDING，无法拒绝 | 等待版本训练完成 |
| 40006 | 409 | 版本状态不允许回退 | 仅ARCHIVED/PENDING版本可回退 |
| 40007 | 400 | 只能删除DRAFT/REJECTED/ARCHIVED状态的版本 | 先归档再处理 |
| 40008 | 409 | 已存在ACTIVE版本，不允许重复上线 | 先归档当前ACTIVE版本 |
| 40009 | 404 | 版本不存在 | 检查版本号 |
| 40010 | 400 | 缺少二次确认令牌 | 高风险操作需通过POST /api/v1/auth/confirm获取确认令牌 |

**信号服务错误（41000-41999）**

| 错误码 | HTTP状态码 | 错误信息 | 处理建议 |
|--------|------------|----------|----------|
| 41001 | 404 | 当前无活跃模型版本 | 先训练并上线一个模型 |
| 41002 | 404 | 指定股票不在监控清单中 | 检查stock_code |
| 41003 | 404 | 未找到信号记录 | 检查查询条件 |

**健康度监控错误（42000-42999）**

| 错误码 | HTTP状态码 | 错误信息 | 处理建议 |
|--------|------------|----------|----------|
| 42001 | 404 | 当前无活跃模型版本 | 先训练并上线一个模型 |
| 42002 | 404 | 健康度数据不足 | 等待至少1天ETL运行 |

**共享指标池错误（43000-43999）**

| 错误码 | HTTP状态码 | 错误信息 | 处理建议 |
|--------|------------|----------|----------|
| 43001 | 400 | trade_date不是交易日 | 传入有效交易日 |
| 43002 | 500 | 增量指标计算失败 | 查看indicator_calc_log错误日志 |
| 43003 | 409 | 全量重建任务已在执行中 | 等待当前任务完成 |
| 43004 | 400 | 全量重建需有效确认令牌 | 通过POST /api/v1/auth/confirm获取令牌 |

**训练引擎错误（45000-45999）**

| 错误码 | HTTP状态码 | 错误信息 | 处理建议 |
|--------|------------|----------|----------|
| 45001 | 409 | 版本已在训练中，不可重复启动 | 等待当前训练完成 |
| 45002 | 500 | 训练执行异常 | 查看训练日志，可能是数据问题 |
| 45003 | 408 | 训练超时 | 增加TRAINING_TIMEOUT_MINUTES或减少n_trials |

**系统内部错误（50000-50999）**

| 错误码 | HTTP状态码 | 错误信息 | 处理建议 |
|--------|------------|----------|----------|
| 50001 | 500 | 版本切换事务失败 | 检查DB连接，事务已回滚，可重试 |
| 50002 | 500 | 数据源API调用失败 | 检查数据源服务状态和网络连通性 |

### 8.3 统一错误响应格式

```json
{
    "code": 40001,
    "message": "版本号已存在",
    "detail": "Version 'v1.3' already exists in model_versions table",
    "suggestion": "请更换版本号，或使用 /api/v1/models/inherit 从已有版本继承创建"
}
```

---

## 9. 部署架构

### 9.1 系统组件图

```
                        ┌─────────────┐
                        │  数据源      │
                        │ AKShare/    │
                        │ Tushare     │
                        └──────┬──────┘
                               │ HTTP
                               ▼
┌──────────┐    HTTP    ┌──────────────┐    Webhook    ┌──────────┐
│  Vue3    │◄──────────►│  Nginx (:80) │──────────────►│ 飞书Bot  │
│  前端    │            │  反向代理     │               └──────────┘
└──────────┘            └──────┬───────┘
                               │
                    ┌──────────▼──────────────────────┐
                    │        FastAPI 主服务 (:8000)     │
                    │  ┌────────────────────────────┐  │
                    │  │  认证中间件 (JWT验证)       │  │
                    │  ├────────────────────────────┤  │
                    │  │  角色权限验证               │  │
                    │  ├────────────────────────────┤  │
                    │  │  业务逻辑层                  │  │
                    │  │  ├─ 模型版本管理            │  │
                    │  │  ├─ 信号服务                │  │
                    │  │  ├─ 健康度监控              │  │
                    │  │  └─ 指标池服务              │  │
                    │  └────────────────────────────┘  │
                    └──┬──────────┬──────────┬─────────┘
                       │          │          │
              ┌────────▼──┐ ┌────▼────┐ ┌──▼──────────┐
              │ PostgreSQL │ │  Redis  │ │  文件存储    │
              │  (:5432)   │ │ (:6379) │ │ ${DATA_ROOT} │
              └────────────┘ └─────────┘ └──┬──────────┘
                                            │
                          ┌─────────────────▼────────────┐
                          │ 训练Worker (Celery, 4C16G)    │
                          │ ├─ Optuna参数搜索              │
                          │ ├─ Walk-Forward验证            │
                          │ └─ ML增强层训练                │
                          └──────────────────────────────┘
```

### 9.2 数据目录结构

```
${DATA_ROOT_DIR}/
├── shared/                                    # 共享数据区
│   ├── cleaned/                               # 清洗后日行情数据（Parquet缓存，DB为主）
│   │   └── all_stocks.parquet
│   └── indicators/                            # 共享指标池（Parquet缓存，DB为主）
│       └── all_stocks.parquet
│
├── v1.0/                                      # 模型版本目录
│   ├── split/                                 # 数据划分结果
│   │   ├── all_stocks_labeled.parquet         # 标记了train/val/test的数据
│   │   └── split_meta.json                    # 划分元信息（用于复现）
│   ├── training/                              # 训练过程产物
│   │   ├── optuna.db                          # Optuna Study存储
│   │   ├── training_report.json               # 训练报告
│   │   └── visuals/                           # 可视化图表（HTML）
│   ├── evaluation/                            # 评估结果
│   │   ├── final_report.json                  # 综合评估报告
│   │   ├── robustness.json                    # 鲁棒性报告
│   │   ├── walk_forward.json                  # Walk-Forward结果
│   │   └── visuals/                           # 评估可视化图表
│   └── ml/                                    # ML增强层
│       ├── model.pkl                          # 训练好的分类器
│       ├── feature_cols.pkl                   # 特征列名
│       └── threshold_report.json              # 置信度阈值报告
├── v1.1/
│   └── （同上结构）
├── v1.2/
│   └── （同上结构）
└── v1.3/
    └── （同上结构）
```

### 9.3 部署清单

| 组件 | 规格 | 说明 |
|------|------|------|
| FastAPI主服务 | 2C4G | 处理API请求、调度训练任务 |
| 训练Worker | 4C16G | Optuna训练+回测计算，资源消耗较大。CPU即可，GPU非必需但可加速XGBoost/LightGBM训练 |
| PostgreSQL | 4C8G, SSD | 约874万指标记录+模型元数据 |
| Redis | 1C2G | 分布式锁、训练进度缓存、会话管理 |
| Nginx | 1C1G | 反向代理+静态资源 |
| 文件存储 | 50GB SSD | 数据文件、训练产物、ML模型文件 |

### 9.4 安全与认证设计

**认证方案**

- 采用OAuth2 Password Flow + JWT
- 用户登录 `POST /api/v1/auth/login` 返回 access_token 和 refresh_token
- access_token 有效期2小时（可通过环境变量配置）
- refresh_token 有效期7天
- 所有业务API需在请求头携带 `Authorization: Bearer <access_token>`

**二次确认机制**

- 敏感操作（审批上线、版本回退、全量重建指标）需额外提供二次确认令牌
- 二次确认令牌通过 `POST /api/v1/auth/confirm` 获取（需admin角色）
- 确认令牌有效期5分钟，单次使用后失效
- 请求时在Header中携带 `X-Confirm-Token: <confirm_token>`

**角色定义**

| 角色 | 权限 |
|------|------|
| admin | 全部权限：审批、回退、删除、全量重建、用户管理、查看所有数据 |
| researcher | 模型创建、训练、查看所有数据与报告 |
| viewer | 只读访问：查询模型、信号、健康度、指标状态 |

**安全建议**

- 生产环境强制HTTPS
- JWT_SECRET_KEY使用强随机字符串（至少32字节）
- 定期轮换密钥
- API限流：登录接口每分钟最多5次，业务接口每分钟最多60次

---

## 10. 附录

### 10.1 枚举值定义

**模型版本状态（ModelStatus）**

| 值 | 说明 | 允许转换到 |
|----|------|------------|
| DRAFT | 草稿，刚创建 | PREPARING |
| PREPARING | 数据准备中 | TRAINING, DRAFT (失败时) |
| TRAINING | 训练中 | VALIDATING, DRAFT (失败时) |
| VALIDATING | 验证中 | PENDING, DRAFT (失败时) |
| PENDING | 待审批 | ACTIVE, REJECTED |
| ACTIVE | 已上线（当前生效） | ARCHIVED |
| REJECTED | 已拒绝 | DRAFT（重新编辑后重新训练） |
| ARCHIVED | 已归档 | ACTIVE（回退） |

**优化方法（OptimizerType）**

| 值 | 说明 |
|----|------|
| bayesian | 贝叶斯优化（Optuna TPE，默认） |
| grid | 网格搜索 |
| random | 随机搜索 |

**目标函数（ObjectiveType）**

| 值 | 说明 |
|----|------|
| sharpe | 夏普比率（默认） |
| win_rate | 胜率 |
| calmar | Calmar比率 |
| sortino | Sortino比率 |

**ML算法（MLAlgorithmType）**

| 值 | 说明 |
|----|------|
| xgboost | XGBoost分类器（默认） |
| lightgbm | LightGBM分类器 |
| logistic | 逻辑回归 |

**卖出规则（SellRuleType）**

| 值 | 说明 |
|----|------|
| reverse_signal | 反向信号卖出（默认） |
| stop_loss | 仅止损卖出 |
| combined | 反向信号+止损组合 |

**信号类型（SignalType）**

| 值 | 说明 |
|----|------|
| BUY | 买入信号 |
| SELL | 卖出信号 |

**信号状态（SignalStatus）**

| 值 | 说明 |
|----|------|
| open | 未了结 |
| closed | 已了结 |

**信号了结原因（CloseReason）**

| 值 | 说明 |
|----|------|
| reverse_signal | 反向信号了结 |
| stop_loss | 触发止损 |
| timeout | 超时关闭 |

**健康度状态（HealthStatus）**

| 值 | 说明 | 行动 |
|----|------|------|
| HEALTHY | 所有维度正常 | 继续运行（周报汇总） |
| WATCH | 1个黄灯 | 关注但不行动（周报标注） |
| CAUTION | 2+个黄灯 | 人工检查，考虑重训（飞书消息） |
| WARNING | 1个红灯 | 建议尽快重训（飞书+微信） |
| CRITICAL | 2+个红灯 | 强烈建议重训或回退（飞书+微信+电话） |

**健康度检查维度（HealthDimension）**

| 值 | 说明 | RED条件 | YELLOW条件 | GREEN条件 |
|----|------|---------|------------|-----------|
| win_rate | 实盘胜率退化 | 实盘/回测 < 0.5 | 实盘/回测 < 0.7 | ≥ 0.7 |
| signal_frequency | 信号频率 | 近20天0信号 | 近20天 < 3信号 | ≥ 3信号 |
| drawdown | 实盘回撤 | 回撤幅度 > 20% | 回撤幅度 > 15% | ≤ 15% |
| ml_confidence | ML置信度 | — | 平均 < 0.4 | ≥ 0.4 |
| model_age | 模型年龄 | — | 上线 > 365天 | ≤ 365天 |

**指标计算类型（CalcType）**

| 值 | 说明 |
|----|------|
| FULL_INIT | 首次全量计算 |
| INCREMENTAL | 每日增量更新 |
| BACKFILL | 补数据 |

**版本比较类型（ComparisonType）**

| 值 | 说明 |
|----|------|
| params | 参数对比 |
| performance | 绩效对比 |
| robustness | 鲁棒性对比 |

### 10.2 数据字典

**数据清洗规则**

| 编号 | 规则 | 说明 |
|------|------|------|
| R-01 | 排除上市不足1年的新股 | list_date 距参考日 < 365天 |
| R-02 | 排除ST/*ST股票 | is_st = true |
| R-03 | 排除退市股 | 退市后无交易数据，自动过滤 |
| R-04 | 排除停牌日 | volume = 0 |
| R-05 | 数据连续性检查 | 每只股票至少500个交易日 |
| R-06 | 后复权处理 | 在数据拉取时完成 |

**重训触发条件**

| 编号 | 触发条件 | 优先级 | 说明 |
|------|----------|--------|------|
| T-01 | 健康度CRITICAL/WARNING | HIGH | 实盘表现严重退化 |
| T-02 | 距上次训练 > 365天 | MEDIUM | 数据积累足够 |
| T-03 | 策略逻辑变更 | HIGH | 人工触发，主版本升级 |
| T-04 | 发现弱参数需要砍掉 | LOW | 人工触发 |

**版本命名规则**

```
格式：v{主版本}.{次版本}

主版本：策略逻辑变更时+1（如改了信号规则、新增指标）
次版本：同一逻辑下重训参数时+1

示例：
  v1.0 → 初始版本，BOLL+MACD背离+RSI+量能策略
  v1.1 → 同策略重训（加了6个月新数据）
  v1.2 → 同策略重训（砍掉弱参数后重新训练）
  v2.0 → 策略逻辑变更（如增加了KDJ指标）
  v2.1 → v2.0逻辑下的参数重训
```

### 10.3 默认监控清单

| 股票代码 | 说明 |
|----------|------|
| 300274 | — |
| 603986 | — |
| 300394 | — |
| 600021 | — |
| 301358 | — |
| 603799 | — |
| 300442 | — |
| 300748 | — |
| 600160 | — |
| 600352 | — |
| 002475 | — |

### 10.4 增量指标计算回溯窗口说明

增量计算每只股票当日指标时，需要回溯历史K线数据：

| 指标 | 需要回溯的天数 | 说明 |
|------|----------------|------|
| BOLL(20) | 20个交易日 | 20日均线+标准差 |
| MACD(12,26,9) | 35个交易日 | 26日EMA+9日信号线 |
| RSI(14) | 15个交易日 | 14日RS计算 |
| ATR(14) | 15个交易日 | 14日真实波幅均值 |
| MA250 | 250个交易日 | 250日均线 |
| OBV | 累计 | 需从数据起始日累计 |

实际取值：统一回溯260个交易日（约1年），满足所有指标的计算需求。

### 10.5 日常运维节奏

| 频率 | 时间 | 内容 | 执行方式 |
|------|------|------|----------|
| 每日 | 15:30 | 增量ETL（拉数据→算指标→出信号→回填收益→健康度评估） | 自动 |
| 每日 | 09:00 | 信号超时关闭 | 自动 |
| 每周 | 周日10:00 | 模型周报（信号汇总+胜率+健康度趋势+重训建议） | 自动 |
| 每月 | 月末 | 审查月报，决定是否重训，评估策略逻辑 | 人工 |
| 按需 | — | 市场重大变化时手动触发重训、策略逻辑调整后主版本升级 | 人工 |

### 10.6 测试策略概要

| 测试类型 | 覆盖范围 | 工具/方法 |
|----------|----------|-----------|
| 单元测试 | 指标计算函数、评分引擎、健康度各维度评估函数、状态机转换逻辑 | pytest，覆盖率 > 80% |
| 集成测试 | API完整链路（请求→DB写入→响应）、ETL流水线端到端、训练Worker完整流程 | pytest + Docker Compose |
| 回归测试 | 固定数据集的回测结果一致性（确保指标计算/训练流程无意外变更） | 预置parquet数据 + 校验训练结果哈希 |
| 性能测试 | 增量ETL执行时间（目标<30分钟）、指标查询QPS（目标>500） | Locust |
| 安全测试 | 认证绕过、权限提升、API参数注入、JWT令牌伪造 | OWASP ZAP + 手动渗透测试 |

---

## 11. 模型评估与可视化

### 11.1 评估维度

训练完成后，系统自动生成评估报告（存入 `model_versions.evaluation_report` JSONB），用户在审批前通过可视化页面判断模型质量。

| 维度 | 指标 | 数据来源 |
|------|------|---------|
| 收益能力 | 年化收益率、累计收益率、超额收益（vs 基准） | 回测引擎计算 |
| 风险控制 | 最大回撤、夏普比率、卡玛比率、波动率 | 回测引擎计算 |
| 胜率 | 总胜率、买入胜率、卖出胜率 | `training_trials` + 回测 |
| 稳定性 | Walk-Forward 各期收益标准差、参数敏感度 | `training_trials.param_importance` |
| 对比 | 与上一版本在各维度上的变化（↑/↓） | `version_comparisons` |

### 11.2 评估页面布局

```
┌─────────────────────────────────────────────────────┐
│  模型 v1.3 评估报告                        [审批] [拒绝] │
├──────────────────┬──────────────────────────────────┤
│  核心指标卡片     │  回测权益曲线 (ECharts)             │
│  ┌────┐ ┌────┐  │                                    │
│  │夏普│ │回撤│  │   ████░░░░███░░░███░░░░████        │
│  │2.15│ │12% │  │   ░░░░░░████░░░░░██████░░░░        │
│  └────┘ └────┘  │   ← 基准收益  ← 策略收益             │
│  ┌────┐ ┌────┐  │                                    │
│  │胜率│ │年化│  │                                    │
│  │58% │ │22% │  │                                    │
│  └────┘ └────┘  │                                    │
├──────────────────┴──────────────────────────────────┤
│  Walk-Forward 各期收益明细 (表格)                      │
│  ┌──────┬──────┬──────┬──────┬──────┐                │
│  │ 周期 │ 收益 │ 胜率 │ 夏普 │ 回撤 │                │
│  ├──────┼──────┼──────┼──────┼──────┤                │
│  │ Q1   │ +8%  │ 62%  │ 2.4  │ -5%  │                │
│  │ Q2   │ +5%  │ 55%  │ 1.8  │ -8%  │                │
│  └──────┴──────┴──────┴──────┴──────┘                │
├─────────────────────────────────────────────────────┤
│  参数重要性 (Optuna)                  版本对比 (vs v1.2) │
│  BOLL ████████░░ 0.35             夏普 +0.15 ↑        │
│  RSI  ████░░░░░░ 0.18             回撤 -2.1%  ↓        │
│  MACD ██░░░░░░░░ 0.09             胜率 +3.2%  ↑        │
└─────────────────────────────────────────────────────┘
```

### 11.3 审批决策支持

| 数据 | 展示方式 | 决策参考 |
|------|---------|---------|
| 核心指标 vs 阈值 | 绿色达标 / 红色不达标 | 夏普 < 0.5 建议拒绝 |
| Walk-Forward 稳定性 | 各期收益标准差 | 近期连续下滑 → 过拟合风险 |
| 参数重要性 | 水平柱状图 | 弱参数建议砍掉后重新训练 |
| 历史版本对比 | 雷达图 / 差值表 | 全面优于上一版 → 建议审批 |

---

## 12. 实盘跟进设计

### 12.1 核心原则：评价模型，不评价用户

模型信号的质量与用户是否执行无关。系统只追踪"如果按照信号操作，结果如何"，不追踪用户的实际交易行为。理由：

- 用户可能因资金/仓位/主观判断等原因不执行信号
- 用户执行的时机/价格可能与信号不完全一致
- 将模型评价与用户行为绑定会导致评价失真

### 12.2 理论收益追踪实现

每笔信号发出后，`model_health` 节点每日回填前向收益。

**交易日偏移（使用 `trade_calendar`）**：

```python
def get_forward_trade_date(signal_date: str, offset_days: int) -> str:
    """获取信号日后第 N 个交易日的日期。"""
    rows = db.execute(text("""
        SELECT cal_date FROM trade_calendar
        WHERE is_trade_day = true AND cal_date > :d
        ORDER BY cal_date ASC LIMIT 1 OFFSET :n
    """), {"d": signal_date, "n": offset_days - 1})
    return rows.scalar()
```

**回填逻辑**（`model_health` 节点每次执行）：

```python
def backfill_forward_returns(today: str):
    """回填到期的 forward 收益。"""
    for offset, col in [(5, 'forward_5d_return'), (10, 'forward_10d_return'), (20, 'forward_20d_return')]:
        target_date = get_forward_trade_date_for_offset(today, offset)
        # 找到信号日期 = target_date 且该列仍为 NULL 的信号
        signals = db.execute(text(f"""
            SELECT id, stock_code, signal_date, price FROM signal_history
            WHERE signal_date = :d AND {col} IS NULL AND direction = 'buy'
        """), {"d": target_date}).fetchall()

        for sig in signals:
            close = db.execute(text(
                "SELECT close_hfq FROM daily_quote WHERE stock_code=:c AND trade_date=:d"
            ), {"c": sig.stock_code, "d": today}).scalar()
            if close and sig.price:
                ret = (float(close) - float(sig.price)) / float(sig.price)
                db.execute(text(f"UPDATE signal_history SET {col}=:r WHERE id=:id"),
                           {"r": round(ret, 4), "id": sig.id})
```

**信号了结**（`model_health` 节点每次执行）：

```python
def close_signals(today: str):
    """检查了结条件并关闭信号。"""
    open_signals = db.execute(text("""
        SELECT id, stock_code, signal_date, price, direction FROM signal_history
        WHERE status = 'open'
    """)).fetchall()

    for sig in open_signals:
        close_price = get_close(sig.stock_code, today)
        reason = None

        # 条件1：反向信号了结 (BUY信号出现SELL → 了结)
        if sig.direction == 'buy' and has_sell_signal(sig.stock_code, today):
            reason = 'reverse_signal'
        # 条件2：止损 (跌幅超过 8%)
        elif close_price and sig.price and (float(close_price) - float(sig.price)) / float(sig.price) < -0.08:
            reason = 'stop_loss'
        # 条件3：超时 (超过 20 个交易日未了结)
        elif trading_days_since(sig.signal_date, today) > 20:
            reason = 'timeout'

        if reason:
            actual_ret = (float(close_price) - float(sig.price)) / float(sig.price) if close_price and sig.price else None
            db.execute(text("""
                UPDATE signal_history
                SET status='closed', actual_return=:r, close_reason=:c, closed_at=CURRENT_DATE
                WHERE id=:id
            """), {"r": round(actual_ret, 4) if actual_ret else None, "c": reason, "id": sig.id})
```

**执行时序**：
```
T日收盘 → model_health 节点:
  1. 回填 T-5/T-10/T-20 日前信号的 forward 收益
  2. 检查所有 open 信号的了结条件
  3. 更新 signal_history (forward 列 + status)
  4. 汇总 → INSERT model_health
```

### 12.3 实盘健康度监控

`model_health` 表每日评估 5 个维度（见 §3.2.8），不依赖用户是否执行：

| 维度 | 计算方式 | 数据来源 |
|------|---------|---------|
| 信号胜率 | 已了结信号中 `actual_return > 0` 的比例 | `signal_history` |
| 收益稳定性 | 最近 20 个信号 forward_5d 的标准差 | `signal_history` |
| 超额收益 | 信号组合收益 vs 基准指数同期收益 | `signal_history` + `daily_quote` |
| 回撤控制 | 信号组合的最大连续亏损 | `signal_history` |
| 信号质量衰减 | 近 30 天胜率 vs 回测胜率的比值 | `signal_history` + `model_versions.evaluation_report` |

### 12.4 与用户持仓的弱关联

虽然不追踪用户是否执行，但提供一个参考视图：持仓页"今日信号"列（已在 K道实现）显示当前持仓股在最近交易日是否有买卖信号。这是**信息展示**，不是**操作指令**。

```sql
-- 持仓与信号弱关联查询（已在 K道 /api/portfolio 实现）
SELECT p.stock_code, p.stock_name,
       sh.direction, sh.signal_date, sh.strength
FROM portfolio p
LEFT JOIN signal_history sh
  ON sh.stock_code = p.stock_code
 AND sh.combined_signal = true
 AND sh.signal_date = (最近交易日)
```

> 用户看到信号后自主决策。模型评价只看信号的 forward 收益，不看用户是否执行。

---

## 13. 平台复用清单

> 本模块部署在 K道平台之上，以下为复用汇总。

| 现有平台能力 | 本文档对应位置 |
|-------------|--------------|
| `daily_quote` 表（K线 + 复权价） | §3.2.1 — 删除自建 `daily_quotes`，复用 |
| `stock_master` 表（代码/上市日/ST状态） | §4.2 — IPO/ST 过滤 |
| `trade_calendar` 表（交易日历） | §4.1 — 训练窗口对齐交易日 |
| `strategy/indicators.py`（BOLL/MACD/RSI/ATR） | §3.2.2 — 指标计算 |
| DAG 流水线（调度/心跳/WS/终止） | §4.2 — indicator_incr / model_signal / model_health / model_train |
| DAG-WS（训练进度推送） | §4.1 — 替代独立 WebSocket |
| `app/feishu/`（通知 + 卡片审批） | §4.1 / §4.4 |
| `signal_history` 表（信号存储） | §4.3 — 替代 `signal_outputs` |
| `StrategyEngine` + `SignalCombiner` | §4.3 — ML 策略注册 |
| `app/auth/auth.py`（JWT） | API 认证 |
| Naive UI + 主题系统 | 前端管理界面 |
