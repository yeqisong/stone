---
name: stock-deep-dive
title: 个股尽调
description: 对单只个股做结构化尽调——行情、估值、资金流、两融、龙虎榜、特征、信号、资讯与雪球情绪，输出带口径的结论。用户问「分析一下某只票 / 这票怎么样 / 能不能买」时使用。
---

# 个股尽调

## 何时用
用户给出一只股票（代码或名称），希望综合判断基本面、资金面、消息面与量化信号。

## 步骤（按顺序，缺数据就跳过并说明）

1. **确认标的**：`stock_overview(stock_code)` 一次拿全基础盘（行情/估值/行业/近期信号/分组/雪球情绪）。
   名称转代码：`SELECT stock_code, stock_name FROM stock_master WHERE stock_name LIKE '%关键词%' AND stock_type='stock' AND status='N'`。
2. **近期走势与量能**（近 60 交易日，后复权算收益、真实价展示）：
   ```sql
   SELECT trade_date, close, close_hfq, volume, turnover_rate
   FROM daily_quote
   WHERE stock_code = '600519' AND close > 0
     AND trade_date >= CURRENT_DATE - INTERVAL '120 days'
   ORDER BY trade_date
   ```
   计算：区间涨跌幅、最大回撤、20 日均量比、近 5 日是否放量。
3. **资金面**（近 20 日主力净流入，单位元）：
   ```sql
   SELECT trade_date, net_mf_amt,
          buy_elg_amt - sell_elg_amt AS elg_net
   FROM stock_moneyflow
   WHERE stock_code = '600519' AND trade_date >= CURRENT_DATE - INTERVAL '40 days'
   ORDER BY trade_date DESC
   ```
4. **杠杆与异动**：
   - 两融：`SELECT trade_date, fin_amount, sec_amount, total_amount FROM stock_margin_detail WHERE stock_code='600519' ORDER BY trade_date DESC LIMIT 20`
   - 龙虎榜：`SELECT trade_date, pct_chg, net_amount, reason FROM stock_top_list WHERE stock_code='600519' ORDER BY trade_date DESC LIMIT 10`
5. **估值分位**（当前 PE/PB 在近三年自身历史的相对位置）：
   ```sql
   SELECT COUNT(*) FILTER (WHERE pe_ttm <= (SELECT pe_ttm FROM stock_fundamentals WHERE stock_code='600519' ORDER BY trade_date DESC LIMIT 1))
          * 1.0 / NULLIF(COUNT(*), 0) AS pe_pctl
   FROM stock_fundamentals_history WHERE stock_code='600519' AND pe_ttm > 0
     AND report_date >= CURRENT_DATE - INTERVAL '3 years'
   ```
6. **消息面**：`search_news(keyword='公司简称')`，重点看 importance>=2 与 sentiment。
7. **量化信号**：`stock_overview` 已带近 5 条；需要更久用
   `SELECT signal_date, direction, strength, reason, model_version, forward_5d_return FROM signal_history WHERE stock_code='600519' ORDER BY signal_date DESC LIMIT 20`。
8. **特征画像**（可选，feature_values 必须限定日期）：
   ```sql
   SELECT feature_name, value FROM feature_values
   WHERE stock_code='600519' AND trade_date = (SELECT MAX(trade_date) FROM feature_values)
     AND feature_name IN ('ma_5','ma_20','rsi_14','vol_ratio','turnover_rate')
   ```

## 输出格式
1. **一句话结论**（偏多/偏空/观察 + 最关键理由）
2. 四张表：行情与量能 / 资金与杠杆 / 估值与基本面 / 消息与情绪
3. **风险清单**（3 条以内，注明触发条件）
4. **口径说明**：收益用 close_hfq；估值分位是自身历史；情绪为 LLM 30 天聚合

## 注意
- 停牌/零价行不在库里：区间用 `close > 0` 过滤，缺失日说明为停牌。
- 不要给出具体买卖点位承诺；A 股红涨绿跌只用于表述方向，不用于评价好坏。
- 数据缺失时明确写「无数据」，不要用 0 代替。
