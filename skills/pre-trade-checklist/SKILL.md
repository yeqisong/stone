---
name: pre-trade-checklist
title: 盘前纪律清单
description: 开盘前/下单前的检查清单——数据新鲜度、闸门状态、模型信号、持仓风险、消息面风险，逐项给通过/警告/阻断。用户问「今天能操作吗」「盘前要看什么」时使用。
---

# 盘前纪律清单

## 何时用
交易日前或下单前做系统化检查；也可在盘后复盘前确认「当时的决策环境」。

## 检查项（逐项 PASS / WARN / BLOCK）
1. **数据新鲜度**：最新交易日行情是否已入库、特征是否算到今天
   ```sql
   SELECT (SELECT MAX(trade_date)::text FROM daily_quote) AS quote_latest,
          (SELECT MAX(trade_date)::text FROM feature_values) AS feature_latest,
          (SELECT MAX(cal_date)::text FROM trade_calendar
           WHERE is_trade_day AND cal_date <= CURRENT_DATE) AS last_trade_day;
   ```
   —— quote_latest 应等于最近交易日；落后 1 天 → WARN，≥2 天 → BLOCK（禁止开新仓）
2. **市场闸门**：沪深300 vs MA20、当前档位（禁/放行）
   ```sql
   SELECT trade_date, close,
          AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20
   FROM index_daily_quote WHERE index_code='000300'
   ORDER BY trade_date DESC LIMIT 1;
   ```
3. **组合熔断**：净值距峰值回撤（≥10% → BLOCK 开新仓）
   ```sql
   WITH e AS (SELECT trade_date, equity FROM paper_trades WHERE action='EOD')
   SELECT trade_date, equity,
          ROUND((MAX(equity) OVER (ORDER BY trade_date)-equity)/NULLIF(MAX(equity) OVER (ORDER BY trade_date),0)*100,2) AS dd
   FROM e ORDER BY trade_date DESC LIMIT 1;
   ```
4. **模型信号**：最近一次信号日与数量、健康度
   ```sql
   SELECT MAX(signal_date)::text FROM signal_history WHERE strategy_name='model_signal';
   SELECT health_status, live_win_rate, rank_ic, check_date
   FROM model_health WHERE version=(SELECT version FROM model_versions WHERE status='ACTIVE' LIMIT 1)
   ORDER BY check_date DESC LIMIT 1;
   ```
   —— 最近信号 >3 个交易日且无新信号 → WARN（可能闸门禁买或节点异常）
5. **持仓风险**：接近止损线的持仓、行业集中度
   ```sql
   SELECT p.stock_code, p.stock_name, p.shares, p.buy_price,
          (SELECT close_hfq FROM daily_quote q WHERE q.stock_code=p.stock_code
           ORDER BY trade_date DESC LIMIT 1) AS last_hfq
   FROM paper_positions p;
   ```
6. **消息面风险**：当日高重要度负面资讯（importance≥2 且 sentiment=negative）
   ```sql
   SELECT n.title, s.importance, s.sentiment
   FROM v_news_feed n JOIN news_summary s ON s.news_id=n.id
   WHERE n.published_at > CURRENT_DATE - INTERVAL '1 day'
     AND s.importance >= 2 AND s.sentiment = 'negative'
   ORDER BY n.published_at DESC LIMIT 10;
   ```

## 输出格式
一张检查表（检查项 | 状态 | 关键数值 | 说明），最后一行总体结论：
- 全部 PASS → 「可正常执行计划」
- 任一 BLOCK → 「暂停开新仓」并写明触发项
- WARN → 「可执行但需注意 X」

## 注意
- 这是纪律清单不是择时建议；BLOCK 的含义是「按既定规则今天不该开新仓」
- 数据缺失本身也要作为检查结果（不要静默跳过为 PASS）
