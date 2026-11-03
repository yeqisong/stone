---
name: trade-postmortem
title: 交易复盘
description: 按信号/退出原因/持有期/预测分档复盘纸面与信号表现，定位亏损集中点。用户问「最近交易怎么样」「为什么亏」「胜率低在哪」时使用。
---

# 交易复盘

## 何时用
周期性复盘（周/月）、亏损归因、策略对比。与 signal-review 的分工：signal-review 看**信号质量**（前瞻收益），本技能看**交易执行结果**（纸面组合）。

## 复盘步骤
1. 已实现交易总览：
   ```sql
   SELECT action, COUNT(*) AS n, ROUND(SUM(pnl),0) AS pnl,
          ROUND(AVG(pnl) FILTER (WHERE pnl IS NOT NULL),0) AS avg_pnl
   FROM paper_trades WHERE action IN ('BUY','SELL')
     AND trade_date >= CURRENT_DATE - INTERVAL '30 days'
   GROUP BY action;
   ```
2. 按退出原因归因（reason 字段：signal/stop_loss/take_profit/trailing/hold_expire/regime）：
   ```sql
   SELECT reason, COUNT(*) AS n, ROUND(SUM(pnl),0) AS pnl,
          ROUND(AVG(pnl),0) AS avg_pnl, ROUND(AVG(hold_days),1) AS avg_days
   FROM (SELECT reason, pnl,
                trade_date - LAG(trade_date) OVER (PARTITION BY stock_code ORDER BY id)::int AS hold_days
         FROM paper_trades WHERE action='SELL') t
   GROUP BY reason ORDER BY pnl;
   ```
3. 持仓期与成本拖累：
   ```sql
   SELECT ROUND(AVG(commission),2) AS avg_fee, ROUND(SUM(commission),0) AS total_fee
   FROM paper_trades WHERE action IN ('BUY','SELL')
     AND trade_date >= CURRENT_DATE - INTERVAL '30 days';
   ```
4. 单笔最大亏损 Top5 与共同特征（行业/信号分档）：
   ```sql
   SELECT t.stock_code, t.stock_name, t.pnl, t.reason, t.trade_date,
          sm.industry_l1
   FROM paper_trades t LEFT JOIN stock_master sm ON sm.stock_code=t.stock_code
   WHERE t.action='SELL' AND t.pnl IS NOT NULL
   ORDER BY t.pnl ASC LIMIT 5;
   ```
5. 对照信号：同期信号的前瞻胜率（forward_5d）——
   ```sql
   SELECT COUNT(*) FILTER (WHERE forward_5d_return > 0) * 100.0 / NULLIF(COUNT(*),0) AS win_5d
   FROM signal_history
   WHERE strategy_name='model_signal' AND direction='buy'
     AND signal_date >= CURRENT_DATE - INTERVAL '30 days'
     AND forward_5d_return IS NOT NULL;
   ```

## 输出格式
1. 结论：本期盈亏、主要亏损来源（一条）
2. 归因表：退出原因 × 笔数 × 盈亏 × 平均持有
3. Top 亏损单与共性（行业/信号分档）
4. 与信号层对照：交易结果 vs 信号前瞻胜率，判断问题在「选股」还是「执行/风控」
5. 一条可执行的改进项（如「止损线过紧」「某行业连续亏损→设行业上限」）

## 注意
- 样本 <30 笔时明确说「样本不足，结论仅供参考」
- pnl 含佣金与印花税；不要与信号层的 gross 前向收益直接相加比较
- 复盘只给事实与归因，不给未来买卖建议
