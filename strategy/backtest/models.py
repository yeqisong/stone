"""回测引擎 v2 数据模型（纯 dataclass，无框架依赖）。"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class TradeConfig:
    """撮合/组合配置（对齐 design/05 §3.1）。

    min_cost / impact_cost / vol_limit / settle_delay 为 v2 增强项，
    与 v1（_simple_backtest）对比等价性时置默认关闭值。
    """
    initial_cash: float = 1_000_000
    max_positions: int = 5
    stop_loss: float = 0.08
    take_profit: float = 0.15
    trailing: float = 0.0
    hold_days: int = 10
    comm: float = 0.00025          # 佣金（双边）
    st_tax: float = 0.001          # 印花税（卖出）
    slip: float = 0.001            # 滑点（买入价含 slip/2；卖出全额进成本）
    min_cost: float = 5.0          # 最低佣金（Qlib min_cost；v1 无，对比时设 0）
    impact_cost: float = 0.0       # 冲击成本系数（Qlib 平方模型；0=关闭）
    vol_limit: Optional[float] = None  # 单票买入占当日成交量上限比例（None=不限制）
    deal_price: str = "close"      # 成交价基准：close / vwap
    settle_delay: bool = True      # 卖出资金 T+1 可用（Qlib cash_delay；v1 当日可用）
    forbid_all_trade_at_limit: bool = True  # 涨跌停双向全禁（False 时允许卖涨停/买跌停）
    trade_unit: int = 100          # A 股整手
    downsize_buy: bool = True      # 现金不足时降档买入（False=整笔放弃，与 v1 对齐）


@dataclass
class Order:
    """订单：方向 + 数量 + 触发价（止损/止盈按触发价成交，其余按收盘）。"""
    stock_code: str
    direction: str                 # BUY / SELL
    shares: int
    reason: str = ""
    limit_price: Optional[float] = None  # 触发价（止损/止盈）；None=市价（close）


@dataclass
class Fill:
    """成交记录（含拒绝原因，用于执行质量统计）。"""
    order: Order
    price: float = 0.0
    shares: int = 0
    fee: float = 0.0
    gross: float = 0.0
    rejected: bool = False
    reject_reason: str = ""


@dataclass
class Position:
    """持仓（count_days 由 Account 每日 +1，T+1 由 count>=1 保证）。"""
    code: str
    shares: int
    buy_price: float
    buy_date: str
    cost_basis: float              # 含买入费用
    peak: float
    count_days: int = 0


@dataclass
class DailyRecord:
    """逐日净值/换手/成本（对齐 Qlib PortfolioMetrics 列）。"""
    trade_date: str
    account: float                 # 账户总值（cash+cash_delay+持仓市值）
    cash: float
    position_value: float
    turnover: float                # 当日成交额（买卖合计）
    cost: float
    bench: Optional[float] = None  # 基准净值（沪深300 同期，由调用方填充）


@dataclass
class BacktestResult:
    """回测结果（字段与 v1 _simple_backtest 对齐，便于评估入口无缝切换）。"""
    sharpe: float = 0.0
    max_dd: float = 0.0
    win_rate: float = 0.0
    total_return: float = 0.0
    total_trades: int = 0
    total_cost: float = 0.0
    equity_curve: List[float] = field(default_factory=list)
    trades: List[dict] = field(default_factory=list)   # 逐笔（含 fill 详情）
    daily_records: List[DailyRecord] = field(default_factory=list)
    config: Optional[TradeConfig] = None
    final_account: Optional[object] = None  # 运行结束时的 Account（纸面组合恢复状态用）
