# 模型训练与回测模块 — 开发设计文档 v2.0

> 版本：v2.0 | 日期：2026-07-08 | 状态：设计中

---

## 1. 模块定位

模型训练与回测模块是K道量化平台的**"策略研发中心"**，独立于DAG流程编排，提供从特征选择、模型训练、策略优化、综合评估到上线部署的完整模型生命周期管理。

**核心设计理念**：**版本 = 模型文件 + 策略配置快照**。模型（预测能力）和策略（执行规则）是并列的两个独立组件，版本管理同时锁定两者。

---

## 2. 设计原则

| 原则 | 说明 |
|------|------|
| **版本即快照** | 每个版本完整记录特征配置、模型超参数、训练数据范围、五层策略配置 |
| **预测与执行分离** | 模型只负责预测；策略负责信号过滤、头寸管理、止盈止损、执行模型 |
| **四层评估体系** | 纯模型基线 → 纯策略基线 → 执行损耗归因 → 实际组合评估 |
| **策略参数可搜索** | 支持网格扫描，在验证集上自动搜索最优交易规则 |
| **回测可诊断** | 每笔交易记录完整上下文（仓位、资金、信号、执行价格） |

---

## 3. 版本状态机

```
config_ready → training → trained → strategy_optimized → evaluated → approved → online → offline
(配置就绪)     (训练中)    (已训练)   (策略已优化)        (已评估)    (已审批)   (已上线)  (已下线)
```

---

## 4. 五层策略配置体系 (`trading_rules`)

### 第一层：执行模型（Execution）— 物理底座

| 参数 | 默认 | 搜索 | 说明 |
|------|------|------|------|
| `price_type` | next_day_open | ❌ | T+1开盘价执行 |
| `order_type` | market_order | ❌ | 市价单 |
| `delay_days` | 1 | ❌ | T+1制度 |
| `volume_limit` | 0.10 | ❌ | 单笔不超成交量10% |

### 第二层：信号过滤（Signal Filter）— 安检门

| 参数 | 默认 | 搜索 | 说明 |
|------|------|------|------|
| `min_score_threshold` | 0.5 | ✅ | 最低预测分阈值 |
| `max_score_threshold` | 0.95 | ✅ | 最高预测分阈值 |
| `exclude_new_stocks_days` | 60 | ❌ | 新股过滤 |
| `allow_limit_up` | false | ❌ | 涨停板过滤 |

### 第三层：头寸管理（Position Sizing）— 买多少

| 参数 | 默认 | 搜索 | 说明 |
|------|------|------|------|
| `sizing_method` | equal_weight | ✅ | 仓位分配方式 |
| `max_single_position` | 0.20 | ✅ | 单票最大仓位 |
| `max_industry_exposure` | 0.40 | ✅ | 单行业最大暴露 |
| `max_turnover_per_day` | 0.30 | ✅ | 日最大换手率 |

### 第四层：止盈止损（Risk Management）— 怎么卖

| 参数 | 默认 | 搜索 | 说明 |
|------|------|------|------|
| `stop_loss_type` | percentage | ✅ | 百分比/ATR |
| `stop_loss` | -0.05 | ✅核心 | 止损阈值 |
| `take_profit` | 0.10 | ✅核心 | 止盈阈值 |
| `trailing_retracement` | 0.05 | ✅核心 | 移动止盈回撤 |
| `max_holding_days` | 20 | ✅ | 最大持仓天数 |

### 第五层：市场择时（Market Filter）— 做不做

| 参数 | 默认 | 搜索 | 说明 |
|------|------|------|------|
| `require_market_above_ma` | true | ✅ | 大盘站上均线才开仓 |
| `market_ma_period` | 20 | ✅ | 大盘均线周期 |
| `max_volatility_threshold` | 0.30 | ✅ | 波动率过高暂停 |

### 第六层：成本模型（Cost）— 锁定

| 参数 | 默认 | 说明 |
|------|------|------|
| `commission_rate` | 0.0015 | 手续费 |
| `slippage_rate` | 0.001 | 滑点 |
| `stamp_duty` | 0.0005 | 印花税 |

---

## 5. 四层评估体系

### 基线一：纯模型基线（Alpha纯度）
- 当日收盘价成交，无止盈止损，每日调仓，无交易限制
- 若夏普<1.0 → 模型质量堪忧，回退特征工程

### 基线二：纯策略基线（规则韧性）
- 随机信号替代模型预测，保留真实止盈止损规则
- 若收益为正 → 策略有适应性；大幅亏损 → 策略依赖模型准确率

### 归因：执行损耗
- 理想化(当日收盘) vs 实盘模拟(次日开盘) → 量化真实损耗

### 实际组合评估
- 模型 + 完整策略，次日开盘价 → 最终上线依据

### Brinson归因
- 选股贡献 = 理想化收益 - 基准收益(沪深300)
- 策略贡献 = 实际收益 - 理想化收益
- 交互收益 = 实际 - 选股 - 策略 - 基准

---

## 6. 策略参数网格搜索

1. 用户定义搜索空间 → 笛卡尔积计算组合数
2. 模型.pkl只加载1次，预测值缓存，纯逻辑重放
3. 热力图展示（X=止盈, Y=止损, 颜色=夏普）
4. 一键应用最优参数到 `trading_rules`

**防过拟合**：搜索严格限定验证集，平台区域检测（尖峰=过拟合风险）

---

## 7. 数据库表结构

### model_versions 新增字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `train_params` | JSONB | 训练参数快照 |
| `trading_rules` | JSONB | 五层策略配置 |
| `strategy_scan_results` | JSONB | 全部扫描结果 |
| `test_performance` | JSONB | 测试集性能 |
| `stage` | VARCHAR(16) | 状态机阶段 |
| `baseline_metrics` | JSONB | 纯模型基线指标 |
| `attribution_analysis` | JSONB | Brinson归因 |

### strategy_scans 表（新）

策略参数扫描记录，含每种组合的 sharpe/max_dd/annual_return。

### backtest_records 表（新）

回测记录，含 mode(baseline_model/baseline_strategy/full_strategy) + metrics + trade_summary。

---

## 8. 核心 API

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/models/versions/{id}/train` | 启动训练 |
| POST | `/api/models/versions/{id}/strategy-scan` | 策略参数扫描 |
| GET | `/api/models/versions/{id}/strategy-scan/{task_id}` | 扫描进度+热力图 |
| POST | `/api/models/versions/{id}/strategy-scan/apply` | 应用最优参数 |
| POST | `/api/models/versions/{id}/backtest` | 执行回测(mode: baseline/full) |
| POST | `/api/models/versions/{id}/evaluate` | 四层评估 |
| POST | `/api/models/versions/{id}/attribution` | 归因分析 |
| POST | `/api/models/versions/{id}/approve` | 审批通过 |
| POST | `/api/models/versions/{id}/online` | 上线 |

---
