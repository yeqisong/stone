"""数据库 Schema 定义与管理。使用原生 SQL 确保与 PRD DDL 一致。"""
from sqlalchemy import text

SCHEMA_VERSION = 1

# ── 建表 SQL（按依赖顺序）──

CREATE_TRADE_CALENDAR = """
CREATE TABLE IF NOT EXISTS trade_calendar (
    cal_date     DATE NOT NULL,
    is_trade_day BOOLEAN NOT NULL,
    exchange     VARCHAR(4) NOT NULL,
    PRIMARY KEY (cal_date, exchange)
);
CREATE INDEX IF NOT EXISTS idx_tc_date ON trade_calendar (cal_date, is_trade_day);
"""

CREATE_STOCK_MASTER = """
CREATE TABLE IF NOT EXISTS stock_master (
    stock_code  VARCHAR(6) NOT NULL,
    stock_name  VARCHAR(30) NOT NULL,
    exchange    VARCHAR(4) NOT NULL,
    ipo_date    DATE,
    status      VARCHAR(10) DEFAULT 'N',
    stock_type  VARCHAR(10) DEFAULT 'stock',
    status_date DATE,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, stock_type)
);
CREATE INDEX IF NOT EXISTS idx_sm_status ON stock_master (status, exchange);
"""

CREATE_DAILY_QUOTE = """
CREATE TABLE IF NOT EXISTS daily_quote (
    id                SERIAL,
    trade_date        DATE NOT NULL,
    exchange          VARCHAR(4) NOT NULL,
    stock_code        VARCHAR(6) NOT NULL,
    stock_name        VARCHAR(30) NOT NULL,
    open              NUMERIC(10,2) NOT NULL,
    high              NUMERIC(10,2) NOT NULL,
    low               NUMERIC(10,2) NOT NULL,
    close             NUMERIC(10,2) NOT NULL,
    close_hfq         NUMERIC(10,3),
    close_qfq         NUMERIC(10,3),
    adj_factor_hfq    NUMERIC(10,6) DEFAULT 1.000000,
    adj_factor_qfq    NUMERIC(10,6) DEFAULT 1.000000,
    is_ex_date        BOOLEAN DEFAULT false,
    ex_date_confirmed BOOLEAN DEFAULT false,
    is_suspended      BOOLEAN DEFAULT false,
    volume            BIGINT NOT NULL,
    amount            NUMERIC(18,2) NOT NULL,
    turnover          NUMERIC(8,4),
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dq_unique ON daily_quote (stock_code, exchange, trade_date);
CREATE INDEX IF NOT EXISTS idx_dq_date ON daily_quote (trade_date);
CREATE INDEX IF NOT EXISTS idx_dq_stock_date ON daily_quote (stock_code, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_dq_exchange_date ON daily_quote (exchange, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_dq_ex_date ON daily_quote (is_ex_date, ex_date_confirmed);
"""

CREATE_INDEX_DAILY_QUOTE = """
CREATE TABLE IF NOT EXISTS index_daily_quote (
    trade_date DATE NOT NULL,
    index_code VARCHAR(16) NOT NULL,
    index_name VARCHAR(64) NOT NULL,
    open       NUMERIC(10,2) NOT NULL,
    high       NUMERIC(10,2) NOT NULL,
    low        NUMERIC(10,2) NOT NULL,
    close      NUMERIC(10,2) NOT NULL,
    volume        BIGINT NOT NULL,
    amount        NUMERIC(18,2) NOT NULL,
    is_suspended  BOOLEAN DEFAULT false,
    PRIMARY KEY (trade_date, index_code)
);
"""

CREATE_CORPORATE_ACTIONS = """
CREATE TABLE IF NOT EXISTS corporate_actions (
    id              SERIAL PRIMARY KEY,
    stock_code      VARCHAR(6) NOT NULL,
    stock_name      VARCHAR(30),
    ex_date         DATE NOT NULL,
    cash_div        NUMERIC(10,4) DEFAULT 0,
    bonus_ratio     NUMERIC(10,4) DEFAULT 0,
    transfer_ratio  NUMERIC(10,4) DEFAULT 0,
    rights_ratio    NUMERIC(10,4) DEFAULT 0,
    rights_price    NUMERIC(10,2) DEFAULT 0,
    source          VARCHAR(20) DEFAULT 'detect',
    confirmed       BOOLEAN DEFAULT false,
    confirmed_at    TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (stock_code, ex_date)
);
CREATE INDEX IF NOT EXISTS idx_ca_ex_date ON corporate_actions (ex_date);
CREATE INDEX IF NOT EXISTS idx_ca_confirmed ON corporate_actions (confirmed, ex_date);
"""

CREATE_PORTFOLIO = """
CREATE TABLE IF NOT EXISTS portfolio (
    id          SERIAL PRIMARY KEY,
    stock_code  VARCHAR(6) NOT NULL,
    stock_name  VARCHAR(30) NOT NULL,
    exchange    VARCHAR(4) NOT NULL,
    quantity    INTEGER NOT NULL CHECK (quantity >= 0),
    cost_price  NUMERIC(10,3) NOT NULL DEFAULT 0,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active   BOOLEAN NOT NULL DEFAULT true,
    notes       VARCHAR(200) DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_portfolio_active_stock
    ON portfolio (stock_code) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_portfolio_active
    ON portfolio (is_active, updated_at DESC);
"""

CREATE_PORTFOLIO_HISTORY = """
CREATE TABLE IF NOT EXISTS portfolio_history (
    id               SERIAL PRIMARY KEY,
    stock_code       VARCHAR(6) NOT NULL,
    stock_name       VARCHAR(20) NOT NULL,
    action           VARCHAR(10) NOT NULL,
    quantity_before  INTEGER NOT NULL,
    quantity_after   INTEGER NOT NULL,
    cost_before      NUMERIC(10,3) NOT NULL,
    cost_after       NUMERIC(10,3) NOT NULL,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ph_stock ON portfolio_history (stock_code, created_at DESC);
"""

CREATE_SIGNAL_HISTORY = """
CREATE TABLE IF NOT EXISTS signal_history (
    id                SERIAL PRIMARY KEY,
    signal_date       DATE NOT NULL,
    stock_code        VARCHAR(6) NOT NULL,
    stock_name        VARCHAR(30) NOT NULL,
    direction         VARCHAR(7) NOT NULL,
    strength          SMALLINT NOT NULL,
    strategy_name     VARCHAR(30) NOT NULL,
    reason            TEXT NOT NULL,
    price             NUMERIC(10,2) NOT NULL,
    preference        VARCHAR(10) DEFAULT 'balanced',
    suggested_action  VARCHAR(20),
    combined_signal   BOOLEAN DEFAULT false,
    source_strategies TEXT,
    is_pushed         BOOLEAN DEFAULT false,
    pushed_at         TIMESTAMP,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    params_snapshot   TEXT DEFAULT '',
    ml_confidence     DECIMAL(5,3),
    forward_5d_return DECIMAL(8,4),
    forward_10d_return DECIMAL(8,4),
    forward_20d_return DECIMAL(8,4),
    actual_return     DECIMAL(8,4),
    close_reason      VARCHAR(30),
    closed_at         TIMESTAMP,
    model_version     VARCHAR(20),
    predict_5d_return DECIMAL(8,4),
    predict_10d_return DECIMAL(8,4),
    predict_20d_return DECIMAL(8,4),
    predict_score     DECIMAL(8,4)
);
CREATE INDEX IF NOT EXISTS idx_sh_date ON signal_history (signal_date);
CREATE INDEX IF NOT EXISTS idx_sh_stock ON signal_history (stock_code, signal_date DESC);
CREATE INDEX IF NOT EXISTS idx_sh_direction ON signal_history (direction, signal_date);
"""

CREATE_STRATEGY_CONFIG = """
CREATE TABLE IF NOT EXISTS strategy_config (
    id            SERIAL PRIMARY KEY,
    strategy_name VARCHAR(30) NOT NULL UNIQUE,
    display_name  VARCHAR(30) DEFAULT '',
    enabled       BOOLEAN NOT NULL DEFAULT true,
    params        TEXT NOT NULL DEFAULT '{}',
    updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by    VARCHAR(20) DEFAULT 'manual'
);
"""

CREATE_STRATEGY_PARAM_LOG = """
CREATE TABLE IF NOT EXISTS strategy_param_log (
    id            SERIAL PRIMARY KEY,
    strategy_name VARCHAR(30) NOT NULL,
    param_key     VARCHAR(30) NOT NULL,
    old_value     TEXT,
    new_value     TEXT,
    changed_by    VARCHAR(20) DEFAULT 'manual',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_STOCK_INDUSTRY = """
CREATE TABLE IF NOT EXISTS stock_industry (
    stock_code         VARCHAR(6) NOT NULL,
    exchange           VARCHAR(4) NOT NULL,
    industry_sw        VARCHAR(20),
    industry_exchange  VARCHAR(20),
    updated_at         DATE,
    PRIMARY KEY (stock_code, exchange)
);
"""

CREATE_FAILED_DOWNLOADS = """
CREATE TABLE IF NOT EXISTS failed_downloads (
    id           SERIAL PRIMARY KEY,
    exchange     VARCHAR(4) NOT NULL,
    trade_date   DATE NOT NULL,
    data_type    VARCHAR(20) NOT NULL,
    error_msg    TEXT,
    retry_count  SMALLINT DEFAULT 0,
    status       VARCHAR(10) DEFAULT 'pending',
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at  TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_fd_status ON failed_downloads (status, exchange);
"""

CREATE_STOCK_FUNDAMENTALS = """
CREATE TABLE IF NOT EXISTS stock_fundamentals (
    stock_code      VARCHAR(6) NOT NULL,
    trade_date      DATE NOT NULL DEFAULT CURRENT_DATE,  -- 数据日期（快照语义：最新一条）
    stock_name      VARCHAR(20),
    industry        VARCHAR(50),
    pe_ttm          NUMERIC(10,2),
    pe              NUMERIC(10,2),
    pb_mrq          NUMERIC(10,2),
    ps              NUMERIC(10,2),
    ps_ttm          NUMERIC(10,2),
    roe             NUMERIC(10,2),
    revenue_yoy     NUMERIC(10,2),
    profit_yoy      NUMERIC(10,2),
    total_shares    BIGINT,
    float_share     BIGINT,
    free_share      BIGINT,
    market_cap      BIGINT,
    circ_mv         BIGINT,
    dv_ratio        NUMERIC(10,4),
    dv_ttm          NUMERIC(10,4),
    turnover_rate   NUMERIC(10,4),
    volume_ratio    NUMERIC(10,4),
    limit_status    INTEGER,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code)  -- 快照模型：每股票一条最新（历史走势由 stock_fundamentals_history 承担）
);
"""

CREATE_FUNDAMENTALS_HISTORY = """
CREATE TABLE IF NOT EXISTS stock_fundamentals_history (
    id            SERIAL PRIMARY KEY,
    stock_code    VARCHAR(6) NOT NULL,
    report_date   DATE NOT NULL,         -- 财报截止日 (2024-12-31)
    pe_ttm        NUMERIC(10,2),
    pb_mrq        NUMERIC(10,2),
    roe           NUMERIC(10,2),
    revenue_yoy   NUMERIC(10,2),
    profit_yoy    NUMERIC(10,2),
    total_shares  BIGINT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (stock_code, report_date)
);
CREATE INDEX IF NOT EXISTS idx_fh_stock_date ON stock_fundamentals_history (stock_code, report_date);
"""

CREATE_TREEMAP_CACHE = """
CREATE TABLE IF NOT EXISTS stock_treemap_cache (
    id           SERIAL PRIMARY KEY,
    trade_date   DATE NOT NULL,
    metric       VARCHAR(10) NOT NULL DEFAULT 'mcap',
    parent       VARCHAR(10) NOT NULL DEFAULT 'root',
    node_id      VARCHAR(10) NOT NULL,
    name         VARCHAR(50) NOT NULL,
    value        NUMERIC(18,2) NOT NULL,
    chg_pct      NUMERIC(8,2),
    trend_up     BOOLEAN DEFAULT true,
    node_type    VARCHAR(10) NOT NULL,
    detail       TEXT DEFAULT '',
    UNIQUE (trade_date, metric, node_id)
);
CREATE INDEX IF NOT EXISTS idx_tm_date_metric ON stock_treemap_cache (trade_date, metric, parent);
CREATE INDEX IF NOT EXISTS idx_tc_date ON stock_treemap_cache (trade_date, parent);
"""

# ── 模型训练：版本管理 ──

CREATE_MODEL_VERSIONS = """
CREATE TABLE IF NOT EXISTS model_versions (
    version       VARCHAR(20) PRIMARY KEY,
    model_name    VARCHAR(100) NOT NULL,
    status        VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    config        JSONB NOT NULL DEFAULT '{}',
    best_params   JSONB,
    evaluation_report JSONB,
    sharpe        DECIMAL(8,4),
    win_rate      DECIMAL(5,4),
    max_drawdown  DECIMAL(5,4),
    annual_return DECIMAL(5,4),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    trained_at    TIMESTAMP,
    activated_at  TIMESTAMP,
    archived_at   TIMESTAMP,
    deleted_at    TIMESTAMP,
    CONSTRAINT chk_model_status CHECK (status IN ('DRAFT','TRAINING','VALIDATING','PENDING','ACTIVE','REJECTED','ARCHIVED'))
);
"""

CREATE_MODEL_TRIALS = """
CREATE TABLE IF NOT EXISTS training_trials (
    id           SERIAL PRIMARY KEY,
    version      VARCHAR(20) NOT NULL REFERENCES model_versions(version),
    trial_number INTEGER NOT NULL,
    params       JSONB,
    score        DECIMAL(8,4),
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (version, trial_number)
);
"""

CREATE_MODEL_COMPARISONS = """
CREATE TABLE IF NOT EXISTS version_comparisons (
    id           SERIAL PRIMARY KEY,
    version_a    VARCHAR(20) NOT NULL REFERENCES model_versions(version),
    version_b    VARCHAR(20) NOT NULL REFERENCES model_versions(version),
    sharpe_diff  DECIMAL(8,4),
    winrate_diff DECIMAL(5,4),
    drawdown_diff DECIMAL(5,4),
    report       JSONB,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_MODEL_HEALTH = """
CREATE TABLE IF NOT EXISTS model_health (
    id           SERIAL PRIMARY KEY,
    version      VARCHAR(20) NOT NULL REFERENCES model_versions(version),
    check_date   DATE NOT NULL,
    health_status VARCHAR(20) NOT NULL DEFAULT 'HEALTHY',
    live_win_rate DECIMAL(5,4),
    signal_count  INTEGER DEFAULT 0,
    avg_forward_5d DECIMAL(8,4),
    max_drawdown  DECIMAL(5,4),
    rank_ic       DECIMAL(8,4),   -- ACTIVE 模型近窗滚动 RankIC 均值（IC 衰减监控，v3.6）
    rank_icir     DECIMAL(8,4),   -- 滚动 ICIR
    detail       JSONB DEFAULT '{}',
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (version, check_date)
);
CREATE INDEX IF NOT EXISTS idx_mh_version_date ON model_health (version, check_date DESC);
"""

# ── 回测记录（v2.0 重构 迭代 5.3）──

CREATE_BACKTEST_RECORDS = """
CREATE TABLE IF NOT EXISTS backtest_records (
    id              SERIAL PRIMARY KEY,
    version         VARCHAR(20) NOT NULL REFERENCES model_versions(version),
    start_date      DATE,
    end_date        DATE,
    initial_cash    NUMERIC(18,2) DEFAULT 1000000,
    final_equity    NUMERIC(18,2),
    sharpe_ratio    DECIMAL(8,4),
    win_rate        DECIMAL(5,4),
    max_drawdown    DECIMAL(5,4),
    annual_return   DECIMAL(5,4),
    total_trades    INTEGER DEFAULT 0,
    winning_trades  INTEGER DEFAULT 0,
    detail          JSONB DEFAULT '{}',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_BACKTEST_TRADES = """
CREATE TABLE IF NOT EXISTS backtest_trades (
    id              SERIAL PRIMARY KEY,
    version         VARCHAR(20) NOT NULL REFERENCES model_versions(version),
    stock_code      VARCHAR(6) NOT NULL,
    trade_date      DATE NOT NULL,
    direction       VARCHAR(4) NOT NULL CHECK (direction IN ('BUY','SELL')),
    price           NUMERIC(10,2) NOT NULL,
    shares          INTEGER NOT NULL,
    cost            NUMERIC(10,2) DEFAULT 0,
    profit_loss     NUMERIC(10,2),
    equity_before   NUMERIC(18,2),
    equity_after    NUMERIC(18,2),
    position_before INTEGER DEFAULT 0,
    position_after  INTEGER DEFAULT 0,
    reason          VARCHAR(30),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_bt_version ON backtest_trades (version, trade_date);
"""

# ── 下载历史（v2.0 重构 迭代 3.1）──

CREATE_DOWNLOAD_HISTORY = """
CREATE TABLE IF NOT EXISTS download_history (
    id              SERIAL PRIMARY KEY,
    task_type       VARCHAR(20) NOT NULL,
    data_source     VARCHAR(20),
    start_date      DATE,
    end_date        DATE,
    status          VARCHAR(10) DEFAULT 'running',
    rows_downloaded INTEGER DEFAULT 0,
    elapsed_ms      INTEGER,
    error_message   TEXT,
    started_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at     TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dh_type_date ON download_history (task_type, started_at DESC);
"""

# ── 特征值存储（v2.0 重构 迭代 3.2）──

CREATE_FEATURE_VALUES = """
CREATE TABLE IF NOT EXISTS feature_values (
    feature_name    VARCHAR(64) NOT NULL,
    stock_code      VARCHAR(6) NOT NULL,
    trade_date      DATE NOT NULL,
    value           NUMERIC(18,6),
    calc_status     VARCHAR(10) DEFAULT 'OK',
    PRIMARY KEY (feature_name, stock_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_fv_feature_date ON feature_values (feature_name, trade_date DESC, stock_code);
CREATE INDEX IF NOT EXISTS idx_fv_stock_date ON feature_values (stock_code, trade_date);
-- 旧单列索引 idx_fv_feature_date 被上面的复合索引替代，可后续 DROP
"""

# ── DAG 流程编排（v2.0 重构 迭代 4.1）──

CREATE_DAG_FLOWS = """
CREATE TABLE IF NOT EXISTS dag_flows (
    id              SERIAL PRIMARY KEY,
    flow_name       VARCHAR(64) NOT NULL UNIQUE,
    description     TEXT,
    nodes           JSONB NOT NULL DEFAULT '[]',
    edges           JSONB NOT NULL DEFAULT '[]',
    cron_expr       VARCHAR(32),
    status          VARCHAR(16) DEFAULT 'draft',
    is_active       BOOLEAN DEFAULT false,
    last_run_at     TIMESTAMP,  -- cron 幂等触发标记（cron_scheduler 写入）
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_DAG_FLOW_VERSIONS = """
CREATE TABLE IF NOT EXISTS dag_flow_versions (
    id              SERIAL PRIMARY KEY,
    flow_id         INTEGER NOT NULL REFERENCES dag_flows(id) ON DELETE CASCADE,
    version         INTEGER NOT NULL DEFAULT 1,
    nodes           JSONB NOT NULL DEFAULT '[]',
    edges           JSONB NOT NULL DEFAULT '[]',
    change_log      TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (flow_id, version)
);
"""

# ── 实体元数据（v2.0 重构 迭代 2.5）──

CREATE_ENTITY_META = """
CREATE TABLE IF NOT EXISTS entity_meta (
    stock_code      VARCHAR(6) NOT NULL,
    stock_type      VARCHAR(10) NOT NULL DEFAULT 'stock',
    ipo_date        DATE,
    delist_date     DATE,
    listing_status  VARCHAR(16) DEFAULT 'ACTIVE',
    total_shares    BIGINT,
    PRIMARY KEY (stock_code, stock_type)
);
CREATE INDEX IF NOT EXISTS idx_em_status ON entity_meta (listing_status);
"""

# ── 特征管理（v2.0 重构 迭代 2.2）──

CREATE_FEATURES = """
CREATE TABLE IF NOT EXISTS features (
    id              BIGSERIAL PRIMARY KEY,
    feature_name    VARCHAR(64) NOT NULL UNIQUE,
    display_name    VARCHAR(64),
    target_entity   VARCHAR(16) NOT NULL,
    description     TEXT,
    formula         TEXT NOT NULL,
    depends_on      JSONB DEFAULT '[]',
    feature_group   VARCHAR(64),
    tags            JSONB DEFAULT '[]',
    status          VARCHAR(16) DEFAULT 'draft',
    total_effective_cells   BIGINT DEFAULT 0,
    missing_cells_total     BIGINT DEFAULT 0,
    abnormal_missing_cells  BIGINT DEFAULT 0,
    data_completeness       DECIMAL(5,4) DEFAULT 0,
    latest_computed_date    DATE,
    data_anomaly_reason     VARCHAR(128),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_features_entity ON features (target_entity);
CREATE INDEX IF NOT EXISTS idx_features_status ON features (status);
"""

# ── DAG 日志 + 配置 ──

CREATE_DAG_RUN_LOG = """
CREATE TABLE IF NOT EXISTS dag_run_log (
    id           SERIAL PRIMARY KEY,
    trade_date   DATE NOT NULL,
    run_id       VARCHAR(20) DEFAULT '',
    node_name    VARCHAR(50) NOT NULL,
    status       VARCHAR(10) NOT NULL DEFAULT 'success',
    rows         INTEGER DEFAULT 0,
    created_at   TIMESTAMP,           -- 日志创建时间
    started_at   TIMESTAMP,           -- 节点开始执行时间
    heartbeat_at TIMESTAMP,           -- 最近一次心跳时间
    finished_at  TIMESTAMP,           -- 节点执行完成时间
    detail       TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_drl_date ON dag_run_log (trade_date, node_name);
CREATE INDEX IF NOT EXISTS idx_drl_run ON dag_run_log (run_id);
"""

CREATE_DAG_CONFIG = """
CREATE TABLE IF NOT EXISTS dag_config (
    id           SERIAL PRIMARY KEY,
    node_name    VARCHAR(50) NOT NULL UNIQUE,
    deps         TEXT DEFAULT '',       -- 逗号分隔的依赖节点名
    label        VARCHAR(50) DEFAULT '',
    sort_order   INTEGER DEFAULT 0,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO dag_config (node_name, deps, label, sort_order) VALUES
    ('cron', '', '⏰ Corn', 0),
    ('daily_update', 'cron', '更新汇总', 1),
    ('kline', 'daily_update', 'A股日K线', 2),
    ('index', 'daily_update', '指数', 3),
    ('etf', 'daily_update', 'ETF', 4),
    ('fund', 'kline', '基本面', 5),
    ('treemap', 'fund', '树图', 6),
    ('stats', 'treemap,model_health,index,etf', '统计', 8),
    ('daily_completeness', 'stats', '日历统计', 9),
    ('model_train', '', '模型训练', 12),
    ('model_signal', 'feature_compute', '模型信号', 13),
    ('model_health', 'model_signal', '模型健康', 14),
    ('feature_compute', 'kline', '特征计算', 15),
    ('feature_backfill', '', '特征补数', 16)
ON CONFLICT (node_name) DO NOTHING;
"""

CREATE_BACKFILL_TASKS = """
CREATE TABLE IF NOT EXISTS backfill_tasks (
    task_id       VARCHAR(50) PRIMARY KEY,
    task_type     VARCHAR(20) NOT NULL,
    task_label    VARCHAR(20),
    status        VARCHAR(20) NOT NULL DEFAULT 'pending',
    start_date    DATE,
    end_date      DATE,
    force         BOOLEAN DEFAULT false,
    total_batches INTEGER DEFAULT 0,
    current_batch INTEGER DEFAULT 0,
    stocks_total  INTEGER DEFAULT 0,
    stocks_done   INTEGER DEFAULT 0,
    rows          INTEGER DEFAULT 0,
    errors        INTEGER DEFAULT 0,
    error_message TEXT,
    started_at    TIMESTAMP,
    completed_at  TIMESTAMP,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_DAILY_COMPLETENESS = """
CREATE TABLE IF NOT EXISTS daily_completeness (
    trade_date DATE PRIMARY KEY,
    stock_rows    INTEGER DEFAULT 0,
    index_rows    INTEGER DEFAULT 0,
    etf_rows      INTEGER DEFAULT 0,
    fund_rows     INTEGER DEFAULT 0,
    stock_baseline INTEGER DEFAULT 0,
    index_baseline INTEGER DEFAULT 0,
    etf_baseline   INTEGER DEFAULT 0,
    fund_baseline  INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_STATS_CACHE = """
CREATE TABLE IF NOT EXISTS data_stats_cache (
    id           SERIAL PRIMARY KEY,
    stats_json   TEXT NOT NULL,
    computed_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_SYSTEM_METRICS = """
CREATE TABLE IF NOT EXISTS system_metrics (
    id           SERIAL PRIMARY KEY,
    metric_name  VARCHAR(30) NOT NULL,
    metric_value NUMERIC NOT NULL,
    status       VARCHAR(10) NOT NULL CHECK (status IN ('ok', 'warn', 'error')),
    detail       TEXT,
    checked_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sm_name_time ON system_metrics (metric_name, checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_sm_status ON system_metrics (status, checked_at DESC);
"""

CREATE_ENTITY_STATS = """
CREATE TABLE IF NOT EXISTS entity_stats (
    entity_type  VARCHAR(10) NOT NULL PRIMARY KEY,  -- stock/index/etf/global
    total_cells  BIGINT NOT NULL DEFAULT 0,
    active_count INT NOT NULL DEFAULT 0,
    base_date    DATE NOT NULL DEFAULT '2000-01-01',
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ── 默认数据 ──

DEFAULT_STRATEGY_CONFIG = """
INSERT INTO strategy_config (strategy_name, display_name, enabled, params) VALUES
('global_preference',       '全局偏好', true, '{"mode": "balanced"}'),
-- 基础指标配置
('boll', 'BOLL',  true, '{"period": 20, "std_mult": 2.0}'),
('macd', 'MACD',  true, '{"fast": 12, "slow": 26, "signal": 9}'),
('rsi',  'RSI',   true, '{"period": 14}'),
('atr',  'ATR',   true, '{"period": 14}'),
('ma',   'MA',    true, '{"periods": [5, 20, 60, 250]}'),
('volume','量能',  true, '{"vol_ma_period": 5}')
ON CONFLICT (strategy_name) DO NOTHING;
"""

# ── 龙虎榜（tushare top_list）──
CREATE_TOP_LIST = """
CREATE TABLE IF NOT EXISTS stock_top_list (
    id SERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    stock_code VARCHAR(6) NOT NULL,
    stock_name VARCHAR(30),
    close FLOAT,
    pct_chg FLOAT,
    turnover_ratio FLOAT,
    total_amount FLOAT,
    buy_amount FLOAT,
    sell_amount FLOAT,
    net_amount FLOAT,
    reason VARCHAR(500),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tl_date ON stock_top_list (trade_date);
CREATE INDEX IF NOT EXISTS idx_tl_code ON stock_top_list (stock_code);
"""

# ── 资金流向（tushare moneyflow）──
CREATE_MONEYFLOW = """
CREATE TABLE IF NOT EXISTS stock_moneyflow (
    trade_date DATE NOT NULL,
    stock_code VARCHAR(6) NOT NULL,
    stock_name VARCHAR(30),
    buy_lg_amt FLOAT,      -- 特大单买入额
    sell_lg_amt FLOAT,     -- 特大单卖出额
    buy_md_amt FLOAT,      -- 大单买入额
    sell_md_amt FLOAT,     -- 大单卖出额
    buy_sm_amt FLOAT,      -- 中单买入额
    sell_sm_amt FLOAT,     -- 中单卖出额
    net_mf_amt FLOAT,      -- 净流入额
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (trade_date, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_mf_date ON stock_moneyflow (trade_date);
CREATE INDEX IF NOT EXISTS idx_mf_code ON stock_moneyflow (stock_code);
"""

# ── 沪深港通持股（tushare hk_hold）──
CREATE_HK_HOLD = """
CREATE TABLE IF NOT EXISTS stock_hk_hold (
    trade_date DATE NOT NULL,
    stock_code VARCHAR(6) NOT NULL,
    stock_name VARCHAR(30),
    vol INT,             -- 持股数量(股)
    amount FLOAT,        -- 持股市值(元)
    hold_ratio FLOAT,    -- 持股比例
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (trade_date, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_hh_date ON stock_hk_hold (trade_date);
CREATE INDEX IF NOT EXISTS idx_hh_code ON stock_hk_hold (stock_code);
"""

# ── 融资融券明细（tushare margin_detail）──
CREATE_MARGIN_DETAIL = """
CREATE TABLE IF NOT EXISTS stock_margin_detail (
    trade_date DATE NOT NULL,
    stock_code VARCHAR(6) NOT NULL,
    stock_name VARCHAR(30),
    fin_amount FLOAT,     -- 融资余额
    fin_buy_amount FLOAT, -- 融资买入额
    sec_amount FLOAT,     -- 融券余额
    sec_sell_amount FLOAT,-- 融券卖出额
    total_amount FLOAT,   -- 融资融券余额
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (trade_date, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_md_date ON stock_margin_detail (trade_date);
CREATE INDEX IF NOT EXISTS idx_md_code ON stock_margin_detail (stock_code);
"""

# ── 股东人数变化（tushare stk_holdernumber）──
CREATE_HOLDER_NUMBER = """
CREATE TABLE IF NOT EXISTS stock_holder_number (
    stock_code VARCHAR(6) NOT NULL,
    end_date DATE NOT NULL,
    holder_num INT,        -- 股东人数
    change_pct FLOAT,      -- 较上期变化(%)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, end_date)
);
CREATE INDEX IF NOT EXISTS idx_shn_date ON stock_holder_number (end_date);
"""

# ── tushare 当日配额（状态页展示 + 补数预算策略）──

CREATE_TUSHARE_QUOTA = """
CREATE TABLE IF NOT EXISTS tushare_quota (
    trade_date       DATE PRIMARY KEY,          -- 配额统计日（自然日）
    calls_today      INTEGER NOT NULL DEFAULT 0, -- 当日已用 API 调用次数
    calls_limit      INTEGER NOT NULL DEFAULT 8000,
    minute_calls     INTEGER NOT NULL DEFAULT 0, -- 当前分钟窗口计数
    minute_limit     INTEGER NOT NULL DEFAULT 50,
    minute_started   TIMESTAMP,                  -- 当前分钟窗口起始
    quota_exhausted  BOOLEAN NOT NULL DEFAULT false,
    last_call_at     TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ── 因子 IC 检验留档（特征页 IC 体检；决策在 features.ic_status，重算不覆盖）──

CREATE_FACTOR_IC_STATS = """
CREATE TABLE IF NOT EXISTS factor_ic_stats (
    id            BIGSERIAL PRIMARY KEY,
    feature_name  VARCHAR(64) NOT NULL,
    horizon       INT NOT NULL,               -- 前瞻天数 1/5/10/20
    val_start     DATE NOT NULL,
    val_end       DATE NOT NULL,
    sample_days   INT,                        -- 有效截面天数
    avg_names     REAL,                       -- 平均每日截面股票数
    ic_mean       DECIMAL(8,4),               -- Pearson IC
    rank_ic_mean  DECIMAL(8,4),               -- RankIC（主指标）
    ic_ir         DECIMAL(8,4),
    rank_ic_ir    DECIMAL(8,4),
    ic_win_rate   DECIMAL(5,4),               -- IC>0 占比
    t_stat        DECIMAL(8,4),
    direction     VARCHAR(4),                 -- '+' 正向 / '-' 反向因子
    traffic       VARCHAR(8),                 -- green/yellow/red（写入时按阈值计算）
    ic_series     JSONB,                      -- 日度 IC 序列（画图）
    q_returns     JSONB,                      -- 分层净值 + 多空（画图）
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (feature_name, horizon, val_start, val_end)
);
CREATE INDEX IF NOT EXISTS idx_fic_name ON factor_ic_stats (feature_name, horizon, created_at DESC);
"""

# ── 纸面组合（影子运行，v3.6）：跟随 ACTIVE 模型信号逐日模拟成交，与实盘互不干扰 ──

CREATE_PAPER_POSITIONS = """
CREATE TABLE IF NOT EXISTS paper_positions (
    stock_code    VARCHAR(6) PRIMARY KEY,
    stock_name    VARCHAR(32),
    shares        INTEGER NOT NULL,
    buy_price     DECIMAL(10,4) NOT NULL,
    cost_basis    DECIMAL(14,2) NOT NULL,
    buy_date      DATE NOT NULL,
    peak          DECIMAL(10,4),
    signal_id     INTEGER,
    model_version VARCHAR(20),
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_PAPER_TRADES = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id           BIGSERIAL PRIMARY KEY,
    trade_date   DATE NOT NULL,
    action       VARCHAR(10) NOT NULL,  -- BUY / SELL / EOD（收盘估值）
    stock_code   VARCHAR(6),
    stock_name   VARCHAR(32),
    price        DECIMAL(10,4),
    shares       INTEGER,
    amount       DECIMAL(14,2),
    commission   DECIMAL(12,2),
    pnl          DECIMAL(14,2),
    cash         DECIMAL(14,2),
    equity       DECIMAL(14,2),
    reason       VARCHAR(30),           -- signal/stop_loss/take_profit/trailing/hold_expire
    signal_id    INTEGER,
    model_version VARCHAR(20),
    detail       JSONB DEFAULT '{}',
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ppt_date ON paper_trades (trade_date, action);
"""

# ── 顺序很重要（满足外键/依赖）──

ALL_TABLES = [
    ("backfill_tasks", CREATE_BACKFILL_TASKS),
    ("trade_calendar", CREATE_TRADE_CALENDAR),
    ("stock_master", CREATE_STOCK_MASTER),
    ("daily_quote", CREATE_DAILY_QUOTE),
    ("index_daily_quote", CREATE_INDEX_DAILY_QUOTE),
    ("corporate_actions", CREATE_CORPORATE_ACTIONS),
    ("portfolio", CREATE_PORTFOLIO),
    ("portfolio_history", CREATE_PORTFOLIO_HISTORY),
    ("signal_history", CREATE_SIGNAL_HISTORY),
    ("strategy_config", CREATE_STRATEGY_CONFIG),
    ("strategy_param_log", CREATE_STRATEGY_PARAM_LOG),
    ("stock_industry", CREATE_STOCK_INDUSTRY),
    ("failed_downloads", CREATE_FAILED_DOWNLOADS),
    ("stock_fundamentals", CREATE_STOCK_FUNDAMENTALS),
    ("stock_fundamentals_history", CREATE_FUNDAMENTALS_HISTORY),
    ("stock_treemap_cache", CREATE_TREEMAP_CACHE),
    ("daily_completeness", CREATE_DAILY_COMPLETENESS),
    ("data_stats_cache", CREATE_STATS_CACHE),
    ("dag_run_log", CREATE_DAG_RUN_LOG),
    ("dag_config", CREATE_DAG_CONFIG),
    ("system_metrics", CREATE_SYSTEM_METRICS),
    ("model_versions", CREATE_MODEL_VERSIONS),
    ("training_trials", CREATE_MODEL_TRIALS),
    ("version_comparisons", CREATE_MODEL_COMPARISONS),
    ("model_health", CREATE_MODEL_HEALTH),
    ("features", CREATE_FEATURES),
    ("factor_ic_stats", CREATE_FACTOR_IC_STATS),
    ("paper_positions", CREATE_PAPER_POSITIONS),
    ("paper_trades", CREATE_PAPER_TRADES),
    ("backtest_records", CREATE_BACKTEST_RECORDS),
    ("backtest_trades", CREATE_BACKTEST_TRADES),
    ("download_history", CREATE_DOWNLOAD_HISTORY),
    ("entity_meta", CREATE_ENTITY_META),
    ("feature_values", CREATE_FEATURE_VALUES),
    ("dag_flows", CREATE_DAG_FLOWS),
    ("dag_flow_versions", CREATE_DAG_FLOW_VERSIONS),
    ("entity_stats", CREATE_ENTITY_STATS),
    ("stock_top_list", CREATE_TOP_LIST),
    ("stock_moneyflow", CREATE_MONEYFLOW),
    ("stock_hk_hold", CREATE_HK_HOLD),
    ("stock_margin_detail", CREATE_MARGIN_DETAIL),
    ("stock_holder_number", CREATE_HOLDER_NUMBER),
    ("tushare_quota", CREATE_TUSHARE_QUOTA),
]


def init_db(sync_session) -> None:
    """初始化数据库：建表 + 默认数据。幂等，可重复执行。"""
    # DDL 锁超时：避免长事务（如特征计算）持锁时启动被无限阻塞；超时后本会话失败可重试
    try:
        sync_session.execute(text("SET lock_timeout = '15s'"))
    except Exception:
        pass
    for name, sql in ALL_TABLES:
        for stmt in sql.strip().split(";"):
            stmt = stmt.strip()
            if stmt and not stmt.startswith("--"):
                try:
                    sync_session.execute(text(stmt))
                    sync_session.commit()
                except Exception as e:
                    # 已存在类错误可继续；其余失败需 rollback 后再判断，
                    # 否则事务进入 aborted 状态导致后续所有语句报错
                    sync_session.rollback()
                    if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower() and "relation" not in str(e).lower():
                        raise

    # 默认策略配置
    sync_session.execute(text(DEFAULT_STRATEGY_CONFIG))

    # 迁移：删除旧 strategy 节点（已从 NODE_FN_MAP 移除）
    try:
        sync_session.execute(text("DELETE FROM dag_config WHERE node_name='strategy'"))
    except Exception:
        sync_session.rollback()

    # 迁移：更新 stats 节点依赖（去掉 strategy）
    try:
        sync_session.execute(text("UPDATE dag_config SET deps='treemap,model_health,index,etf' WHERE node_name='stats' AND deps LIKE '%strategy%'"))
    except Exception:
        sync_session.rollback()

    # 迁移：已有库补充基础指标配置（幂等）
    indicators = [
        ('boll', 'BOLL', True, '{"period": 20, "std_mult": 2.0}'),
        ('macd', 'MACD', True, '{"fast": 12, "slow": 26, "signal": 9}'),
        ('rsi', 'RSI', True, '{"period": 14}'),
        ('atr', 'ATR', True, '{"period": 14}'),
        ('ma', 'MA', True, '{"periods": [5, 20, 60, 250]}'),
        ('volume', '量能', True, '{"vol_ma_period": 5}'),
    ]
    for name, display, enabled, params in indicators:
        try:
            sync_session.execute(text("INSERT INTO strategy_config (strategy_name, display_name, enabled, params) VALUES (:n,:d,:e,:p) ON CONFLICT (strategy_name) DO NOTHING"),
                                {"n": name, "d": display, "e": enabled, "p": params})
        except Exception:
            sync_session.rollback()
    sync_session.commit()

    # 迁移：signal_history 新增模型训练列（幂等）
    for col, col_type in [
        ('ml_confidence', 'DECIMAL(5,3)'),
        ('forward_5d_return', 'DECIMAL(8,4)'),
        ('forward_10d_return', 'DECIMAL(8,4)'),
        ('forward_20d_return', 'DECIMAL(8,4)'),
        ('actual_return', 'DECIMAL(8,4)'),
        ('close_reason', 'VARCHAR(30)'),
        ('closed_at', 'TIMESTAMP'),
        ('model_version', 'VARCHAR(20)'),
    ]:
        try:
            sync_session.execute(text(f"ALTER TABLE signal_history ADD COLUMN IF NOT EXISTS {col} {col_type}"))
        except Exception:
            sync_session.rollback()
    # 迁移：signal_history.model_version 索引
    try:
        sync_session.execute(text("CREATE INDEX IF NOT EXISTS idx_sh_model_version ON signal_history (model_version)"))
    except Exception:
        sync_session.rollback()

    # 迁移：model_versions 新增 deleted_at 列（幂等）
    try:
        sync_session.execute(text("ALTER TABLE model_versions ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP"))
    except Exception:
        sync_session.rollback()

    # 迁移：signal_history 新增预测模型列（v2.6+）
    for col, col_type in [
        ('predict_5d_return', 'DECIMAL(8,4)'),
        ('predict_10d_return', 'DECIMAL(8,4)'),
        ('predict_20d_return', 'DECIMAL(8,4)'),
        ('predict_score', 'DECIMAL(8,4)'),
    ]:
        try:
            sync_session.execute(text(f"ALTER TABLE signal_history ADD COLUMN IF NOT EXISTS {col} {col_type}"))
        except Exception:
            sync_session.rollback()

    # 迁移：daily_completeness 新增 baseline 列
    for col in ['stock_baseline', 'index_baseline', 'etf_baseline', 'fund_baseline']:
        try:
            sync_session.execute(text(f"ALTER TABLE daily_completeness ADD COLUMN IF NOT EXISTS {col} INTEGER DEFAULT 0"))
        except Exception:
            sync_session.rollback()

    # 迁移：entity_stats 表 + dag_config 节点（v2.8 entity_stats 增量）
    try:
        sync_session.execute(text("""
            INSERT INTO entity_stats (entity_type, total_cells, active_count, base_date)
            VALUES
                ('stock',  0, 0, '2000-01-01'),
                ('index',  0, 0, '2000-01-01'),
                ('etf',    0, 0, '2000-01-01'),
                ('global', 0, 0, '2000-01-01')
            ON CONFLICT (entity_type) DO NOTHING
        """))
        sync_session.execute(text("""
            INSERT INTO dag_config (node_name, deps, label, sort_order)
            VALUES ('entity_stats', 'kline', '实体统计', 55)
            ON CONFLICT (node_name) DO NOTHING
        """))
        sync_session.commit()
    except Exception:
        sync_session.rollback()

    # 迁移：特征 IC 检验（v3.4）——features 决策列 + dag_config 批量节点
    try:
        sync_session.execute(text("ALTER TABLE factor_ic_stats ADD COLUMN IF NOT EXISTS traffic VARCHAR(8)"))
        sync_session.commit()
    except Exception:
        sync_session.rollback()
    for col, col_type in [
        ('ic_status', "VARCHAR(16) DEFAULT 'candidate'"),  # candidate/included/excluded（用户决策，重算不覆盖）
        ('ic_decided_at', 'TIMESTAMP'),
    ]:
        try:
            sync_session.execute(text(f"ALTER TABLE features ADD COLUMN IF NOT EXISTS {col} {col_type}"))
        except Exception:
            sync_session.rollback()
    try:
        sync_session.execute(text("""
            INSERT INTO dag_config (node_name, deps, label, sort_order)
            VALUES ('factor_ic', 'stats', '因子IC体检', 56)
            ON CONFLICT (node_name) DO NOTHING
        """))
        sync_session.execute(text("""
            INSERT INTO dag_config (node_name, deps, label, sort_order)
            VALUES ('paper_portfolio', 'model_signal', '纸面组合', 57)
            ON CONFLICT (node_name) DO NOTHING
        """))
        sync_session.execute(text("""
            UPDATE features SET ic_status = 'candidate' WHERE ic_status IS NULL
        """))
        sync_session.commit()
    except Exception:
        sync_session.rollback()

    # 迁移：function_versions 表（v2.0 重构）
    try:
        sync_session.execute(text("""
            CREATE TABLE IF NOT EXISTS function_versions (
                id            SERIAL PRIMARY KEY,
                function_id   INTEGER NOT NULL REFERENCES functions(id) ON DELETE CASCADE,
                version       INTEGER NOT NULL DEFAULT 1,
                source_code   TEXT NOT NULL,
                parameters    JSONB DEFAULT '[]',
                change_log    TEXT,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        sync_session.commit()
    except Exception:
        sync_session.rollback()

    # 迁移：functions 表（v2.0 重构）
    try:
        sync_session.execute(text("""
            CREATE TABLE IF NOT EXISTS functions (
                id              SERIAL PRIMARY KEY,
                name            VARCHAR(64) NOT NULL UNIQUE,
                display_name    VARCHAR(30),
                description     TEXT,
                category        VARCHAR(20) DEFAULT 'other',
                parameters      JSONB NOT NULL DEFAULT '[]',
                source_code     TEXT NOT NULL,
                lookback        INTEGER DEFAULT 5,
                dependencies    JSONB DEFAULT '[]',
                is_builtin      BOOLEAN DEFAULT false,
                status          VARCHAR(16) DEFAULT 'draft',
                version         INTEGER DEFAULT 1,
                avg_runtime_ms  DOUBLE PRECISION,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        sync_session.commit()
    except Exception:
        sync_session.rollback()

    # 预置系统内置函数
    BUILTINS = [
        ('ma', '移动平均', '计算N周期简单移动平均', 'time_series', '[{"name":"field","type":"field","desc":"价格字段"},{"name":"window","type":"numeric","default":5,"desc":"周期"}]',
         'def ma(df, window=5):\n    return df.rolling(window).mean()', 5, True),
        ('ema', '指数移动平均', '计算N周期指数移动平均', 'time_series', '[{"name":"field","type":"field","desc":"价格字段"},{"name":"window","type":"numeric","default":12,"desc":"周期"}]',
         'def ema(df, window=12):\n    return df.ewm(span=window).mean()', 12, True),
        ('rsi', '相对强弱', '计算N周期RSI指标', 'time_series', '[{"name":"field","type":"field","desc":"价格字段"},{"name":"window","type":"numeric","default":14,"desc":"周期"}]',
         'def rsi(df, window=14):\n    d=df.diff();u=d.clip(lower=0);l=-d.clip(upper=0);rs=u.rolling(window).mean()/l.rolling(window).mean();return 100-100/(1+rs)', 14, True),
        ('std', '标准差', '计算N周期标准差', 'statistical', '[{"name":"field","type":"field","desc":"字段"},{"name":"window","type":"numeric","default":20,"desc":"周期"}]',
         'def std(df, window=20):\n    return df.rolling(window).std()', 20, True),
        ('avg', '全市场均值', '全市场某字段均值，默认排除自身', 'cross_sectional',
         '[{"name":"field","type":"series","desc":"数据源字段如stock.close"},{"name":"exclude_self","type":"scalar","default":"True","desc":"是否排除当前行"}]',
         'def avg(df, exclude_self=True):\n    return df.mean()', 1, True),
        ('rank', '排名百分位', '全市场排名，pct=True返回百分位(0~1)', 'cross_sectional',
         '[{"name":"field","type":"series","desc":"数据源字段"},{"name":"pct","type":"scalar","default":"True","desc":"是否返回百分位"},{"name":"exclude_self","type":"scalar","default":"True","desc":"是否排除当前行"}]',
         'def rank(df, pct=True, exclude_self=True):\n    return df.rank(pct=pct)', 1, True),
        ('sum', '全市场求和', '全市场某字段总和,排除自身', 'cross_sectional',
         '[{"name":"field","type":"series","desc":"数据源字段如stock.volume"},{"name":"exclude_self","type":"scalar","default":"True","desc":"是否排除当前行"}]',
         'def sum_func(df, exclude_self=True):\n    return df.sum()', 1, True),
        ('cs_max', '全市场最大值', '全市场当日某字段最大值', 'cross_sectional',
         '[{"name":"field","type":"series","desc":"数据源字段如stock.high"}]',
         'def cs_max(df):\n    return df.max()', 1, True),
        ('cs_min', '全市场最小值', '全市场当日某字段最小值', 'cross_sectional',
         '[{"name":"field","type":"series","desc":"数据源字段如stock.low"}]',
         'def cs_min(df):\n    return df.min()', 1, True),
        ('quantile', '分位数', '全市场某字段的Q分位数值', 'cross_sectional',
         '[{"name":"field","type":"series","desc":"数据源字段如stock.pe"},{"name":"q","type":"scalar","default":"0.8","desc":"分位数(0~1)"}]',
         'def quantile(df, q=0.8):\n    return df.quantile(q)', 1, True),
        ('dif', 'MACD快线', '计算MACD的DIF线（12日EMA - 26日EMA）', 'time_series',
         '[{"name":"field","type":"field","desc":"价格字段"},{"name":"fast","type":"numeric","default":12,"desc":"快线周期"},{"name":"slow","type":"numeric","default":26,"desc":"慢线周期"}]',
         'def dif(df, fast=12, slow=26):\n    return df.ewm(span=fast).mean() - df.ewm(span=slow).mean()', 26, True),
        ('dea', 'MACD信号线', '计算MACD的DEA线（DIF的9日EMA）', 'time_series',
         '[{"name":"field","type":"field","desc":"价格字段"},{"name":"fast","type":"numeric","default":12,"desc":"快线周期"},{"name":"slow","type":"numeric","default":26,"desc":"慢线周期"},{"name":"signal","type":"numeric","default":9,"desc":"信号线周期"}]',
         'def dea(df, fast=12, slow=26, signal=9):\n    d = df.ewm(span=fast).mean() - df.ewm(span=slow).mean()\n    return d.ewm(span=signal).mean()', 35, True),
        ('macd_hist', 'MACD柱', '计算MACD柱状线（DIF - DEA）', 'time_series',
         '[{"name":"field","type":"field","desc":"价格字段"},{"name":"fast","type":"numeric","default":12,"desc":"快线周期"},{"name":"slow","type":"numeric","default":26,"desc":"慢线周期"},{"name":"signal","type":"numeric","default":9,"desc":"信号线周期"}]',
         'def macd_hist(df, fast=12, slow=26, signal=9):\n    d = df.ewm(span=fast).mean() - df.ewm(span=slow).mean()\n    de = d.ewm(span=signal).mean()\n    return d - de', 35, True),
        ('boll_upper', '布林上轨', '计算布林带上轨（MA + K×σ）', 'time_series',
         '[{"name":"field","type":"field","desc":"价格字段"},{"name":"window","type":"numeric","default":20,"desc":"均线周期"},{"name":"k","type":"numeric","default":2,"desc":"标准差倍数"}]',
         'def boll_upper(df, window=20, k=2):\n    return df.rolling(window).mean() + k * df.rolling(window).std()', 20, True),
        ('boll_mid', '布林中轨', '计算布林带中轨（MA）', 'time_series',
         '[{"name":"field","type":"field","desc":"价格字段"},{"name":"window","type":"numeric","default":20,"desc":"均线周期"}]',
         'def boll_mid(df, window=20):\n    return df.rolling(window).mean()', 20, True),
        ('boll_lower', '布林下轨', '计算布林带下轨（MA - K×σ）', 'time_series',
         '[{"name":"field","type":"field","desc":"价格字段"},{"name":"window","type":"numeric","default":20,"desc":"均线周期"},{"name":"k","type":"numeric","default":2,"desc":"标准差倍数"}]',
         'def boll_lower(df, window=20, k=2):\n    return df.rolling(window).mean() - k * df.rolling(window).std()', 20, True),
    ]
    for name, dname, desc, cat, params, code, lookback, builtin in BUILTINS:
        try:
            import json as _j
            sync_session.execute(text(
                "INSERT INTO functions (name,display_name,description,category,parameters,source_code,lookback,is_builtin,status) "
                "VALUES (:n,:d,:desc,:c,:p,:s,:l,:b,'published') ON CONFLICT (name) DO NOTHING"
            ), {"n":name,"d":dname,"desc":desc,"c":cat,"p":_j.dumps(eval(params)),"s":code,"l":lookback,"b":builtin})
        except Exception:
            sync_session.rollback()
    sync_session.commit()

    # 预置系统特征 — 个股均线（迭代 2.2 优化）
    SYS_FEATURES = [
        # ── 均线 (feature_group: ma) ──
        ('ma_5',   '5日均线',   '个股5日简单移动平均',    'ma(close, 5)',   '["ma","close"]',   'ma', '["均线","趋势"]'),
        ('ma_10',  '10日均线',  '个股10日简单移动平均',   'ma(close, 10)',  '["ma","close"]',   'ma', '["均线","趋势"]'),
        ('ma_20',  '20日均线',  '个股20日简单移动平均',   'ma(close, 20)',  '["ma","close"]',   'ma', '["均线","趋势"]'),
        ('ma_30',  '30日均线',  '个股30日简单移动平均',   'ma(close, 30)',  '["ma","close"]',   'ma', '["均线","趋势"]'),
        ('ma_60',  '60日均线',  '个股60日简单移动平均',   'ma(close, 60)',  '["ma","close"]',   'ma', '["均线","趋势"]'),
        ('ma_90',  '90日均线',  '个股90日简单移动平均',   'ma(close, 90)',  '["ma","close"]',   'ma', '["均线","趋势"]'),
        ('ma_120', '120日均线', '个股120日简单移动平均',  'ma(close, 120)', '["ma","close"]',   'ma', '["均线","趋势"]'),
        ('ma_180', '180日均线', '个股180日简单移动平均',  'ma(close, 180)', '["ma","close"]',   'ma', '["均线","趋势"]'),
        # ── 指数均线 (feature_group: ema) ──
        ('ema_12', '12日EMA',  '个股12日指数移动平均', 'ema(close, 12)', '["ema","close"]', 'ema', '["均线","趋势"]'),
        ('ema_26', '26日EMA',  '个股26日指数移动平均', 'ema(close, 26)', '["ema","close"]', 'ema', '["均线","趋势"]'),
        # ── MACD (feature_group: macd) ──
        ('dif',       'MACD快线',   'MACD快慢线差值',  'dif(close)',      '["dif","close"]',       'macd', '["MACD","动量"]'),
        ('dea',       'MACD信号线', 'DIF的9日EMA',     'dea(close)',      '["dea","close"]',       'macd', '["MACD","动量"]'),
        ('macd_hist', 'MACD柱',     'MACD柱状线',      'macd_hist(close)','["macd_hist","close"]', 'macd', '["MACD","动量"]'),
        # ── RSI ──
        ('rsi_14', '14日RSI', '个股14日相对强弱', 'rsi(close, 14)', '["rsi","close"]', 'oscillator', '["震荡","超买超卖"]'),
        # ── 布林带 (feature_group: boll) ──
        ('boll_upper', '布林上轨', '布林带上轨', 'boll_upper(close)', '["boll_upper","close"]', 'boll', '["布林","波动"]'),
        ('boll_mid',   '布林中轨', '布林带中轨', 'boll_mid(close)',   '["boll_mid","close"]',   'boll', '["布林","波动"]'),
        ('boll_lower', '布林下轨', '布林带下轨', 'boll_lower(close)', '["boll_lower","close"]', 'boll', '["布林","波动"]'),
        # ── ATR ──
        ('atr_14', '14日ATR', '个股14日平均真实波幅', 'atr(close, 14)', '["atr","close"]', 'volatility', '["波动"]'),
        # ── 涨跌幅 ──
        ('pct_1d',  '1日涨跌幅',  '个股1日涨跌幅',  'pct_change(close, 1)',  '["pct_change","close"]', 'momentum', '["动量","涨跌"]'),
        ('pct_5d',  '5日涨跌幅',  '个股5日涨跌幅',  'pct_change(close, 5)',  '["pct_change","close"]', 'momentum', '["动量","涨跌"]'),
        ('pct_20d', '20日涨跌幅', '个股20日涨跌幅', 'pct_change(close, 20)', '["pct_change","close"]', 'momentum', '["动量","涨跌"]'),
        # ── 乖离率 ──
        ('bias_5',  '5日乖离率',  '收盘价相对5日均线偏离度',  '(close - ma(close,5)) / ma(close,5)',  '["ma","close"]', 'bias', '["乖离","动量"]'),
        ('bias_20', '20日乖离率', '收盘价相对20日均线偏离度', '(close - ma(close,20)) / ma(close,20)', '["ma","close"]', 'bias', '["乖离","动量"]'),
    ]
    for fn, dn, desc, formula, deps, fg, tags in SYS_FEATURES:
        try:
            sync_session.execute(text(
                "INSERT INTO features (feature_name, display_name, target_entity, description, formula, depends_on, feature_group, tags, status) "
                "VALUES (:fn, :dn, 'stock', :desc, :formula, :deps, :fg, :tags, 'enabled') "
                "ON CONFLICT (feature_name) DO NOTHING"
            ), {"fn": fn, "dn": dn, "desc": desc, "formula": formula, "deps": deps, "fg": fg, "tags": tags})
        except Exception:
            sync_session.rollback()
    sync_session.commit()

    # 迁移：features 表新增 feature_group / tags 列（v2.4）
    for col, col_type in [('feature_group', 'VARCHAR(64)'), ('tags', 'JSONB DEFAULT \'[]\'')]:
        try:
            sync_session.execute(text(f"ALTER TABLE features ADD COLUMN IF NOT EXISTS {col} {col_type}"))
        except Exception:
            sync_session.rollback()

    # 迁移：abnormal_missing_cells 列（v2.5）
    try:
        sync_session.execute(text("ALTER TABLE features ADD COLUMN IF NOT EXISTS abnormal_missing_cells BIGINT DEFAULT 0"))
    except Exception:
        sync_session.rollback()

    # 迁移：features 表新增 actual_row_count 列（v2.8 — 数据预览优化）
    try:
        sync_session.execute(text("ALTER TABLE features ADD COLUMN IF NOT EXISTS actual_row_count BIGINT DEFAULT 0"))
    except Exception:
        sync_session.rollback()

    # 迁移：model_health 记 IC（v3.6——ACTIVE 模型滚动 RankIC 衰减监控）
    for col, col_type in [
        ('rank_ic', 'DECIMAL(8,4)'),
        ('rank_icir', 'DECIMAL(8,4)'),
    ]:
        try:
            sync_session.execute(text(f"ALTER TABLE model_health ADD COLUMN IF NOT EXISTS {col} {col_type}"))
        except Exception:
            sync_session.rollback()

    # 迁移：model_versions 新增策略优化字段（v2.7）
    for col, col_type in [
        ('train_params', 'JSONB'),
        ('trading_rules', 'JSONB'),
        ('strategy_scan_results', 'JSONB'),
        ('test_performance', 'JSONB'),
        ('perm_test', 'JSONB'),  # 置换检验留档（v3.5：real vs 打乱分布 vs random）
    ]:
        try:
            sync_session.execute(text(f"ALTER TABLE model_versions ADD COLUMN IF NOT EXISTS {col} {col_type}"))
        except Exception:
            sync_session.rollback()

    # 迁移：strategy_scan_tasks 表（v2.7）
    try:
        sync_session.execute(text("""
            CREATE TABLE IF NOT EXISTS strategy_scan_tasks (
                task_id       VARCHAR(16) PRIMARY KEY,
                version       VARCHAR(16),
                status        VARCHAR(16) DEFAULT 'pending',
                total_combos  INTEGER DEFAULT 0,
                completed     INTEGER DEFAULT 0,
                best_params   JSONB,
                best_sharpe   DECIMAL(8,4),
                results       JSONB,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at   TIMESTAMP
            )
        """))
        sync_session.commit()
    except Exception:
        sync_session.rollback()

    try:
        sync_session.execute(text("DELETE FROM dag_config WHERE node_name IN ('indicator_incr','indicator_full')"))
        sync_session.commit()
    except Exception:
        sync_session.rollback()

    # 回填已有系统特征的 feature_group / tags
    import json as _j
    BACKFILL_FG = {
        'ma_5':'ma','ma_10':'ma','ma_20':'ma','ma_30':'ma','ma_60':'ma','ma_90':'ma','ma_120':'ma','ma_180':'ma',
        'ema_12':'ema','ema_26':'ema',
        'dif':'macd','dea':'macd','macd_hist':'macd',
        'rsi_14':'oscillator',
        'boll_upper':'boll','boll_mid':'boll','boll_lower':'boll',
        'atr_14':'volatility',
        'pct_1d':'momentum','pct_5d':'momentum','pct_20d':'momentum',
        'bias_5':'bias','bias_20':'bias',
    }
    BACKFILL_TAGS = {
        'ma':['均线','趋势'], 'ema':['均线','趋势'], 'macd':['MACD','动量'],
        'oscillator':['震荡','超买超卖'], 'boll':['布林','波动'], 'volatility':['波动'],
        'momentum':['动量','涨跌'], 'bias':['乖离','动量'],
    }
    for fn, fg in BACKFILL_FG.items():
        tags = BACKFILL_TAGS.get(fg, [])
        try:
            sync_session.execute(text(
                "UPDATE features SET feature_group=:fg, tags=:tags WHERE feature_name=:fn AND feature_group IS NULL"
            ), {"fn": fn, "fg": fg, "tags": _j.dumps(tags)})
        except Exception:
            sync_session.rollback()
    sync_session.commit()

    # 迁移：daily_quote / index_daily_quote 新增 is_suspended 列
    for table_name in ['daily_quote', 'index_daily_quote']:
        try:
            sync_session.execute(text(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS is_suspended BOOLEAN DEFAULT false"))
        except Exception:
            sync_session.rollback()

    # 迁移：stock_master PK 改为复合主键 (stock_code, stock_type)
    try:
        # 检查当前 PK 是否只有 stock_code（旧方案）
        has_old_pk = sync_session.execute(text("""
            SELECT COUNT(*) FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name
            WHERE tc.table_name='stock_master' AND tc.constraint_type='PRIMARY KEY'
            AND kcu.column_name='stock_code'
            AND NOT EXISTS (
                SELECT 1 FROM information_schema.key_column_usage k2
                WHERE k2.constraint_name=tc.constraint_name AND k2.column_name='stock_type'
            )
        """)).scalar()
        if has_old_pk and has_old_pk > 0:
            sync_session.execute(text("ALTER TABLE stock_master DROP CONSTRAINT stock_master_pkey"))
            sync_session.execute(text("UPDATE stock_master SET stock_type='stock' WHERE stock_type='' OR stock_type IS NULL"))
            sync_session.execute(text("ALTER TABLE stock_master ADD PRIMARY KEY (stock_code, stock_type)"))
    except Exception:
        sync_session.rollback()
    sync_session.commit()

    # 迁移：stock_master 扩展字段（tushare 适配器 v3.1.2）
    for col, col_type in [("delist_date", "DATE"), ("is_hs", "VARCHAR(1)"),
                          ("act_name", "VARCHAR(100)"), ("area", "VARCHAR(20)"),
                          ("reg_capital", "NUMERIC"), ("employees", "INTEGER"),
                          ("main_business", "VARCHAR(200)"),
                          ("industry", "VARCHAR(50)"),
                          # 申万行业层级（v3.2.1，tushare index_member_all）
                          ("industry_l1", "VARCHAR(50)"), ("industry_l2", "VARCHAR(50)")]:
        try:
            sync_session.execute(text(f"ALTER TABLE stock_master ADD COLUMN IF NOT EXISTS {col} {col_type}"))
        except Exception:
            sync_session.rollback()
    sync_session.commit()

    # 迁移：stock_fundamentals → 双主键 + 扩展字段（v3.2 重构）
    # 先加列
    for col, col_type in [("ps", "NUMERIC(10,2)"),
                          ("ps_ttm", "NUMERIC(10,2)"), ("dv_ratio", "NUMERIC(10,4)"),
                          ("dv_ttm", "NUMERIC(10,4)"), ("turnover_rate", "NUMERIC(10,4)"),
                          ("volume_ratio", "NUMERIC(10,4)"), ("free_share", "BIGINT"),
                          ("circ_mv", "BIGINT"), ("pe", "NUMERIC(10,2)"),
                          ("limit_status", "INTEGER")]:
        try:
            sync_session.execute(text(f"ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS {col} {col_type}"))
        except Exception:
            sync_session.rollback()
    sync_session.commit()

    # 迁移：stock_fundamentals 扩展字段（daily_basic v3.2）
    for col, col_type in [("ps_ttm", "NUMERIC(10,2)"), ("dv_ttm", "NUMERIC(10,2)"),
                          ("float_share", "BIGINT"), ("circ_mv", "BIGINT")]:
        try:
            sync_session.execute(text(f"ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS {col} {col_type}"))
        except Exception:
            sync_session.rollback()
    sync_session.commit()

    # 交易日历：如果为空则从 baostock 同步真实日历（含法定节假日）
    cnt = sync_session.execute(text("SELECT COUNT(*) FROM trade_calendar")).scalar() or 0
    if cnt == 0:
        try:
            from crawler.trade_calendar import sync_from_baostock
            sync_from_baostock(sync_session, start_year=2020, end_year=2030)
            sync_session.commit()
        except Exception as e:
            sync_session.rollback()
            import logging
            logging.getLogger("loguru").warning(f"[init_db] 交易日历同步失败（降级为空日历）: {e}")
    else:
        sync_session.commit()

    # 迁移：signal_history 补 status 列（v3.3 信号了结状态，此前缺失导致 /signal/stats 500）
    try:
        sync_session.execute(text("ALTER TABLE signal_history ADD COLUMN IF NOT EXISTS status VARCHAR(10) DEFAULT NULL"))
        sync_session.execute(text("CREATE INDEX IF NOT EXISTS idx_sh_status ON signal_history (status, signal_date)"))
        sync_session.commit()
    except Exception:
        sync_session.rollback()

    # 迁移：model_versions 补 feature_list / stage 列（策略扫描/归因/质量看板依赖）
    for col, col_type in [("feature_list", "JSONB"), ("stage", "VARCHAR(20) DEFAULT 'pending'")]:
        try:
            sync_session.execute(text(f"ALTER TABLE model_versions ADD COLUMN IF NOT EXISTS {col} {col_type}"))
        except Exception:
            sync_session.rollback()
    sync_session.commit()

    # 迁移：dag_flows 补 last_run_at 列（cron 幂等触发）
    try:
        sync_session.execute(text("ALTER TABLE dag_flows ADD COLUMN IF NOT EXISTS last_run_at TIMESTAMP"))
        sync_session.commit()
    except Exception:
        sync_session.rollback()

    # 迁移：stock_fundamentals 对齐新 schema（旧库为单列 PK + 无 trade_date）
    # 1) 补 trade_date 列并回填旧数据
    try:
        sync_session.execute(text("ALTER TABLE stock_fundamentals ADD COLUMN IF NOT EXISTS trade_date DATE"))
        sync_session.commit()
    except Exception:
        sync_session.rollback()
    try:
        sync_session.execute(text(
            "UPDATE stock_fundamentals SET trade_date = COALESCE(trade_date, updated_at::date, CURRENT_DATE) WHERE trade_date IS NULL"
        ))
        sync_session.commit()
    except Exception:
        sync_session.rollback()

    # 迁移：删除 stock_fundamentals 冗余列（stock_name/industry 权威在 stock_master；trade_date_v2 已被 trade_date 取代）
    try:
        for col in ("stock_name", "industry", "trade_date_v2"):
            sync_session.execute(text(f"ALTER TABLE stock_fundamentals DROP COLUMN IF EXISTS {col}"))
        sync_session.commit()
    except Exception:
        sync_session.rollback()
    # 2) 主键 = 单列 stock_code（快照模型，每股票一条最新）
    #    若现存双列主键（v3.2 曾升级为 code+trade_date 累积了多交易日历史行）
    #    → 清理重复（保留每股票最新 trade_date）→ DROP 双列 PK → ADD 单列 PK
    try:
        pk_name = sync_session.execute(text(
            "SELECT conname FROM pg_constraint WHERE conrelid='stock_fundamentals'::regclass AND contype='p'"
        )).scalar()
        pk_cols = sync_session.execute(text(
            "SELECT array_agg(a.attname ORDER BY array_position(c.conkey, a.attnum)) "
            "FROM pg_constraint c JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum = ANY(c.conkey) "
            "WHERE c.conrelid='stock_fundamentals'::regclass AND c.contype='p' GROUP BY c.conname"
        )).scalar()
        if pk_name and pk_cols and pk_cols == ['stock_code', 'trade_date']:
            # 清理历史累积：每股票只保留最新 trade_date 一行
            sync_session.execute(text("""
                DELETE FROM stock_fundamentals WHERE (stock_code, trade_date) IN (
                    SELECT stock_code, trade_date FROM (
                        SELECT stock_code, trade_date,
                               ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY trade_date DESC) AS rn
                        FROM stock_fundamentals
                    ) t WHERE rn > 1
                )
            """))
            sync_session.execute(text(f"ALTER TABLE stock_fundamentals DROP CONSTRAINT {pk_name}"))
            sync_session.execute(text("ALTER TABLE stock_fundamentals ADD PRIMARY KEY (stock_code)"))
            sync_session.commit()
    except Exception:
        sync_session.rollback()
