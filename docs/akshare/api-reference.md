# AKShare API 参考文档（本项目所需接口）

> 版本：AKShare 1.18.64 | 数据源：东方财富(EastMoney) | 完全免费无需注册
> 官方文档：https://akshare.akfamily.xyz/
> GitHub：https://github.com/akfamily/akshare (24k+ ⭐)

---

## 1. 个股日K线（含复权）

### 接口：`ak.stock_zh_a_hist()`

**描述**：东方财富-A股日线行情，支持不复权/前复权/后复权

**输入参数**：

| 参数 | 类型 | 必选 | 说明 |
|------|------|------|------|
| symbol | str | ✅ | 股票代码，如 `"000001"` （6位纯数字，无前缀） |
| period | str | ❌ | `"daily"` (默认) / `"weekly"` / `"monthly"` |
| start_date | str | ❌ | 开始日期 `"20200101"` 格式（YYYYMMDD） |
| end_date | str | ❌ | 结束日期 `"20241231"` 格式（YYYYMMDD） |
| adjust | str | ❌ | `""` 不复权(默认) / `"qfq"` 前复权 / `"hfq"` 后复权 |

**输出列（DataFrame）**：

| 列名 | 类型 | 说明 | 映射到 DB |
|------|------|------|----------|
| 日期 | object | `"2024-01-02"` 格式 | `daily_quote.trade_date` |
| 开盘 | float64 | 开盘价 | `daily_quote.open` |
| 收盘 | float64 | 收盘价（根据 adjust 参数决定是否复权） | `daily_quote.close` 或 `close_hfq` |
| 最高 | float64 | 最高价 | `daily_quote.high` |
| 最低 | float64 | 最低价 | `daily_quote.low` |
| 成交量 | int64 | 成交量（手）⚠️ 注意单位是「手」= 100股 | `daily_quote.volume` (需 ×100) |
| 成交额 | float64 | 成交额（元） | `daily_quote.amount` |
| 振幅 | float64 | 振幅 (%) | — |
| 涨跌幅 | float64 | 涨跌幅 (%) | — |
| 涨跌额 | float64 | 涨跌额 | — |
| 换手率 | float64 | 换手率 (%) | `daily_quote.turnover` |

**⚠️ 关键差异**：
- 成交量单位是「手」(100股)，baostock 是「股」→ **需要 ×100**
- 需要调用两次：一次 `adjust=""` 获取不复权 OHLCV，一次 `adjust="hfq"` 获取后复权收盘价
- 日期格式是 `"2024-01-02"`（带连字符），baostock 也是同样格式

**用法示例**：
```python
import akshare as ak

# 不复权日线
df_raw = ak.stock_zh_a_hist(symbol="000001", period="daily",
                            start_date="20240101", end_date="20241231", adjust="")

# 后复权日线（仅需收盘价）
df_hfq = ak.stock_zh_a_hist(symbol="000001", period="daily",
                            start_date="20240101", end_date="20241231", adjust="hfq")
```

---

## 2. 指数日K线

### 接口：`ak.stock_zh_index_daily_em()`

**描述**：东方财富-指数日线行情

**输入参数**：

| 参数 | 类型 | 必选 | 说明 |
|------|------|------|------|
| symbol | str | ✅ | 指数代码，如 `"sh000001"` (上证综指)、`"sz399001"` (深证成指)、`"sh000300"` (沪深300) |
| start_date | str | ❌ | 开始日期 `"20200101"` 格式 |
| end_date | str | ❌ | 结束日期 `"20241231"` 格式 |

**输出列（DataFrame）**：

| 列名 | 类型 | 说明 | 映射到 DB |
|------|------|------|----------|
| date | object | `"2024-01-02"` 格式 | `index_daily_quote.trade_date` |
| open | float64 | 开盘价 | `index_daily_quote.open` |
| close | float64 | 收盘价 | `index_daily_quote.close` |
| high | float64 | 最高价 | `index_daily_quote.high` |
| low | float64 | 最低价 | `index_daily_quote.low` |
| volume | int64 | 成交量 | `index_daily_quote.volume` |
| amount | float64 | 成交额 | `index_daily_quote.amount` |

**⚠️ 关键差异**：
- symbol 格式为 `"sh000001"` (小写sh/sz前缀)，baostock 为 `"sh.000001"` (有点号)
- 输出列名是英文（date, open, close...），而个股日线是中文（日期, 开盘, 收盘...）
- 无「换手率」列

---

## 3. ETF 日K线

### 接口：`ak.fund_etf_hist_em()`

**描述**：东方财富-ETF 日线行情，支持复权

**输入参数**：

| 参数 | 类型 | 必选 | 说明 |
|------|------|------|------|
| symbol | str | ✅ | ETF代码，如 `"510050"` (50ETF)、`"510300"` (300ETF) |
| period | str | ❌ | `"daily"` (默认) / `"weekly"` / `"monthly"` |
| start_date | str | ❌ | 开始日期 `"20200101"` 格式 |
| end_date | str | ❌ | 结束日期 `"20241231"` 格式 |
| adjust | str | ❌ | `""` 不复权 / `"qfq"` 前复权 / `"hfq"` 后复权 |

**输出列（DataFrame）**：

| 列名 | 类型 | 说明 | 映射到 DB |
|------|------|------|----------|
| 日期 | object | `"2024-01-02"` 格式 | `daily_quote.trade_date` |
| 开盘 | float64 | 开盘价 | `daily_quote.open` |
| 收盘 | float64 | 收盘价 | `daily_quote.close` 或 `close_hfq` |
| 最高 | float64 | 最高价 | `daily_quote.high` |
| 最低 | float64 | 最低价 | `daily_quote.low` |
| 成交量 | int64 | 成交量（手）⚠️ 同个股 | `daily_quote.volume` (需 ×100) |
| 成交额 | float64 | 成交额（元） | `daily_quote.amount` |
| 振幅 | float64 | 振幅 (%) | — |
| 涨跌幅 | float64 | 涨跌幅 (%) | — |
| 涨跌额 | float64 | 涨跌额 | — |
| 换手率 | float64 | 换手率 (%) | `daily_quote.turnover` |

**⚠️ 与个股日线格式一致**，列名中文，成交量单位为手。

---

## 4. 基本面指标（PE/PB/总市值）

### 接口：`ak.stock_a_indicator_lg()`

**描述**：乐咕乐股-A股个股指标（PE/PB/股息率/总市值等历史序列）

**输入参数**：

| 参数 | 类型 | 必选 | 说明 |
|------|------|------|------|
| symbol | str | ✅ | 股票代码，如 `"000001"` |

**输出列（DataFrame）**：

| 列名 | 类型 | 说明 | 映射到 DB |
|------|------|------|----------|
| trade_date | object | 交易日期 | `stock_fundamentals.updated_at` |
| pe | float64 | 市盈率(静) | — |
| pe_ttm | float64 | 市盈率(TTM) | `stock_fundamentals.pe_ttm` |
| pb | float64 | 市净率 | `stock_fundamentals.pb_mrq` |
| ps | float64 | 市销率 | — |
| ps_ttm | float64 | 市销率(TTM) | — |
| dv_ratio | float64 | 股息率 (%) | — |
| dv_ttm | float64 | 股息率TTM (%) | — |
| total_mv | float64 | 总市值（万元）⚠️ | `stock_fundamentals.market_cap` (需 ×10000) |

**⚠️ 关键差异**：
- 总市值单位是「万元」，DB 存储单位是「元」→ **需要 ×10000**
- 返回的是时间序列（每日一行），取最新一行即可
- **不含 ROE / revenue_yoy / profit_yoy** → 这几个字段需要从其他接口获取或保留 baostock

### 补充接口：`ak.stock_zh_a_spot_em()`

**描述**：东方财富-A股实时行情（含动态市盈率），批量返回全部A股

**输出列（关键列）**：

| 列名 | 类型 | 映射到 DB |
|------|------|----------|
| 代码 | object | `stock_code` |
| 名称 | object | `stock_name` |
| 最新价 | float64 | — |
| 市盈率-动态 | float64 | `pe_ttm` (近似) |
| 总市值 | float64 | `market_cap` (单位：元) |
| 流通市值 | float64 | — |
| 换手率 | float64 | — |

**⚠️ 注意**：此接口返回的是实时/收盘快照，不是历史数据。适合一次性刷全量基本面。

---

## 5. A股股票列表

### 接口：`ak.stock_info_a_code_name()`

**描述**：获取所有 A 股代码和名称映射

**输出列（DataFrame）**：

| 列名 | 类型 | 说明 | 映射到 DB |
|------|------|------|----------|
| code | object | 股票代码 `"000001"` | `stock_master.stock_code` |
| name | object | 股票名称 `"平安银行"` | `stock_master.stock_name` |

**⚠️ 不含交易所/IPO日期/状态信息**。如需更详细信息，使用 `ak.stock_zh_a_spot_em()` 可获取全部 A 股的代码、名称、交易所信息。

---

## 6. 交易日历

### 接口：`ak.tool_trade_date_hist_sina()`

**描述**：新浪财经-交易日历（所有历史交易日列表）

**输出列（DataFrame）**：

| 列名 | 类型 | 说明 | 映射到 DB |
|------|------|------|----------|
| trade_date | object | 交易日期 `"2024-01-02"` | `trade_calendar.cal_date` |

**⚠️ 注意**：
- 只返回**交易日**列表（非交易日不在列表中），需自行补全非交易日标记
- baostock 返回的是完整日历（包含非交易日，用 `is_trading_day` 字段区分）

---

## 7. 健康检查方式

AKShare 无 login/logout 概念，也无专用健康检查 API。建议用轻量级调用测试：

```python
def check_akshare_health() -> bool:
    try:
        df = ak.stock_zh_a_hist(symbol="000001", period="daily",
                                start_date="20240101", end_date="20240102", adjust="")
        return len(df) > 0
    except Exception:
        return False
```

---

## 8. 注意事项

1. **请求频率**：无官方限制，但数据源为东方财富公开接口，建议 `time.sleep(0.5~1)` 间隔
2. **反爬风险**：短时间内大量请求可能触发东方财富反爬机制（返回空数据或连接超时）
3. **数据延迟**：盘后数据通常在 16:00-17:00 后更新完成
4. **异常处理**：网络超时、数据为空、接口变更都可能发生，需要 try/except 包裹
5. **DataFrame 为空**：AKShare 部分接口在无数据时返回空 DataFrame 而非抛异常
