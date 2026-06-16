"""数据源适配器包。

提供多数据源支持（AKShare / Baostock），通过 DataSourceManager 自动选源和 fallback。

用法：
    from crawler.adapters import get_data_source_manager

    manager = get_data_source_manager()
    rows, source = manager.fetch_with_fallback("fetch_stock_kline", codes, start, end)
"""
from crawler.adapters.base import (
    DataSourceAdapter,
    KlineRow,
    IndexKlineRow,
    FundamentalRow,
    StockInfo,
    code_to_exchange,
)

__all__ = [
    "DataSourceAdapter",
    "KlineRow",
    "IndexKlineRow",
    "FundamentalRow",
    "StockInfo",
    "code_to_exchange",
    "get_data_source_manager",
]


def get_data_source_manager():
    """获取全局 DataSourceManager 单例。延迟导入避免循环引用。"""
    from crawler.adapters.manager import get_manager
    return get_manager()
