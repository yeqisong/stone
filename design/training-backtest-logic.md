# 模型训练与回测 — 完整设计方案

> v2.0 | 2026-06-23 | 18 点重构计划

---

## 一、现状问题汇总

| # | 问题 | 严重 |
|---|------|:---:|
| 1 | 验证集复用——Optuna 调参和最终评估用同一份数据 | 🔴 |
| 2 | 回测无资金约束——每日 50 只收益无限叠加 | 🔴 |
| 3 | 回撤 100%——`_backtest` 从未执行止损逻辑 | 🔴 |
| 4 | 标签用 MA20 做分母——模型学乖离率而非涨跌幅 | 🟡 |
| 5 | 50 轮 Optuna 全返回相同夏普——参数不敏感 | 🟡 |
| 6 | 缺资金曲线图 / 交易明细——人工无法审计 | 🟡 |

---

## 二、18 点重构清单

### 模块一：数据层

| # | 改造 | 实现 |
|---|------|------|
| M1-1 | 强制特征对齐 `how='inner'`，行数不一致直接报错 | `pd.merge` 加 `validate='1:1'` |
| M1-2 | 检查 `vol_ratio_3d` / `obv_slope_7d` 是否有前视偏差 | 代码审计，有问题全改 `shift(1)` / `rolling()` |
| M1-3 | 训练前断言 `max(trade_date) <= 当前日期 - 2 天` | 否则抛 `ValueError` 终止 |

### 模块二：标签与 PnL 同源

| # | 改造 | 实现 |
|---|------|------|
| M2-4 | 标签改为 `(N日后 close_hfq / 当日 close_hfq) - 1` | 放弃 MA20 锚点 |
| M2-5 | 标签 winsorize(1%, 99%) + Z-Score | `scipy.stats.mstats.winsorize` |

### 模块三：三重时间切分

| # | 改造 | 实现 |
|---|------|------|
| M3-6 | 60/20/20 三段：train / val / test | 三路 mask |
| M3-7 | Optuna 禁止访问 test 集 | 函数闭包隔离 |

### 模块四：回测引擎与资金管理

| # | 改造 | 实现 |
|---|------|------|
| M4-8 | 从 config 读 `initial_cash`（默认 1,000,000） | `cfg['initial_cash']` |
| M4-9 | 持仓数 `max_positions`，资金等权分配 | `cash_per = equity / max_positions` |
| M4-10 | 逐日检查止损 `当前价 <= 买入价 × (1 - stop_loss_pct)` | 触发立即平仓 |
| M4-11 | A 股规则：100 股整手，T+1（次日可卖） | `int(shares/100)*100` |
| M4-12 | 逐日记录权益序列 `daily_equity` | 用于画曲线 |

### 模块五：Optuna 规范化

| # | 改造 | 实现 |
|---|------|------|
| M5-13 | `n_trials` 从 config 读取（默认 50） | `cfg['optuna_trials']` |
| M5-14 | `objective()` 加 `-np.inf` 防护 | NaN 预测返回 `-np.inf` |
| M5-15 | XGBoost `early_stopping_rounds=20` + `eval_set` | 从 train 再切 10% 做 early stop |
| M5-16 | 搜索 `reg_alpha` (L1) + `reg_lambda` (L2) | Optuna suggest_float |

### 模块六：存储与可视化

| # | 改造 | 实现 |
|---|------|------|
| M6-17 | 回测结束时生成资金曲线 PNG | `matplotlib` 保存到 `data/models/{v}/equity_curve.png` |
| M6-18 | 输出交易明细 CSV | `data/models/{v}/trade_details.csv` |

---

## 三、新流程伪代码

```
dag_task_model_train(config):
│
├─ 加载指标 → M1-1 对齐校验 → M1-3 新鲜度断言
├─ 特征工程 → M1-2 审计无前视偏差
├─ 标签计算 → M2-4 复权收益率 → M2-5 winsorize+ZScore
│
├─ M3-6 三重切分: train(60%) / val(20%) / test(20%)
│
├─ M5-13 Optuna(n_trials):
│      每轮:
│      ├─ 采样: lr, depth, n_est, subsample, colsample, reg_alpha, reg_lambda
│      ├─ XGBoost(early_stopping, eval_set=train内10%)
│      ├─ M3-7 在 val 集预测 → M4 回测引擎:
│      │   ├─ M4-8 初始资金
│      │   ├─ M4-9/11 等权持仓 + 整手买入
│      │   ├─ M4-10 逐日止损检查
│      │   └─ M4-12 权益序列 → 夏普
│      └─ M5-14 防 NaN
│
├─ 最优模型在 test 集跑一次回测(M3-7) → 最终指标
│
├─ 写入 model_versions(sharpe/win_rate/max_dd/...)
│
└─ M6-17/18 生成资金曲线 PNG + 交易明细 CSV
```

---

## 四、配置项清单（模型可编辑）

| 字段 | 默认 | 说明 |
|------|------|------|
| `train_start` | 2021-01-01 | 训练数据起点 |
| `train_end` | 2025-12-31 | 训练数据终点 |
| `test_start` | 2026-01-01 | 评估起点（仅展示用） |
| `optuna_trials` | 50 | Optuna 搜索轮数 |
| `initial_cash` | 1,000,000 | 回测初始资金（元） |
| `max_positions` | 5 | 同时最大持仓数 |
| `stop_loss_pct` | 8 | 止损比例（%） |
| `search_space` | {...} | 超参搜索范围 |
