# 交易日历统计模块 — 设计文档

> 版本：v1.1 | 状态：已实现 | 2026-06-22

---

## 一、现状问题

| 问题 | 说明 |
|------|------|
| 百分比只看个股 | `stock_rows / baseline * 100`，指数/ETF/基本面数据虽然在 `daily_completeness` 中但百分比计算完全不看 |
| baseline 不稳定 | 中位数兜底 6000，不同月份中位数漂移，百分比含义模糊 |
| 历史分母不可知 | `stock_master` 只有当前快照，无法知道 2020 年某天有多少股票已上市 |
| 日历单元格信息量少 | 只显示百分比，看不到当日具体个股数 |

---

## 二、设计方案

### 2.1 核心指标

每日计算 4 个子百分比，最终百分比 = 4 项算术平均：

| 子项 | 分子 | 分母 |
|------|------|------|
| 个股 % | `daily_quote` 当日有数据的股票数 | 当日已上市的股票总数 |
| 指数 % | `index_daily_quote` 当日有数据的指数数 | 当日已上市的指数总数 |
| ETF % | `daily_quote` 当日有数据的 ETF 数 | 当日已上市的 ETF 总数 |
| 基本面 % | 当日所属季度有 `stock_fundamentals` 记录的股票数 | 当日已上市股票数（简化，后续迭代细化） |

**综合百分比** = `(个股% + 指数% + ETF% + 基本面%) / 4`

> 分母为 0 时该项不参与平均。

### 2.2 分母计算

分母查询 `stock_master.ipo_date`：

```sql
SELECT COUNT(*) FROM stock_master 
WHERE stock_type='stock' AND status='N' AND ipo_date <= :date
```

### 2.3 `daily_completeness` 表结构

新增 4 个分母列：

| 列 | 说明 |
|----|------|
| stock_rows | 当日有 K 线数据的股票数 |
| index_rows | 当日有 K 线数据的指数数 |
| etf_rows | 当日有 K 线数据的 ETF 数 |
| fund_rows | 当日有基本面数据的股票数 |
| stock_baseline | 当日已上市股票总数 |
| index_baseline | 当日已上市指数总数 |
| etf_baseline | 当日已上市 ETF 总数 |
| fund_baseline | 当日已上市股票数（与 stock_baseline 同） |

### 2.4 日历统计补数

已有「📅 日历统计」按钮。执行逻辑改为：

```
对每个交易日:
  1. 查询当日分子（4 个 COUNT）
  2. 查询当日分母（4 个 baseline）
  3. INSERT INTO daily_completeness ON CONFLICT UPDATE 全部 8 列
```

断点续传：`daily_completeness` 已有该日期且 `stock_baseline > 0` 则跳过。

### 2.5 每日定时任务

DAG `daily_completeness` 节点：每天 17:35 自动计算当日数据，多写入 4 个 baseline 列。

### 2.6 前端日历单元格

```
 15
5780 只
 95%
```

- 第一行：日期数字
- 第二行：个股数（`5780 只`，0 只不显示）
- 第三行：综合百分比

颜色规则不变：≥80% 绿、50-80% 黄、<50% 红。

### 2.7 百分比计算（后端，不在 API 中实时算）

```python
stock_pct = stock_rows / stock_baseline * 100 if stock_baseline > 0 else 0
index_pct = index_rows / index_baseline * 100 if index_baseline > 0 else 0
etf_pct   = etf_rows   / etf_baseline   * 100 if etf_baseline > 0   else 0
fund_pct  = fund_rows  / fund_baseline  * 100 if fund_baseline > 0  else 0

parts = []
if stock_baseline > 0: parts.append(stock_pct)
if index_baseline > 0: parts.append(index_pct)
if etf_baseline > 0:   parts.append(etf_pct)
if fund_baseline > 0:  parts.append(fund_pct)
pct = sum(parts) / len(parts) if parts else 0
```

---

## 三、可行性评估

| 维度 | 评估 |
|------|------|
| 分母查询 | `stock_master` ~7700 行，`ipo_date` 索引，单次 ~1ms。1500 天多 6000 次查询 ≈ 6s，可接受 |
| 表结构 | 加 4 列，`ALTER TABLE ADD COLUMN IF NOT EXISTS` 幂等，无风险 |
| 历史兼容 | 老日期 baseline=NULL → 显示 0%，用户跑一次日历补数即修复 |
| 风险 | `ipo_date` 可能 NULL → 这些股票不参与分母，影响 2000 年前后日期；基本面分母简化用 stock_baseline |

### 暂不做

- 日历单元格 tooltip 展示 4 个子百分比明细（信息过载，后续迭代）
- 基本面分母细化到"当季度已出财报股票数"（需额外逻辑，初期简化）

---

## 四、修复记录

| 版本 | 问题 | 修复 |
|------|------|------|
| v2.30 | baseline 列不存在 | `ALTER TABLE ADD COLUMN IF NOT EXISTS` 迁移 |
| v2.31 | stock 分子含 ETF，百分比超 100% | `LEFT` 过滤排除 ETF 代码前缀 |
| v2.31 | baseline 全部为 0 | `dag_task_completeness` + `_run_calendar_backfill` 增加分母查询 |
| v2.32 | `stock_master.ipo_date` 全部 NULL → baseline 恒 0 | K 线补数时 `_sync_ipo_dates()` 同步 IPO 日期 |
