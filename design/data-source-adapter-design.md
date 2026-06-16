# 数据源适配器架构设计

> 状态：设计中 | 创建日期：2026-06-15 | 参考文档：`docs/akshare/api-reference.md`

---

## 一、背景与问题

### 1.1 现状

所有数据下载**强依赖 baostock 单一数据源**。`BaostockCrawler`（600+ 行）将数据获取、转换、写入全部耦合在一个类中。7 个独立脚本（`full_refresh.py`、`full_reset_kline.py`、`resume_kline.py` 等）**重复实现了 baostock 的登录/拉取逻辑**。

### 1.2 痛点

| 问题 | 影响 |
|------|------|
| baostock 单点故障 | 服务不可用时全线阻塞，无法获取任何数据 |
| 指数/ETF 无重试机制 | 个股有 4 次重试，指数/ETF 仅单次尝试 |
| 代码重复 | 7 个脚本各自 copy 了 `bs.login/query/logout` |
| 无抽象层 | 换数据源需改所有调用方 |
| `_record_failure()` 死代码 | 失败记录方法定义了但从未被调用 |

### 1.3 目标

1. 引入适配器模式，解耦数据获取与业务逻辑
2. 接入 AKShare 作为首选数据源，保留 baostock 为备选
3. 下载前自动探测数据源可用性，选择最优源
4. 所有数据源输出统一标准化结构，数据库和业务层完全不感知数据源差异

---

## 二、数据源选型结论

| 数据源 | 免费 | 注册 | 个股K线 | 指数 | ETF | 基本面 | 日历 | 维护 | 结论 |
|--------|------|------|---------|------|-----|--------|------|------|------|
| **AKShare** | ✅ | 无需 | ✅ 含后复权 | ✅ | ✅ | PE/PB/总市值 | ✅ | 极活跃(24k⭐) | **首选** |
| baostock | ✅ | 无需 | ✅ 含后复权 | ✅ | ✅ | PE/PB/ROE/营收增长 | ✅ | 低频更新 | **备选** |
| Tushare | ⚠️ 积分制 | 需Token | ✅ 需自算复权 | ✅ | ✅ | 需5000积分 | ✅ | 活跃 | 预留扩展位 |

---

## 三、架构总览

```
DAG Pipeline / 独立脚本
        │
        ▼
┌──────────────────────────┐
│    DataSourceManager     │
│  ┌─────────────────────┐ │
│  │ check_all_health()  │ │    ← 下载前探测可用性
│  │ get_source()        │ │    ← 按优先级返回最优源
│  │ fetch_with_fallback()│ │   ← 失败自动切换下一源
│  └─────────────────────┘ │
└────────────┬─────────────┘
             │
    ┌────────┼────────┐
    ▼        ▼        ▼
┌────────┐┌────────┐┌────────┐
│AKShare ││Baostock││(Tushare│
│Adapter ││Adapter ││ 预留)  │
│pri=10  ││pri=20  ││pri=30  │
└───┬────┘└───┬────┘└────────┘
    │         │
    └────┬────┘
         ▼
┌──────────────────┐
│ 标准化数据结构     │     ← 所有适配器输出相同结构
│ KlineRow         │
│ IndexKlineRow    │
│ FundamentalRow   │
│ StockInfo        │
└────────┬─────────┘
         ▼
┌──────────────────┐
│ 数据库写入层       │     ← 统一的 batch_upsert 函数
│ (保持不变)        │
└──────────────────┘
```

### 3.1 文件结构

```
crawler/
├── adapters/
│   ├── __init__.py           # 导出 DataSourceManager 单例
│   ├── base.py               # ABC + 标准化 dataclass 定义
│   ├── akshare_adapter.py    # AKShare 适配器实现
│   ├── baostock_adapter.py   # Baostock 适配器（从 BaostockCrawler 提取）
│   └── manager.py            # DataSourceManager
├── baostock_crawler.py       # 【保留】旧类改为代理层，内部委托给 baostock_adapter
├── writers.py                # 【新增】统一数据库写入函数（从 crawler 中提取）
├── daily_crawl.py            # 改为使用 DataSourceManager
├── ...
```

---

## 四、适配器接口定义

### 4.1 标准化数据结构（`crawler/adapters/base.py`）

```python
from dataclasses import dataclass
from abc import ABC, abstractmethod
from typing import List, Optional

@dataclass
class KlineRow:
    """个股 / ETF 日K线标准行"""
    trade_date: str       # "2024-01-02" (YYYY-MM-DD)
    stock_code: str       # "000001" (6位纯数字，无前缀)
    stock_name: str       # "平安银行"
    exchange: str         # "SSE" / "SZSE" / "BSE"
    open: float
    high: float
    low: float
    close: float          # 不复权收盘价
    close_hfq: float      # 后复权收盘价
    volume: int           # 成交量（股）⚠️ 统一为「股」
    amount: float         # 成交额（元）
    turnover: Optional[float] = None  # 换手率 (%)

@dataclass
class IndexKlineRow:
    """指数日K线标准行"""
    trade_date: str       # "2024-01-02"
    index_code: str       # "000001" (6位纯数字，无前缀)
    index_name: str       # "上证指数"
    open: float
    high: float
    low: float
    close: float
    volume: int           # 成交量
    amount: float         # 成交额（元）

@dataclass
class FundamentalRow:
    """基本面数据标准行"""
    stock_code: str
    stock_name: str
    industry: Optional[str] = None
    pe_ttm: Optional[float] = None     # 市盈率(TTM)
    pb_mrq: Optional[float] = None     # 市净率(MRQ)
    roe: Optional[float] = None        # ROE (%)
    revenue_yoy: Optional[float] = None  # 营收同比 (%)
    profit_yoy: Optional[float] = None   # 净利同比 (%)
    total_shares: Optional[int] = None   # 总股本（股）
    market_cap: Optional[int] = None     # 总市值（元）

@dataclass
class StockInfo:
    """股票/指数/ETF 基本信息"""
    stock_code: str       # "000001"
    stock_name: str       # "平安银行"
    exchange: str         # "SSE" / "SZSE" / "BSE"
    ipo_date: Optional[str] = None
    status: str = "N"     # "N"=正常 / "D"=退市
    stock_type: str = "stock"  # "stock" / "index" / "etf"
```

> **⚠️ 代码冲突（设计时未考虑到，开发中发现）**：
> 同一 6 位代码可能同时是股票和指数。如 `000001` 既是**平安银行**(stock, SZSE) 又是**上证综合指数**(index, SSE)。类似冲突约 120 只（000xxx 深交所个股 vs 上交所指数）。因此 `stock_master` 表的主键必须从单一 `stock_code` 改为复合主键 `(stock_code, stock_type)`，且所有 `ON CONFLICT` 子句需同步更新。

### 4.2 适配器抽象基类

```python
class DataSourceAdapter(ABC):
    """数据源适配器抽象基类。所有适配器必须实现以下方法。"""

    name: str          # 数据源名称，如 "akshare" / "baostock"
    priority: int      # 优先级，数字越小越优先（10=首选, 20=备选）

    @abstractmethod
    def check_health(self) -> bool:
        """轻量级健康探测（<5秒）。返回 True 表示可用。"""

    @abstractmethod
    def fetch_stock_kline(self, codes: List[str],
                          start: str, end: str) -> List[KlineRow]:
        """拉取个股日K线（含后复权）。
        Args:
            codes: 股票代码列表 ["000001", "600519"]
            start: 开始日期 "2024-01-01"
            end:   结束日期 "2024-12-31"
        """

    @abstractmethod
    def fetch_index_kline(self, codes: List[str],
                          start: str, end: str) -> List[IndexKlineRow]:
        """拉取指数日K线。codes 格式同 stock_code，6位数字。"""

    @abstractmethod
    def fetch_etf_kline(self, codes: List[str],
                        start: str, end: str) -> List[KlineRow]:
        """拉取 ETF 日K线（含后复权）。"""

    @abstractmethod
    def fetch_fundamentals(self, codes: List[str]) -> List[FundamentalRow]:
        """拉取基本面数据（PE/PB/ROE/营收增长等）。"""

    @abstractmethod
    def get_stock_list(self, stock_type: str = "stock") -> List[StockInfo]:
        """获取股票/指数/ETF 列表。
        Args:
            stock_type: "stock" / "index" / "etf"
        """

    @abstractmethod
    def get_trade_calendar(self, start_year: int,
                           end_year: int) -> List[dict]:
        """获取交易日历。返回 [{"cal_date": "2024-01-02", "is_trade_day": True}]"""
```

---

## 五、逐字段映射表

### 5.1 个股日K线 → `daily_quote` 表

| DB 列 | DB 类型 | Baostock 原始字段 | Baostock 转换 | AKShare 原始字段 | AKShare 转换 |
|--------|---------|------------------|--------------|-----------------|-------------|
| `trade_date` | DATE | `date` | 直接使用 `"2024-01-02"` | `日期` | 直接使用 `"2024-01-02"` |
| `exchange` | VARCHAR(4) | 从代码推断 | `6xx→SSE, 0xx/3xx→SZSE, 4xx/8xx→BSE` | 从代码推断 | 同左规则 |
| `stock_code` | VARCHAR(6) | `code` → `"sh.600519"` | 去掉 `sh./sz./bj.` 前缀 | `symbol` 参数 | 已是6位数字，无需转换 |
| `stock_name` | VARCHAR(30) | 从 `stock_master` 查 | JOIN 获取 | 需从 `stock_info_a_code_name()` 预加载 | 建立 code→name 映射字典 |
| `open` | NUMERIC(10,2) | `open` (str) | `float(val)` | `开盘` (float64) | 直接使用 |
| `high` | NUMERIC(10,2) | `high` (str) | `float(val)` | `最高` (float64) | 直接使用 |
| `low` | NUMERIC(10,2) | `low` (str) | `float(val)` | `最低` (float64) | 直接使用 |
| `close` | NUMERIC(10,2) | `close` (str), adjustflag=3 | `float(val)` | `收盘` (float64), adjust="" | 直接使用 |
| `close_hfq` | NUMERIC(10,3) | `close` (str), adjustflag=1 | 第2次 API 调用获取 | `收盘` (float64), adjust="hfq" | 第2次调用获取 |
| `close_qfq` | NUMERIC(10,3) | 设为不复权 close | `= close` | 设为不复权 close | `= close` |
| `volume` | BIGINT | `volume` (str) | `int(val)` **单位：股** | `成交量` (int64) | `int(val) * 100` **⚠️ 单位：手→股** |
| `amount` | NUMERIC(18,2) | `amount` (str) | `float(val)` | `成交额` (float64) | 直接使用（单位：元） |
| `turnover` | NUMERIC(8,4) | `turn` (str) | `float(val)` | `换手率` (float64) | 直接使用（单位：%） |

**⚠️ 关键差异总结**：
1. **成交量单位**：AKShare 返回「手」(= 100股)，baostock 返回「股」→ AKShare 需 `×100`
2. **API 调用次数**：两个源都需要**调用 2 次**（不复权 + 后复权），分别获取 OHLCV 和 close_hfq
3. **代码格式**：baostock 用 `"sh.600519"`，AKShare 用 `"600519"` 纯数字
4. **列名语言**：baostock 英文、AKShare 中文

### 5.2 指数日K线 → `index_daily_quote` 表

| DB 列 | DB 类型 | Baostock 原始字段 | Baostock 转换 | AKShare 原始字段 | AKShare 转换 |
|--------|---------|------------------|--------------|-----------------|-------------|
| `trade_date` | DATE | `date` | 直接使用 | `date` | 直接使用 |
| `index_code` | VARCHAR(8) | `code` → `"sh.000001"` | 去前缀 `→ "000001"` | `symbol` 参数 `"sh000001"` | 去前缀 `→ "000001"` |
| `index_name` | VARCHAR(20) | 从 `stock_basic` 查 | JOIN 获取 | 需从 `stock_zh_index_spot_em()` 预加载 | 建立 code→name 字典 |
| `open` | NUMERIC(10,2) | `open` (str) | `float(val)` | `open` (float64) | 直接使用 |
| `high` | NUMERIC(10,2) | `high` (str) | `float(val)` | `high` (float64) | 直接使用 |
| `low` | NUMERIC(10,2) | `low` (str) | `float(val)` | `low` (float64) | 直接使用 |
| `close` | NUMERIC(10,2) | `close` (str) | `float(val)` | `close` (float64) | 直接使用 |
| `volume` | BIGINT | `volume` (str) | `int(val)` | `volume` (int64) | 直接使用（已是股数） |
| `amount` | NUMERIC(18,2) | `amount` (str) | `float(val)` | `amount` (float64) | 直接使用 |

**⚠️ 关键差异**：
1. **代码格式**：baostock `"sh.000001"` (有点)，AKShare `"sh000001"` (无点)
2. **AKShare 接口选择**：推荐 `stock_zh_index_daily_em(symbol="sh000001")`，输出列为英文
3. **无复权**：指数不需要复权，只调用 1 次

### 5.3 ETF 日K线 → `daily_quote` 表

与个股日K线**完全相同的映射规则**，差异点：

| 差异点 | Baostock | AKShare |
|--------|----------|---------|
| 接口 | `query_history_k_data_plus` (同个股) | `fund_etf_hist_em()` (专用 ETF 接口) |
| 代码识别 | `stock_basic` type=5 过滤 | 直接传 ETF 代码如 `"510050"` |
| 输出格式 | 同个股 | 同个股（中文列名，成交量=手） |
| 同样需要 2 次调用 | ✅ 不复权 + 后复权 | ✅ adjust="" + adjust="hfq" |

### 5.4 基本面 → `stock_fundamentals` / `stock_fundamentals_history` / `stock_industry`

| DB 列 | DB 类型 | Baostock 原始 | 转换 | AKShare 原始 | 转换 |
|--------|---------|-------------|------|-------------|------|
| `stock_code` | VARCHAR(6) | `code` | 去前缀 | `symbol` 参数 | 已是6位 |
| `stock_name` | VARCHAR(20) | 从 master 查 | — | 从 `stock_info_a_code_name()` | 预加载字典 |
| `industry` | VARCHAR(50) | `query_stock_industry()` 返回 | 直接使用 | **⚠️ AKShare 无直接行业接口** | 保留 baostock 或从东方财富个股信息获取 |
| `pe_ttm` | NUMERIC(10,2) | `query_history_k_data_plus` → `peTTM` | `float(val)` | ~~`stock_a_indicator_lg()`~~ **⚠️ 该 API 在 v1.18.23 已不存在，改用 `stock_individual_info_em()`** → 不含 PE/PB | 仅返回总市值/总股本/行业 |
| `pb_mrq` | NUMERIC(10,2) | `query_history_k_data_plus` → `pbMRQ` | `float(val)` | 同上 | — |
| `roe` | NUMERIC(10,2) | `query_profit_data()` → 第4列 | `float(val) * 100`（小数→%） | **⚠️ AKShare 无直接 ROE 接口** | 保留 baostock 作为该字段来源 |
| `revenue_yoy` | NUMERIC(10,2) | `query_growth_data()` → 第2列 | `float(val) * 100` | **⚠️ 同上** | 保留 baostock |
| `profit_yoy` | NUMERIC(10,2) | `query_growth_data()` → 第3列 | `float(val) * 100` | **⚠️ 同上** | 保留 baostock |
| `total_shares` | BIGINT | 计算: `volume / (turn/100)` | 整数化 | `stock_individual_info_em()` → `总股本` | 直接使用 |
| `market_cap` | BIGINT | 计算: `close * total_shares` | 整数化 | `stock_individual_info_em()` → `总市值` | 直接使用（单位已是元） |

**⚠️ 基本面关键差异**：
1. **ROE / 营收增长 / 净利增长**：AKShare 的 `stock_a_indicator_lg()` **不包含**这三个字段，需要调用其他接口或保留 baostock 作为该类数据的来源
2. **总市值单位**：AKShare 返回「万元」，DB 存「元」→ 需 `×10000`
3. **行业分类**：AKShare 无直接等价于 baostock `query_stock_industry()` 的接口，可用 `ak.stock_board_industry_name_em()` 替代

### 5.5 交易日历 → `trade_calendar` 表

| DB 列 | DB 类型 | Baostock | AKShare |
|--------|---------|----------|---------|
| `cal_date` | DATE | `query_trade_dates()` 返回完整日历 | `tool_trade_date_hist_sina()` 仅返回交易日 |
| `is_trade_day` | BOOLEAN | 有 `is_trading_day` 字段 | **⚠️ 只有交易日列表，需自行补全非交易日标记为 false** |
| `exchange` | VARCHAR(4) | 按交易所分别查询 | 返回全市场，不区分交易所 |

**⚠️ AKShare 差异**：只返回交易日列表，需要自行生成完整日历（交易日=true, 周末/节假日=false）

---

## 六、DataSourceManager 设计（`crawler/adapters/manager.py`）

### 6.1 核心职责

| 方法 | 职责 | 调用时机 |
|------|------|----------|
| `register(adapter)` | 注册数据源适配器 | 应用启动时 |
| `check_all_health()` | 逐个探测所有源的可用性，结果缓存 5 分钟 | 每次下载前自动调用 |
| `get_source(data_type)` | 按优先级返回第一个健康的源 | DAG task 内部 |
| `fetch_with_fallback(method, *args)` | 按优先级逐个尝试，某源失败自动切换下一个 | 替代直接调用适配器 |

### 6.2 伪代码

```python
import time
from typing import Optional
from loguru import logger

class DataSourceManager:
    def __init__(self):
        self._sources: list[DataSourceAdapter] = []
        self._health_cache: dict[str, tuple[bool, float]] = {}  # {name: (healthy, timestamp)}
        self._cache_ttl = 300  # 5 分钟

    def register(self, adapter: DataSourceAdapter):
        self._sources.append(adapter)
        self._sources.sort(key=lambda s: s.priority)

    def check_all_health(self) -> dict[str, bool]:
        """检查所有源健康状态，返回 {name: bool}"""
        result = {}
        for src in self._sources:
            cached = self._health_cache.get(src.name)
            if cached and time.time() - cached[1] < self._cache_ttl:
                result[src.name] = cached[0]
                continue
            try:
                healthy = src.check_health()
            except Exception:
                healthy = False
            self._health_cache[src.name] = (healthy, time.time())
            result[src.name] = healthy
            logger.info(f"[DataSource] {src.name} health: {'✅' if healthy else '❌'}")
        return result

    def get_source(self) -> DataSourceAdapter:
        """返回优先级最高的健康源"""
        self.check_all_health()
        for src in self._sources:
            if self._health_cache.get(src.name, (False,))[0]:
                return src
        raise RuntimeError("所有数据源均不可用")

    def fetch_with_fallback(self, method_name: str, *args, **kwargs):
        """带自动切换的数据拉取。遍历所有健康源，首个成功即返回。"""
        self.check_all_health()
        errors = []
        for src in self._sources:
            if not self._health_cache.get(src.name, (False,))[0]:
                continue
            try:
                fn = getattr(src, method_name)
                result = fn(*args, **kwargs)
                logger.info(f"[DataSource] {method_name} 成功，使用 {src.name}")
                return result, src.name  # 返回数据 + 来源名
            except Exception as e:
                logger.warning(f"[DataSource] {src.name}.{method_name} 失败: {e}")
                errors.append((src.name, str(e)))
                # 标记为不健康，避免后续再试
                self._health_cache[src.name] = (False, time.time())
        raise RuntimeError(f"所有数据源均失败: {errors}")

# ── 全局单例 ──
_manager: Optional[DataSourceManager] = None

def get_data_source_manager() -> DataSourceManager:
    global _manager
    if _manager is None:
        _manager = DataSourceManager()
        from crawler.adapters.akshare_adapter import AKShareAdapter
        from crawler.adapters.baostock_adapter import BaostockAdapter
        _manager.register(AKShareAdapter())   # priority=10
        _manager.register(BaostockAdapter())  # priority=20
    return _manager
```

### 6.3 健康检查实现

| 数据源 | check_health() 实现 | 超时 |
|--------|---------------------|------|
| AKShare | `ak.stock_zh_a_hist("000001", start_date=昨天, end_date=昨天)` 返回非空 | 5s |
| Baostock | `bs.login()` 成功 + `bs.query_trade_dates()` 返回非空 | 5s |

### 6.4 API 接口（前端可视化）

```
GET /api/data-sources/health
```
返回：
```json
{
  "sources": [
    {"name": "akshare", "priority": 10, "healthy": true, "checked_at": "2026-06-16T08:00:12"},
    {"name": "baostock", "priority": 20, "healthy": false, "checked_at": "2026-06-16T08:00:14"}
  ],
  "active_source": "akshare"
}
```

---

## 七、适配器实现要点

### 7.1 AKShareAdapter（`crawler/adapters/akshare_adapter.py`）

```python
class AKShareAdapter(DataSourceAdapter):
    name = "akshare"
    priority = 10  # 首选

    def __init__(self):
        self._name_cache: dict[str, str] = {}  # code → name 缓存
        self._request_interval = 0.8  # 请求间隔秒数，防反爬
```

**个股K线核心流程**：

```python
def fetch_stock_kline(self, codes, start, end):
    rows = []
    for code in codes:
        # 第1次：不复权 OHLCV
        df_raw = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=start.replace("-",""),
            end_date=end.replace("-",""),
            adjust=""
        )
        # 第2次：后复权收盘价
        df_hfq = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=start.replace("-",""),
            end_date=end.replace("-",""),
            adjust="hfq"
        )
        # 按日期合并 hfq close
        hfq_map = dict(zip(df_hfq["日期"], df_hfq["收盘"]))
        name = self._get_name(code)
        exchange = self._code_to_exchange(code)

        for _, r in df_raw.iterrows():
            rows.append(KlineRow(
                trade_date=str(r["日期"]),
                stock_code=code,
                stock_name=name,
                exchange=exchange,
                open=float(r["开盘"]),
                high=float(r["最高"]),
                low=float(r["最低"]),
                close=float(r["收盘"]),
                close_hfq=float(hfq_map.get(r["日期"], r["收盘"])),
                volume=int(r["成交量"]) * 100,   # ⚠️ 手→股
                amount=float(r["成交额"]),
                turnover=float(r["换手率"]) if r["换手率"] else None,
            ))
        time.sleep(self._request_interval)
    return rows
```

**AKShare 特有注意事项**：

| 问题 | 处理方式 | 实际验证 |
|------|----------|----------|
| `stock_a_indicator_lg` API | 设计时假设此 API 存在用于获取 PE/PB/市值 | ⚠️ **在 v1.18.23 中不存在**，改用 `stock_individual_info_em()` 获取总市值/总股本/行业，PE/PB 需从 baostock 补充 |
| `start_date` 格式 `"20240101"` 无连字符 | 入参 `"2024-01-01"` 需 `.replace("-","")` | ✅ 已验证 |
| 成交量单位「手」 | `* 100` 转为「股」 | ✅ 已验证 |
| 股票名称不在 K 线返回中 | 首次调用 `stock_info_a_code_name()` 建立全局映射缓存 | ✅ 已验证（5,528 只） |
| 东方财富反爬 | 每次请求间隔 ≥ 0.8 秒 | ⚠️ **部分网络环境完全不可达**（开发网络被东方财富屏蔽），这是 AKShare 依赖东方财富公开接口的固有风险 |
| 空 DataFrame | 接口在无数据时返回空 DF 而非抛异常，需检查 `len(df) == 0` | ✅ 已验证 |
| HTTP 连接错误 | `Connection aborted / RemoteDisconnected` | 需捕获所有网络异常并降级为空结果 |

### 7.2 BaostockAdapter（`crawler/adapters/baostock_adapter.py`）

从现有 `BaostockCrawler` 提取核心拉取逻辑，**保留其已有的重试机制**。

> **⚠️ 关键约束：baostock 非线程安全**（开发过程中发现的重要问题）
>
> baostock 内部使用**单一 TCP 连接**（通过 `bs.login()` 建立）。多个线程并发调用 `bs.query_*` 时，响应数据会**串扰**——如股票 600104 返回 600105 的 K 线数据，导致数据库中写入错误数据。
>
> **必须串行调用 baostock API**，禁止 `ThreadPoolExecutor` 或多线程并发访问同一 baostock 会话。

```python
class BaostockAdapter(DataSourceAdapter):
    name = "baostock"
    priority = 20  # 备选

    def __init__(self):
        self._logged_in = False
        self.RETRY_MAX = 4
        self.RETRY_BACKOFF = 3

    def check_health(self) -> bool:
        try:
            lg = bs.login()
            ok = lg.error_code == '0'
            if ok: bs.logout()
            return ok
        except:
            return False

    def _ensure_login(self):
        if not self._logged_in:
            # 复用 BaostockCrawler.login() 的 5 次重试逻辑
            ...
```

**代码映射**（指数/ETF 需要专用转换函数，通用 `_bs_code()` 不能覆盖）：

| 方法 | 用途 | 规则 | 示例 |
|------|------|------|------|
| `_bs_code(code)` | 个股代码 | `6xxxxx→sh`，其他→`sz` | `600519→sh.600519` |
| `_bs_index_code(code)` | 指数代码 | `000xxx→sh`，`399xxx→sz` | `000001→sh.000001`（上证指数） |
| `_bs_etf_code(code)` | ETF 代码 | `5xxxxx→sh`，`1xxxxx→sz` | `510050→sh.510050`（50ETF） |

> **⚠️ 重要**：个股 `000001`（平安银行·深交所）和指数 `000001`（上证指数·上交所）虽然代码相同但**交易所不同**。通用的 `_bs_code("000001")` 返回 `"sz.000001"`（平安银行），而指数需要用 `_bs_index_code("000001")` 返回 `"sh.000001"`（上证指数）。设计时未考虑此冲突，导致 fetch_index_kline 最初返回了错误的个股数据。

**会话稳定性**（开发过程中发现 baostock 的两个退化模式）：

| 退化模式 | 现象 | 修复 |
|----------|------|------|
| **会话退化** | 约 200 次请求后开始返回空数据 | 每 200 只股票主动 `logout()+login()` 刷新 |
| **连接 hang 住** | 请求发出后永不返回（线程永久阻塞） | `socket.setdefaulttimeout(30)`，超时立即跳过 |
| **行数据残缺** | API 返回成功但行数据长度不足，`r[4]` 引发 IndexError | 过滤 `< 8` 列的行（`if len(row) >= 8 and row[0]`） |
| **超时重试死循环** | 超时后 `RETRY_MAX=4` 每次都超时（退化会话重试无用） | 检测 `timed out/timeout` 异常后**不重试**，直接返回空 |

**批量下载最终方案**（串行 + 会话刷新）：

```
for i, code in enumerate(codes):
    if i % 200 == 0: logout(); sleep(2); login()  # 每 200 只刷新会话
    rows = _fetch_kline(code, start, end)           # 串行调用
    if len(rows) == 0: empty_streak += 1
    else: empty_streak = 0; write_to_buffer(rows)
    if empty_streak >= 20: logout(); login()        # 连续 20 只空结果时提前刷新
    if len(buffer) >= 500: batch_upsert(db, buffer) # 批量写入 DB
```

**提取原则**：
1. `_fetch_kline()` → 提取为 `fetch_stock_kline()` + `fetch_etf_kline()`（注意使用正确的 `_bs_index_code` / `_bs_etf_code`）
2. `download_all_index_daily()` → 提取为 `fetch_index_kline()`
3. `download_fundamentals()` → 提取为 `fetch_fundamentals()`
4. 保留重试机制，但超时不重试
5. 去掉数据库写入逻辑（移到 `writers.py`）
6. 去掉 ThreadPoolExecutor 多线程（baostock 非线程安全）

### 7.3 旧 BaostockCrawler 兼容层

```python
# crawler/baostock_crawler.py（改造后）
class BaostockCrawler:
    """兼容层：保留旧接口签名，内部委托给 DataSourceManager。
    确保 pipeline.py / daily_crawl.py 等现有调用方无需立即改动。"""

    def __init__(self, db_session):
        self.db = db_session
        self._manager = get_data_source_manager()

    def download_daily_update(self, trade_date, ...):
        rows, source = self._manager.fetch_with_fallback(
            "fetch_stock_kline", codes, trade_date, trade_date
        )
        batch_upsert_kline(self.db, rows)  # 统一写入
        return len(rows)
```

---

## 八、Pipeline 集成方案

### 8.1 DAG Task 改造前后对比

**改造前**（以 `dag_task_kline` 为例）：

```python
def dag_task_kline(trade_date=None, **kw):
    db = get_sync_db()
    crawler = BaostockCrawler(db)
    crawler.login()                              # 强绑定 baostock
    saved = crawler.download_daily_update(td)    # 拉取+写入耦合
    crawler.logout()
```

**改造后**：

```python
def dag_task_kline(trade_date=None, **kw):
    db = get_sync_db()
    manager = get_data_source_manager()

    # 1. 获取当日需要下载的股票列表
    codes = get_active_stock_codes(db, td)

    # 2. 通过 manager 自动选源 + fallback 拉取
    rows, source_name = manager.fetch_with_fallback(
        "fetch_stock_kline", codes, td, td
    )

    # 3. 统一写入（与数据源无关）
    saved = batch_upsert_kline(db, rows)

    # 4. 记录使用了哪个数据源
    write_node_log(log_id, status='success', rows=saved,
                   detail=f'{saved} 行 (来源: {source_name})')
```

### 8.2 四个 DAG 节点改造对照

**实际采用渐进式策略**（baostock 可用时保留原有路径，只在不可用时 fallback）：

| DAG 节点 | baostock 可用时 | baostock 不可用（fallback） | 写入函数 |
|----------|----------------|---------------------------|----------|
| `kline` | `BaostockCrawler.download_daily_update()` | `adapter.fetch_stock_kline()` → `batch_upsert_kline()` | 原有 / writers.py |
| `index` | `BaostockCrawler.download_all_index_daily()` | `adapter.fetch_index_kline()` → `batch_upsert_index_kline()` | 原有 / writers.py |
| `etf` | `BaostockCrawler.download_etf_daily()` | `adapter.fetch_etf_kline()` → `batch_upsert_kline()` | 原有 / writers.py |
| `fund` | `BaostockCrawler.download_fundamentals()` | `adapter.fetch_fundamentals()` → `batch_upsert_fundamentals()` | 原有 / writers.py |

### 8.3 前端 API 兼容（设计时遗漏的问题）

适配器层统一了数据拉取，但**前端 API 层未考虑指数/ETF 与个股使用不同数据库表**：

| API 端点 | 设计时假设 | 实际问题 | 修复 |
|----------|-----------|---------|------|
| `GET /api/stocks?category=index` | 所有数据在 `daily_quote` | 指数在 `index_daily_quote` | `CASE WHEN stock_type='index' THEN index_daily_quote ELSE daily_quote` |
| `GET /api/stock/{code}/detail` | 同上 | 同上 | 先查 `stock_master.stock_type`，指数走 `index_daily_quote` |
| `GET /api/stock/{code}/kline` | 同上 | 同上 | 同上 |
| `stock_name` 来源 | 从 `daily_quote.stock_name` | 适配器写入时可能为空 | 改为从 `stock_master.stock_name` 获取（权威来源） |
| `stock_type` 歧义 | `stock_code` 唯一确定类型 | 同码可有多类型（如 000001） | 新增 `?type=index/stock/etf` 可选参数 |

### 8.3 统一写入函数（`crawler/writers.py`）

从 `BaostockCrawler._batch_insert_rows()` 提取，接收标准化 dataclass 列表：

```python
def batch_upsert_kline(db, rows: list[KlineRow], batch_size=500):
    """批量 UPSERT 到 daily_quote，每 batch_size 行一次提交"""
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i+batch_size]
        db.execute(text("""
            INSERT INTO daily_quote
                (trade_date, exchange, stock_code, stock_name,
                 open, high, low, close, close_hfq, close_qfq,
                 volume, amount, turnover)
            VALUES (:td, :ex, :sc, :sn, :o, :h, :l, :c, :ch, :cq, :v, :a, :t)
            ON CONFLICT (stock_code, exchange, trade_date)
            DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high,
                          low=EXCLUDED.low, close=EXCLUDED.close,
                          close_hfq=EXCLUDED.close_hfq, volume=EXCLUDED.volume,
                          amount=EXCLUDED.amount, turnover=EXCLUDED.turnover
        """), [row_to_params(r) for r in batch])
    db.commit()

def batch_upsert_index_kline(db, rows: list[IndexKlineRow], batch_size=500):
    """批量 UPSERT 到 index_daily_quote"""
    ...

def batch_upsert_fundamentals(db, rows: list[FundamentalRow], batch_size=200):
    """批量 UPSERT 到 stock_fundamentals"""
    ...
```

### 8.4 独立脚本迁移

| 脚本 | 当前状态 | 迁移方式 |
|------|----------|----------|
| `scripts/full_refresh.py` | 重复实现 bs.login + 全量拉取 | 改为调用 `manager.fetch_with_fallback()` |
| `scripts/full_reset_kline.py` | 同上 | 同上 |
| `scripts/full_reset_fundamentals.py` | 同上 | 同上 |
| `scripts/resume_kline.py` | 断点续传 | 保留断点逻辑，拉取部分改用 manager |
| `scripts/init_dev_data.py` | 开发数据初始化 | 改用 manager |
| `crawler/daily_crawl.py` | 委托 BaostockCrawler | 改用 manager（或通过兼容层自动生效） |
| `crawler/catch_up.py` | 补数据 | 改用 manager |

---

## 九、分阶段实施计划

### Phase 1：基础层（预计 2 天）

| 步骤 | 产出 | 风险 |
|------|------|------|
| 创建 `crawler/adapters/` 目录结构 | `__init__.py`, `base.py` | 低 |
| 定义 5 个 dataclass + ABC 接口 | `base.py` 完整 | 低 |
| 实现 `DataSourceManager` | `manager.py` 完整 | 低 |
| 提取统一写入函数 | `writers.py` 完整 | 中（需确保 UPSERT SQL 与现有一致） |

### Phase 2：BaostockAdapter（预计 1 天）

| 步骤 | 产出 | 风险 |
|------|------|------|
| 从 `BaostockCrawler` 提取拉取逻辑 | `baostock_adapter.py` | 中（保留重试/relogin 机制） |
| 旧 `BaostockCrawler` 改为代理层 | 兼容层 | 低（接口签名不变） |
| 验证：Pipeline 通过代理层运行不变 | 回归测试 | 中 |

### Phase 3：AKShareAdapter（预计 2 天）

| 步骤 | 产出 | 风险 |
|------|------|------|
| `pip install akshare` | 依赖就绪 | 低 |
| 实现 `fetch_stock_kline` | 个股K线适配 | 中（成交量×100、名称缓存） |
| 实现 `fetch_index_kline` | 指数K线适配 | 低 |
| 实现 `fetch_etf_kline` | ETF K线适配 | 低（与个股类似） |
| 实现 `fetch_fundamentals` | 基本面适配 | 高（ROE/营收增长需额外接口） |
| 实现 `check_health` | 健康检查 | 低 |

### Phase 4：Pipeline 集成（预计 1 天）

| 步骤 | 产出 | 风险 |
|------|------|------|
| 改造 4 个 DAG task | 使用 manager | 中 |
| 改造独立脚本 | 统一入口 | 中 |
| DAG 日志记录数据源来源 | 可追溯 | 低 |

### Phase 5：前端 + 验证（预计 1 天）

| 步骤 | 产出 | 风险 |
|------|------|------|
| 数据源健康状态 API | `GET /api/data-sources/health` | 低 |
| 状态页展示当前数据源 | 前端卡片 | 低 |
| 全量下载对比测试 | 两个源数据一致性 | 中（可能有小数精度差异） |

---

## 十、风险评估与缓解

| 风险 | 严重度 | 概率 | 缓解措施 | 实际发现 |
|------|--------|------|----------|----------|
| AKShare API 接口变更 | 高 | 中 | 适配器内部消化，ABC 接口不变 | ⚠️ **已证实**：`stock_a_indicator_lg` 在 v1.18.23 不存在，已改用 `stock_individual_info_em` |
| 东方财富反爬/网络不可达 | 中 | 中 | 请求间隔 ≥ 0.8s；fallback 到 baostock | ⚠️ **已证实**：某些网络环境（如开发机）东方财富完全不可达，AKShare 全线失败 |
| **baostock 非线程安全** | **高** | **确定** | 串行调用，禁止 ThreadPoolExecutor | ⚠️ **设计时遗漏**：多线程导致数据串扰（600104 返回 600105 数据） |
| **baostock 会话退化** | **高** | **确定** | 每 200 只股票 logout+login 刷新 | ⚠️ **设计时遗漏**：~200 次请求后开始返回空/残缺数据 |
| **baostock 连接 hang 住** | **高** | **中** | `socket.setdefaulttimeout(30)` + 超时不重试 | ⚠️ **设计时遗漏**：退化后请求永不返回，线程永久阻塞 |
| 两源数据精度差异 | 低 | 高 | 以先到数据为准 | 尚未验证 |
| 基本面 ROE/营收 AKShare 缺失 | 中 | 确定 | PE/PB/market_cap 用 AKShare，ROE/营收用 baostock 补充 | 已实现混合策略 |
| **stock_master 代码冲突** | **中** | **确定** | PK 改为 `(stock_code, stock_type)` 复合主键 | ⚠️ **设计时遗漏**：000001 既是平安银行(stock)也是上证指数(index) |
| 前端 API 表路由 | 中 | 确定 | 判断 stock_type 后选择 daily_quote 或 index_daily_quote | ⚠️ **设计时遗漏**：kline/detail 接口最初只查 daily_quote |

### 基本面混合策略

由于 AKShare 和 Baostock 在基本面数据上各有优劣，采用**按字段选源**而非按整体选源：

```
stock_fundamentals 写入：
├── pe_ttm, pb_mrq, market_cap, total_shares → 优先 AKShare (stock_a_indicator_lg)
├── roe, revenue_yoy, profit_yoy            → 优先 Baostock (query_profit/growth_data)
└── industry                                 → 优先 Baostock (query_stock_industry)
```

Manager 的 `fetch_fundamentals` 内部自动合并两个源的数据，对调用方透明。

---

## 附录 A：AKShare 核心 API 速查

详见 `docs/akshare/api-reference.md`，包含 6 个核心接口的完整参数、输出列、DB 映射和差异标注。

## 附录 B：现有 Baostock 调用点清单

| 文件 | bs API | 涉及数据 |
|------|--------|----------|
| `crawler/baostock_crawler.py` | `login/logout/query_stock_basic/query_history_k_data_plus/query_all_stock/query_profit_data/query_growth_data/query_stock_industry` | 全部 |
| `crawler/trade_calendar.py` | `login/logout/query_trade_dates` | 交易日历 |
| `scripts/full_refresh.py` | `login/logout/query_stock_basic/query_history_k_data_plus` | 个股K线全量 |
| `scripts/full_reset_kline.py` | 同上 | 个股K线重置 |
| `scripts/full_reset_fundamentals.py` | `login/logout/query_stock_basic/query_profit_data` | 基本面全量 |
| `scripts/init_dev_data.py` | `login/logout/query_history_k_data_plus/query_profit_data` | 开发数据 |
| `scripts/resume_kline.py` | `login/logout/query_stock_basic/query_history_k_data_plus` | 断点续传 |
