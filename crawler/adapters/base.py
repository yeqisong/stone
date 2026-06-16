"""数据源适配器基础层：标准化数据结构 + 抽象基类。

所有适配器（AKShare / Baostock / 未来扩展）统一输出这里定义的 dataclass，
确保数据库写入层和业务层完全不感知数据源差异。

设计参考：design/data-source-adapter-design.md 第四章
"""
from dataclasses import dataclass
from abc import ABC, abstractmethod
from typing import List, Optional


# ── 标准化数据结构 ──


@dataclass
class KlineRow:
    """个股 / ETF 日K线标准行。

    所有适配器输出此结构，字段规范：
    - volume 统一为「股」（AKShare 返回「手」需 ×100）
    - amount 统一为「元」
    - turnover 为换手率百分比（如 2.5 表示 2.5%）
    - close_hfq 为后复权收盘价（用于策略回测）
    """
    trade_date: str         # "2024-01-02" (YYYY-MM-DD)
    stock_code: str         # "000001" (6位纯数字，无前缀)
    stock_name: str         # "平安银行"
    exchange: str           # "SSE" / "SZSE" / "BSE"
    open: float
    high: float
    low: float
    close: float            # 不复权收盘价
    close_hfq: float        # 后复权收盘价
    volume: int             # 成交量（股）
    amount: float           # 成交额（元）
    turnover: Optional[float] = None  # 换手率 (%)


@dataclass
class IndexKlineRow:
    """指数日K线标准行。

    指数无复权概念，只需一次 API 调用。
    """
    trade_date: str         # "2024-01-02"
    index_code: str         # "000001" (6位纯数字，无前缀)
    index_name: str         # "上证指数"
    open: float
    high: float
    low: float
    close: float
    volume: int             # 成交量
    amount: float           # 成交额（元）


@dataclass
class FundamentalRow:
    """基本面数据标准行。

    注意：
    - pe_ttm / pb_mrq 优先从 AKShare 获取
    - roe / revenue_yoy / profit_yoy 优先从 Baostock 获取（AKShare 无直接接口）
    - market_cap 统一为「元」（AKShare 返回「万元」需 ×10000）
    """
    stock_code: str
    stock_name: str
    industry: Optional[str] = None
    pe_ttm: Optional[float] = None       # 市盈率(TTM)
    pb_mrq: Optional[float] = None       # 市净率(MRQ)
    roe: Optional[float] = None          # ROE (%)
    revenue_yoy: Optional[float] = None  # 营收同比 (%)
    profit_yoy: Optional[float] = None   # 净利同比 (%)
    total_shares: Optional[int] = None   # 总股本（股）
    market_cap: Optional[int] = None     # 总市值（元）


@dataclass
class StockInfo:
    """股票/指数/ETF 基本信息。"""
    stock_code: str         # "000001"
    stock_name: str         # "平安银行"
    exchange: str           # "SSE" / "SZSE" / "BSE"
    ipo_date: Optional[str] = None     # "1991-04-03" 或 None
    status: str = "N"       # "N"=正常 / "D"=退市
    stock_type: str = "stock"  # "stock" / "index" / "etf"


# ── 抽象基类 ──


class DataSourceAdapter(ABC):
    """数据源适配器抽象基类。

    所有数据源适配器必须实现以下方法：
    - check_health(): 轻量级健康探测
    - fetch_stock_kline(): 个股日K线（含后复权）
    - fetch_index_kline(): 指数日K线
    - fetch_etf_kline(): ETF日K线（含后复权）
    - fetch_fundamentals(): 基本面数据
    - get_stock_list(): 股票/指数/ETF 列表
    - get_trade_calendar(): 交易日历

    属性：
    - name: 数据源名称标识
    - priority: 优先级（数字越小越优先，10=首选, 20=备选）
    """

    name: str
    priority: int

    @abstractmethod
    def check_health(self) -> bool:
        """轻量级健康探测（应在 5 秒内返回）。

        Returns:
            True 表示数据源当前可用
        """

    @abstractmethod
    def fetch_stock_kline(self, codes: List[str],
                          start: str, end: str) -> List[KlineRow]:
        """拉取个股日K线（含后复权）。

        Args:
            codes: 股票代码列表 ["000001", "600519"]（6位数字）
            start: 开始日期 "2024-01-01"
            end:   结束日期 "2024-12-31"

        Returns:
            标准化的 KlineRow 列表
        """

    @abstractmethod
    def fetch_index_kline(self, codes: List[str],
                          start: str, end: str) -> List[IndexKlineRow]:
        """拉取指数日K线。

        Args:
            codes: 指数代码列表 ["000001", "399001"]（6位数字，无 sh/sz 前缀）
            start: 开始日期
            end:   结束日期
        """

    @abstractmethod
    def fetch_etf_kline(self, codes: List[str],
                        start: str, end: str) -> List[KlineRow]:
        """拉取 ETF 日K线（含后复权）。

        Args:
            codes: ETF代码列表 ["510050", "510300"]
            start: 开始日期
            end:   结束日期
        """

    @abstractmethod
    def fetch_fundamentals(self, codes: List[str]) -> List[FundamentalRow]:
        """拉取基本面数据。

        Args:
            codes: 股票代码列表

        Returns:
            每只股票一行 FundamentalRow
        """

    @abstractmethod
    def get_stock_list(self, stock_type: str = "stock") -> List[StockInfo]:
        """获取股票/指数/ETF 列表。

        Args:
            stock_type: "stock" / "index" / "etf"
        """

    @abstractmethod
    def get_trade_calendar(self, start_year: int,
                           end_year: int) -> List[dict]:
        """获取交易日历。

        Args:
            start_year: 起始年份（含）
            end_year:   结束年份（含）

        Returns:
            [{"cal_date": "2024-01-02", "is_trade_day": True, "exchange": "SSE"}, ...]
        """

    def __repr__(self):
        return f"<{self.__class__.__name__} name={self.name} priority={self.priority}>"


# ── 工具函数 ──


def code_to_exchange(code: str) -> str:
    """根据股票代码推断交易所。

    规则：
    - 6 开头 → SSE（上交所）
    - 0/2/3 开头 → SZSE（深交所）
    - 4/8/9 开头 → BSE（北交所）
    """
    if not code:
        return "SZSE"
    first = code[0]
    if first == '6':
        return "SSE"
    elif first in ('0', '2', '3'):
        return "SZSE"
    elif first in ('4', '8', '9'):
        return "BSE"
    return "SZSE"
