"""数据域白名单：DBExplorer / Agent SQL 只允许访问共享表。

个人表、身份/权限/审计/支持会话以及备份表一律禁止；新增共享表时必须在此登记。
"""
from __future__ import annotations

SHARED_TABLES = frozenset({
    # 行情/主数据/基本面
    "daily_quote", "index_daily_quote", "stock_master",
    "stock_fundamentals", "stock_fundamentals_history", "fina_indicator",
    # 拓展数据
    "stock_moneyflow", "stock_margin_detail", "stock_top_list",
    "stock_holder_number", "stock_hk_hold", "block_trade", "moneyflow_hsgt",
    "index_weight", "stock_dividend", "stock_express", "stock_forecast",
    "stock_repurchase", "stock_share_float", "stock_minute_bar",
    "sector_flow_daily", "index_valuation", "macro_indicator",
    "market_sentiment_daily", "trade_calendar", "daily_completeness",
    "stock_treemap_cache", "stock_treemap_intraday",
    # 运维/统计（不含密钥）
    "data_stats_cache", "entity_stats", "tushare_quota", "data_lineage",
    "backfill_tasks", "backfill_ext_checked",
    # 可转债
    "cb_basic", "cb_daily", "cb_share", "cb_metrics",
    # 资讯/雪球
    "news_item", "news_stock", "news_summary", "news_crawl_state", "news_poll_state",
    "xueqiu_status", "xueqiu_sentiment", "xueqiu_runtime", "xueqiu_crawl_state",
    # 模型/纸面账本
    "model_versions", "training_trials", "model_health", "version_comparisons",
    "backtest_records", "backtest_trades", "backtest_daily_records",
    "paper_positions", "paper_trades", "model_score_daily",
    # 特征/函数/信号
    "features", "feature_values", "functions", "function_versions",
    "factor_ic_stats", "signal_history",
    # DAG 定义/日志
    "dag_config", "dag_flows", "dag_flow_versions", "dag_run_log",
})


def is_shared_table(name: str) -> bool:
    return (name or "") in SHARED_TABLES
