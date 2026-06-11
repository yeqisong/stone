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
    stock_code  VARCHAR(6) PRIMARY KEY,
    stock_name  VARCHAR(30) NOT NULL,
    exchange    VARCHAR(4) NOT NULL,
    ipo_date    DATE,
    status      VARCHAR(10) DEFAULT 'N',
    stock_type  VARCHAR(10) DEFAULT '',
    status_date DATE,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    volume     BIGINT NOT NULL,
    amount     NUMERIC(18,2) NOT NULL,
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
    params_snapshot   TEXT DEFAULT ''
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
INSERT OR IGNORE INTO strategy_config (strategy_name, display_name, enabled, params) VALUES
('global_preference',       '全局偏好', true, '{"mode": "balanced"}'),
('bollinger_daily',         '日布林线', true, '{"period": 20, "std_mult": 2.0, "bandwidth_threshold": 0.04}'),
('volume_price_divergence', '量价背离', true, '{"levels": ["daily","weekly","monthly"], "lookback_daily": 20, "lookback_weekly": 24, "lookback_monthly": 12}'),
('weekly_trend',            '周趋势',   true, '{"fast_period": 5, "slow_period": 20}');
"""

# ── 顺序很重要（满足外键/依赖）──

ALL_TABLES = [
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
    ("data_stats_cache", CREATE_STATS_CACHE),
    ("system_metrics", CREATE_SYSTEM_METRICS),
]


def init_db(sync_session) -> None:
    """初始化数据库：建表 + 默认数据。幂等，可重复执行。"""
    from app.config import settings
    is_sqlite = "sqlite" in settings.DATABASE_URL_SYNC

    for name, sql in ALL_TABLES:
        for stmt in sql.strip().split(";"):
            stmt = stmt.strip()
            if stmt and not stmt.startswith("--"):
                try:
                    if not is_sqlite:
                        # PostgreSQL: CREATE INDEX IF NOT EXISTS 原生支持
                        pass
                    else:
                        # SQLite 不支持 CREATE INDEX IF NOT EXISTS
                        stmt = stmt.replace("CREATE INDEX IF NOT EXISTS", "CREATE INDEX")
                    sync_session.execute(text(stmt))
                except Exception as e:
                    if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower() and "relation" not in str(e).lower():
                        raise

    # 默认策略配置
    if is_sqlite:
        sync_session.execute(text(DEFAULT_STRATEGY_CONFIG))
    else:
        # PostgreSQL: INSERT OR IGNORE → ON CONFLICT DO NOTHING
        pg_config = DEFAULT_STRATEGY_CONFIG.replace("INSERT OR IGNORE", "INSERT").rstrip(";\n ")
        pg_config += " ON CONFLICT (strategy_name) DO NOTHING;"
        sync_session.execute(text(pg_config))

    sync_session.commit()

    # 交易日历：如果为空则从 baostock 同步真实日历（含法定节假日）
    cnt = sync_session.execute(text("SELECT COUNT(*) FROM trade_calendar")).scalar() or 0
    if cnt == 0:
        from crawler.trade_calendar import sync_from_baostock
        sync_from_baostock(sync_session, start_year=2020, end_year=2030)
