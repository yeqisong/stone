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
    index_code VARCHAR(8) NOT NULL,
    index_name VARCHAR(20) NOT NULL,
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
    stock_code    VARCHAR(6) PRIMARY KEY,
    stock_name    VARCHAR(20),
    industry      VARCHAR(50),       -- 行业(证监会分类)
    pe_ttm        NUMERIC(10,2),     -- 市盈率(TTM)
    pb_mrq        NUMERIC(10,2),     -- 市净率(MRQ)
    roe           NUMERIC(10,2),     -- ROE(%)
    revenue_yoy   NUMERIC(10,2),     -- 营收同比(%)
    profit_yoy    NUMERIC(10,2),     -- 净利同比(%)
    total_shares  BIGINT,            -- 总股本(股)
    market_cap    BIGINT,            -- 总市值(元)
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

# ── 模型训练：共享指标池（6张窄表，按指标类型分表） ──

CREATE_INDICATORS_BOLL = """
CREATE TABLE IF NOT EXISTS stock_indicators_boll (
    stock_code VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    upper      DECIMAL(12,4),
    mid        DECIMAL(12,4),
    lower      DECIMAL(12,4),
    pct_b      DECIMAL(8,4),
    width      DECIMAL(8,4),
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (stock_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_boll_date ON stock_indicators_boll (trade_date);
"""

CREATE_INDICATORS_MACD = """
CREATE TABLE IF NOT EXISTS stock_indicators_macd (
    stock_code VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    dif        DECIMAL(12,4),
    dea        DECIMAL(12,4),
    hist       DECIMAL(12,4),
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (stock_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_macd_date ON stock_indicators_macd (trade_date);
"""

CREATE_INDICATORS_RSI = """
CREATE TABLE IF NOT EXISTS stock_indicators_rsi (
    stock_code VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    rsi        DECIMAL(8,4),
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (stock_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_rsi_date ON stock_indicators_rsi (trade_date);
"""

CREATE_INDICATORS_ATR = """
CREATE TABLE IF NOT EXISTS stock_indicators_atr (
    stock_code VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    atr        DECIMAL(12,4),
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (stock_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_atr_date ON stock_indicators_atr (trade_date);
"""

CREATE_INDICATORS_MA = """
CREATE TABLE IF NOT EXISTS stock_indicators_ma (
    stock_code VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    ma5        DECIMAL(12,4),
    ma20       DECIMAL(12,4),
    ma60       DECIMAL(12,4),
    ma250      DECIMAL(12,4),
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (stock_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_ma_date ON stock_indicators_ma (trade_date);
"""

CREATE_INDICATORS_VOLUME = """
CREATE TABLE IF NOT EXISTS stock_indicators_volume (
    stock_code VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    vol_ma5    DECIMAL(18,4),
    vol_ratio  DECIMAL(8,4),
    obv        DECIMAL(18,4),
    obv_ma5    DECIMAL(18,4),
    obv_ma10   DECIMAL(18,4),
    created_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (stock_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_volume_date ON stock_indicators_volume (trade_date);
"""

CREATE_INDICATOR_CALC_LOG = """
CREATE TABLE IF NOT EXISTS indicator_calc_log (
    id               SERIAL PRIMARY KEY,
    stock_code       VARCHAR(10) NOT NULL,
    calc_date        DATE NOT NULL,
    params_snapshot  JSONB NOT NULL DEFAULT '{}',
    row_count        INTEGER DEFAULT 0,
    status           VARCHAR(20) NOT NULL DEFAULT 'success',
    error_detail     TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (stock_code, calc_date)
);
CREATE INDEX IF NOT EXISTS idx_icl_date ON indicator_calc_log (calc_date);
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
CREATE INDEX IF NOT EXISTS idx_fv_feature_date ON feature_values (feature_name, trade_date);
CREATE INDEX IF NOT EXISTS idx_fv_stock_date ON feature_values (stock_code, trade_date);
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
    pending_cells_total     BIGINT DEFAULT 0,
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
    ('indicator_incr', 'kline', '指标增量', 10),
    ('indicator_full', '', '指标全量', 11),
    ('model_train', '', '模型训练', 12),
    ('model_signal', 'indicator_incr', '模型信号', 13),
    ('model_health', 'model_signal', '模型健康', 14),
    ('feature_compute', 'indicator_incr', '特征计算', 15)
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

# ── 顺序很重要（满足外键/依赖）──

ALL_TABLES = [
    ("backfill_tasks", CREATE_BACKFILL_TASKS),
    ("indicator_calc_log", CREATE_INDICATOR_CALC_LOG),
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
    ("stock_indicators_boll", CREATE_INDICATORS_BOLL),
    ("stock_indicators_macd", CREATE_INDICATORS_MACD),
    ("stock_indicators_rsi", CREATE_INDICATORS_RSI),
    ("stock_indicators_atr", CREATE_INDICATORS_ATR),
    ("stock_indicators_ma", CREATE_INDICATORS_MA),
    ("stock_indicators_volume", CREATE_INDICATORS_VOLUME),
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
    ("backtest_records", CREATE_BACKTEST_RECORDS),
    ("backtest_trades", CREATE_BACKTEST_TRADES),
    ("download_history", CREATE_DOWNLOAD_HISTORY),
    ("entity_meta", CREATE_ENTITY_META),
    ("feature_values", CREATE_FEATURE_VALUES),
]


def init_db(sync_session) -> None:
    """初始化数据库：建表 + 默认数据。幂等，可重复执行。"""
    for name, sql in ALL_TABLES:
        for stmt in sql.strip().split(";"):
            stmt = stmt.strip()
            if stmt and not stmt.startswith("--"):
                try:
                    sync_session.execute(text(stmt))
                except Exception as e:
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

    # 交易日历：如果为空则从 baostock 同步真实日历（含法定节假日）
    cnt = sync_session.execute(text("SELECT COUNT(*) FROM trade_calendar")).scalar() or 0
    if cnt == 0:
        from crawler.trade_calendar import sync_from_baostock
        sync_from_baostock(sync_session, start_year=2020, end_year=2030)
