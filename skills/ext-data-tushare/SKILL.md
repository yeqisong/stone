---
name: ext-data-tushare
title: 扩展数据（Tushare 直查）
description: 查询本地库没有的数据域——宏观经济（CPI/PPI/GDP/M0M1M2/SHIBOR）、上市公司三大报表（利润表/现金流量表/资产负债表）、可转债、基金净值、指数每日估值。用户问宏观环境、财报原始数据、可转债、ETF 净值时使用。
---

# 扩展数据（Tushare 直查）

## 何时用
本地数据库**没有**这些数据域，必须走 Tushare：

| 数据域 | 本地库状态 | 用这个技能 |
|---|---|---|
| 宏观：CPI/PPI/GDP/M0M1M2/SHIBOR | 无 | ✓ |
| 三大报表：income / cashflow / balancesheet | 无（fina_indicator 只有派生指标） | ✓ |
| 可转债：cb_basic / cb_price | 无 | ✓ |
| 基金净值：fund_nav | 部分（ETF 只有行情） | ✓ |
| 指数每日估值：index_dailybasic（PE/PB/成交额） | 无 | ✓ |

**已有数据不要走 Tushare**（行情/资金流/两融/龙虎榜/新闻/雪球/信号等一律用 query_sql 查本地库）——避免绕过配额计数与本地口径。

## 环境（已验证）
- 沙箱 Python 与后端同 venv，**tushare 已安装**，无需 pip install；
- Token 在环境变量 `TUSHARE_TOKEN`，直接 `os.environ['TUSHARE_TOKEN']`；
- 账号为 **2000 积分**，实测可用接口见下；sw_daily（申万指数）**无权限**，不要调。

## 代码模板
```python
import os, tushare as ts
pro = ts.pro_api(os.environ['TUSHARE_TOKEN'])

# 宏观（接口名是 cn_ 前缀）
pro.cn_cpi(start_m='202601', end_m='202610')      # 同月同比 nt_yoy
pro.cn_ppi(start_m='202601', end_m='202610')
pro.cn_gdp(start_q='2026Q1', end_q='2026Q3')
pro.cn_m(start_m='202601', end_m='202610')        # m0/m1/m2 + *_yoy
pro.shibor(date='20261030')                        # 日频 shibor on/1w/1m/1y

# 指数每日估值（2000 积分可用）
pro.index_dailybasic(ts_code='000300.SH', start_date='20260101', end_date='20261031')
# 字段：pe, pe_ttm, pb, total_mv, float_mv, turnover_rate, volume, amount

# 三大报表（注意 period 格式 YYYYMMDD，季末）
pro.income(ts_code='600519.SH', period='20260930')
pro.cashflow(ts_code='600519.SH', period='20260930')
pro.balancesheet(ts_code='600519.SH', period='20260930')

# 可转债（全量 1178 只）
pro.cb_basic()
pro.cb_price(ts_code='113050.SH', start_date='20261001', end_date='20261031')

# 基金净值
pro.fund_nav(ts_code='510300.SH', end_date='20261030')
```

## 使用规范
1. 一次查询控制窗口（如宏观按月 2-3 年、财报按单季），避免拉全量；
2. 把结果只需返回结论 + 关键行；需要复用就 `df.to_csv('ext_xxx.csv')` 存到会话工作区（跨轮次保留）；
3. Tushare 调用走**独立配额**，不要与本地采集混用；单轮对话总调用 ≤10 次；
4. 数字必须标注：数据来源 Tushare、区间、单位（宏观：亿元/%；报表：元）；
5. 与本地数据对照时说明口径差异（例如本地 close 是真实价、Tushare 复权口径不同）。

## 输出格式
结论 → 关键数据表（时间、指标、值、同比）→ 口径与来源说明。
