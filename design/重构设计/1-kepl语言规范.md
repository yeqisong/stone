# KEPL 语言规范文档 v1.0（最终定稿版）
## Keda Expression & Programming Language（K道表达式语言）

---

### 文档说明

本规范是 **K道量化平台** 特征公式编辑器的官方语言定义文档。它统一了前端的语法校验规则、后端的表达式解析逻辑以及底层的物理存储映射标准。开发人员应以此文档为最终依据，实现特征管理模块的全部功能。

> **版本历史**：v1.0 基于2026-06-26最终讨论定稿，整合了实体绑定、`current`上下文、显式集合引用、横截面聚合与全局特征存储等全部核心设计。

---

## 第一章：设计哲学与核心概念

### 1.1 设计哲学（三大原则）

1. **零歧义原则**：公式中的每一个符号都只有唯一确定的语义指向，系统绝不猜测用户意图。
2. **上下文显式化原则**：通过 `current` 关键字显式声明“当前行”，通过数据源前缀（如 `stock.`）显式声明“全量集合”。
3. **物理逻辑分离原则**：语法层面描述“算什么”，物理存储层面解决“存哪里”，两者通过实体（Entity）机制解耦。

### 1.2 核心概念速览

| 概念 | 说明 | 示例 |
| :--- | :--- | :--- |
| **主实体（Primary Entity）** | 特征计算时最外层遍历的对象。由用户在注册特征时从下拉列表中选定。 | `stock`、`etf`、`index`、`global` |
| **当前行上下文（Current Context）** | 系统级变量 `current`，指向主实体在当前遍历循环中的**单一行**。 | `current.close` 表示当前这只股票的收盘价；`current.industry_code` 表示当前股票的行业代码 |
| **数据源集合（Dataset Collection）** | 用数据源别名（如 `stock`、`index`）表示该数据源下的**全体数据**（向量）。 | `stock.close` 表示所有股票的收盘价列表 |
| **外部实体引用（External Reference）** | 在遍历主实体的同时，引入其他数据源的辅助数据。 | 固定个体：`index['000300.SH'].close`；动态映射：`index[current.industry_code].close` |

---

## 第二章：数据源注册表（System Registry）

系统预置以下数据源，所有数据源均需在 **数据源管理模块** 中提前注册方可使用。

| 别名 | 物理含义 | 关键可用字段（示例） | 更新频率 | 主键标识列 |
| :--- | :--- | :--- | :--- | :--- |
| `stock` | A股个股日行情 | `open, high, low, close, volume, amount, industry_code, market_cap` | 日频 | `stock_code` |
| `etf` | ETF基金日行情 | `open, high, low, close, volume, iopv, premium_rate` | 日频 | `etf_code` |
| `index` | 指数日行情 | `open, high, low, close, volume` | 日频 | `index_code` |
| `financial` | 财务报告数据（季报/年报） | `pe, pb, roe, eps, revenue_growth, report_date` | 季频 | `stock_code` |
| `macro` | 宏观经济指标 | `cpi, ppi, pmi, m2, publish_date` | 月频 | （无） |
| `north` | 北向资金（陆股通） | `hold_volume, hold_ratio, net_inflow` | 日频 | `stock_code` |

---

## 第三章：语法元素详解（Syntax Elements）

### 3.1 字段引用寻址规则（Addressing Rules）

| 写法 | 指向对象 | 数据类型 | 合法性条件 | 使用场景 |
| :--- | :--- | :--- | :--- | :--- |
| **裸字段**（如 `close`） | 当前遍历主实体的该字段（等价于 `current.close`） | **标量**（单个数值） | 仅当特征已绑定非`global`实体时合法 | 常规运算、时间序列函数入参 |
| **显式当前行**（`current.字段`） | 当前遍历主实体的该字段 | **标量** | 任何实体类型（`global`除外）均合法 | 在动态映射 `[...]` 中作为索引键时**必须**使用 |
| **数据源集合**（`stock.close`） | 该数据源下所有标的的该字段 | **向量**（一列数据） | 仅作为横截面聚合函数的参数 | `avg(stock.close)`、`rank(stock.volume)` |
| **外部个体固定引用**（`index['000300.SH'].close`） | 该数据源中指定唯一标的的字段值 | **标量** | 中括号内必须是字符串常量 | 引用固定基准（如沪深300） |
| **外部动态映射引用**（`index[current.industry_code].close`） | 根据当前行属性动态匹配外部标的 | **标量** | 中括号内必须是 `current.属性名` | 个股对齐所属行业指数 |
| **非法裸露引用**（`index.close`） | — | — | **系统拦截** | 无，系统报错要求添加 `['代码']` 或聚合函数 |

### 3.2 横截面聚合函数（Cross-Sectional Aggregation）

用于对**全量数据集**进行统计运算。参数**必须**是 `数据源别名.字段名` 形式，支持可选的 `exclude_self` 参数（默认为 `True`，即计算时排除当前行自身，避免大权重股自相关偏差）。

| 函数名 | 语法示例 | 说明 | 可选参数 |
| :--- | :--- | :--- | :--- |
| `avg` | `avg(stock.close, exclude_self=True)` | 全市场收盘价均值（默认排除自己） | `exclude_self` |
| `sum` | `sum(stock.volume, exclude_self=True)` | 全市场成交量总和（默认排除自己） | `exclude_self` |
| `std` | `std(stock.pe)` | 全市场市盈率标准差 | 无 |
| `max` | `max(stock.high)` | 全市场当日最高价中的最大值 | 无 |
| `min` | `min(stock.low)` | 全市场当日最低价中的最小值 | 无 |
| `rank` | `rank(stock.volume, pct=True, exclude_self=True)` | 全市场成交量排名百分位（0~1） | `pct`（默认False）、`exclude_self`（默认True） |
| `quantile` | `quantile(stock.pe, q=0.8)` | 全市场市盈率的80%分位数值 | `q`（0~1） |

### 3.3 系统内置时间序列函数（Time-Series Functions）

用于对**单一行**在**时间维度**上的历史数据进行滚动计算。参数**必须**是裸字段或 `current.字段名`。

| 函数名 | 语法示例 | 说明 | 自动回溯窗口 |
| :--- | :--- | :--- | :--- |
| `ma` | `ma(close, 5)` | N周期简单移动平均 | N |
| `ema` | `ema(close, 12)` | N周期指数移动平均 | N |
| `rsi` | `rsi(close, 14)` | N周期相对强弱指标 | N+1 |
| `std` | `std(close, 20)` | N周期标准差（波动率） | N |
| `corr` | `corr(close, volume, 10)` | 两字段在N周期内的相关性 | N |
| `atr` | `atr(high, low, close, 14)` | N周期平均真实波幅 | N |
| `boll_upper` | `boll_upper(close, 20, 2)` | 布林带上轨（均线 + K倍标准差） | N |
| `boll_lower` | `boll_lower(close, 20, 2)` | 布林带下轨（均线 - K倍标准差） | N |

### 3.4 自定义函数（User-Defined Functions）

用户在“函数管理模块”中注册的函数，与系统内置函数同权调用。

- **调用方式**：`函数名(参数1, 参数2, ...)`
- **参数约束**：必须为字段引用、数字常量或简单四则运算
- **声明要求**：注册时需声明该函数的默认回溯窗口（`lookback`），供DAG拉取数据使用

### 3.5 运算符与优先级

支持标准四则运算与逻辑比较，优先级遵循常规数学规则，括号 `()` 可强制改变优先级。

---

## 第四章：实体（Entity）系统与物理存储映射

### 4.1 系统支持的实体类型

| 实体别名 | 中文名称 | 遍历范围 | 典型应用场景 | 每天产生数据行数 |
| :--- | :--- | :--- | :--- | :--- |
| `stock` | 个股 | 全市场A股（5000+只） | 选股因子、买卖信号 | ~5000行 |
| `etf` | ETF基金 | 全市场ETF | ETF轮动、折溢价套利 | ~100行 |
| `index` | 指数 | 全市场指数（沪深300等） | 大盘择时、行业基准 | ~30行 |
| `global` | 全局（无标的） | **不遍历，仅计算一次** | 宏观择时、情绪温度、仓位系数 | **仅1行** |

### 4.2 物理存储方案（按实体分表）

系统**不为所有实体共用一张宽表**，而是为每个实体独立创建一张物理特征表，彻底杜绝稀疏NULL值浪费：

| 实体类型 | 物理表名 | 主键（联合主键） | 存储内容 |
| :--- | :--- | :--- | :--- |
| `stock` | `feature_stock` | `(trade_date, stock_code)` | 所有绑定 `stock` 实体的特征列（如 `ma5`, `rsi`） |
| `etf` | `feature_etf` | `(trade_date, etf_code)` | 所有绑定 `etf` 实体的特征列 |
| `index` | `feature_index` | `(trade_date, index_code)` | 所有绑定 `index` 实体的特征列 |
| `global` | `feature_global` | **`(trade_date)`** （无代码列！） | 所有绑定 `global` 实体的特征列（每天且仅有一行） |

### 4.3 全局（Global）特征的广播机制

当训练一个基于 `stock` 实体的模型，且特征公式中引用了全局特征（如 `global_market_sentiment`）时，DAG引擎在构建训练集时自动执行以下逻辑关联：

```sql
SELECT 
    s.trade_date,
    s.stock_code,
    s.ma5,                          -- 来自 feature_stock
    s.rsi,                          -- 来自 feature_stock
    g.market_sentiment,             -- 来自 feature_global（该值被广播到每一行）
    g.pmi_factor                    -- 来自 feature_global
FROM feature_stock s
LEFT JOIN feature_global g 
    ON s.trade_date = g.trade_date  -- 仅按日期关联，无代码条件
```

**结论**：`global` 特征在物理上只存1行/天，但在逻辑查询时被“广播”到当天所有个股的行上。这一机制对公式编写者完全透明。

### 4.4 实体选择对公式语法的硬性约束

| 选择的实体 | `current` 是否可用？ | 裸字段（如 `close`）是否合法？ | 典型合法公式 |
| :--- | :--- | :--- | :--- |
| `stock` / `etf` / `index` | ✅ 可用 | ✅ 合法（指向当前标的） | `close / ma(close, 20)` |
| `global` | ❌ 不可用（无遍历行） | ❌ **非法**（系统拦截） | `ma(index['000300.SH'].close, 20)` 或 `avg(stock.close)` |

---

## 第五章：错误处理规范（Error Handling）

系统必须对以下非法写法进行明确拦截并返回中文提示：

| 错误类型 | 错误写法示例 | 系统报错信息 |
| :--- | :--- | :--- |
| **裸露外部数据源** | `index.close` | “数据源‘index’是集合类型，请使用 `['代码']` 指定个体，或使用 `avg(index.close)` 进行聚合。” |
| **聚合函数参数错误** | `avg(close)` | “聚合函数 `avg` 需要接收数据源集合形式（如 `stock.close`），请检查参数。” |
| **时序函数参数错误** | `ma(stock.close, 5)` | “时间序列函数 `ma` 不能接收集合 `stock.close`，请使用当前行字段 `close`。” |
| **动态映射缺少 `current`** | `index[industry_code].close` | “动态映射中括号内请使用 `current.属性名` 格式，如 `index[current.industry_code].close`。” |
| **全局实体使用裸字段** | 实体选`global`，公式写 `close` | “全局（Global）实体不存在当前行，公式中禁止使用裸字段，请显式指定数据源。” |
| **未注册数据源/函数** | `crypto.close` 或 `my_func(close)` | “数据源/函数 ‘xxx’ 未在系统中注册，请检查拼写或前往管理模块注册。” |

---

## 第六章：完整示例集（Comprehensive Examples）

### 6.1 基础量价特征（目标实体：`stock`）

| 特征名称 | KEPL 公式 | 说明 |
| :--- | :--- | :--- |
| 5日均线偏离度 | `close / ma(close, 5) - 1` | 当前价格相对5日均线的偏离百分比 |
| 成交量异动比 | `volume / ma(volume, 20)` | 当日成交量相对20日均量的倍数 |
| RSI超买超卖 | `(rsi(close, 14) - 50) / 50` | RSI偏离中轴的幅度 |

### 6.2 跨数据源引用（目标实体：`stock`）

| 特征名称 | KEPL 公式 | 说明 |
| :--- | :--- | :--- |
| 个股/沪深300比值 | `close / index['000300.SH'].close` | 固定基准引用 |
| 个股/所属行业比值 | `close / index[current.industry_code].close` | **动态映射**（必须用 `current`） |
| ETF折溢价率 | 目标实体选 `etf`，公式 `close / iopv - 1` | ETF特有字段 |

### 6.3 横截面聚合（目标实体：`stock`）

| 特征名称 | KEPL 公式 | 说明 |
| :--- | :--- | :--- |
| 全市场估值分位 | `rank(stock.pe, pct=True, exclude_self=True)` | 该股PE在全市场中的百分位（排除自身） |
| 相对全市场平均强度 | `close / avg(stock.close, exclude_self=True)` | 自身价格 / 全市场平均价（排除自身） |
| 成交量集中度 | `volume / sum(stock.volume, exclude_self=True)` | 自身成交量占全市场总成交的比例（排除自身） |

### 6.4 全局特征（目标实体：`global`）

| 特征名称 | KEPL 公式 | 说明 |
| :--- | :--- | :--- |
| 沪深300趋势强度 | `index['000300.SH'].close / ma(index['000300.SH'].close, 20) - 1` | 指数相对自身均线的偏离 |
| 全市场情绪温度 | `avg(stock.close) / ma(avg(stock.close), 10) - 1` | 全市场平均价的短期动量 |
| 宏观仓位系数 | `(macro.pmi - 50) / 10` | 基于PMI的仓位调节因子（需处理发布日滞后） |

### 6.5 嵌套与复合表达式

| 特征名称 | 目标实体 | KEPL 公式 | 说明 |
| :--- | :--- | :--- | :--- |
| 行业相对全市场强度 | `stock` | `index[current.industry_code].close / avg(index.close)` | 该行业指数相对全市场指数均值的强弱 |
| 多空信号（金叉） | `stock` | `ma(close, 5) > ma(close, 20)` | 返回1（真）或0（假） |
| 多因子综合评分 | `stock` | `(close / ma(close, 20) - 1) * 0.5 + rank(stock.pe, pct=True) * 0.5` | 动量与估值因子加权组合 |

---

## 第七章：附录——语法速查卡（Quick Reference）

| 你想表达的意思 | KEPL 写法 | 关键注意点 |
| :--- | :--- | :--- |
| 当前股票的收盘价 | `close` | 裸字段 = 当前行（语法糖） |
| 当前股票的成交量 | `volume` | 同上 |
| 当前股票的5日均线 | `ma(close, 5)` | 时序函数入参用裸字段 |
| 所属行业指数收盘价 | `index[current.industry_code].close` | **必须**加 `current.` 作为索引键 |
| 固定基准沪深300 | `index['000300.SH'].close` | 固定字符串用单引号 |
| 全市场平均收盘价（排除自身） | `avg(stock.close, exclude_self=True)` | 聚合函数入参用数据源集合 |
| 全市场成交量排名百分比 | `rank(stock.volume, pct=True, exclude_self=True)` | 默认排除自身 |
| 全局宏观信号 | 实体选 `global`，公式 `ma(index['000300.SH'].close, 20)` | 全局模式下禁止裸字段 |
| **非法写法** | `index.close` | 系统拦截：缺少 `['代码']` 或聚合函数 |

---

*文档版本：v1.0（定稿版）*  
*发布日期：2026-06-26*  
*适用系统：K道量化平台 特征管理模块*  
*文档状态：✅ 已通过架构评审，可交付开发*