"""数据源适配器基础层：标准化数据结构 + 抽象基类。

所有适配器（TuShare / Baostock / 未来扩展）统一输出这里定义的 dataclass，
确保数据库写入层和业务层完全不感知数据源差异。
"""
from dataclasses import dataclass
from abc import ABC, abstractmethod
from typing import List, Optional


# ── 标准化数据结构 ──


@dataclass
class KlineRow:
    """个股 / ETF 日K线标准行。

    所有适配器输出此结构，字段规范：
    - volume 统一为「股」
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
    """基本面数据标准行（daily_basic 全字段 + baostock 补充字段）。"""
    stock_code: str
    stock_name: str
    trade_date: Optional[str] = None    # 数据日期
    industry: Optional[str] = None
    pe_ttm: Optional[float] = None       # 市盈率(TTM)
    pe: Optional[float] = None           # 市盈率
    pb_mrq: Optional[float] = None       # 市净率(MRQ)
    ps: Optional[float] = None           # 市销率
    ps_ttm: Optional[float] = None       # 市销率(TTM)
    roe: Optional[float] = None          # ROE (%)
    revenue_yoy: Optional[float] = None  # 营收同比 (%)
    profit_yoy: Optional[float] = None   # 净利同比 (%)
    total_shares: Optional[int] = None   # 总股本（股）
    float_share: Optional[int] = None    # 流通股本（股）
    free_share: Optional[int] = None     # 自由流通股本（股）
    market_cap: Optional[int] = None     # 总市值（元）
    circ_mv: Optional[int] = None        # 流通市值（元）
    dv_ratio: Optional[float] = None     # 股息率 (%)
    dv_ttm: Optional[float] = None       # 股息率 TTM (%)
    turnover_rate: Optional[float] = None # 换手率 (%)
    volume_ratio: Optional[float] = None # 量比
    limit_status: Optional[int] = None   # 涨跌停状态


@dataclass
class StockInfo:
    """股票/指数/ETF 基本信息。"""
    stock_code: str         # "000001"
    stock_name: str         # "平安银行"
    exchange: str           # "SSE" / "SZSE" / "BSE"
    ipo_date: Optional[str] = None     # "1991-04-03" 或 None
    status: str = "N"       # "N"=正常 / "D"=退市
    stock_type: str = "stock"  # "stock" / "index" / "etf"
    delist_date: Optional[str] = None  # 退市日期
    is_hs: Optional[str] = None        # "H"沪深港通 / "S"深港通 / "N"否
    act_name: Optional[str] = None     # 实控人
    area: Optional[str] = None         # 地域（省）
    industry: Optional[str] = None     # 所属行业
    reg_capital: Optional[float] = None
    employees: Optional[int] = None
    main_business: Optional[str] = None


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
    - 5 开头 → SSE（沪市基金/ETF，如 510050）
    - 1 开头 → SZSE（深市基金/ETF，如 159915）
    """
    if not code:
        return "SZSE"
    first = code[0]
    if first == '6' or first == '5':
        return "SSE"
    elif first in ('0', '1', '2', '3'):
        return "SZSE"
    elif first in ('4', '8', '9'):
        return "BSE"
    return "SZSE"
