---
name: drawdown-discipline
title: 回撤熔断纪律
description: 检查组合当前回撤与熔断闸门状态，按纪律给出停止开仓/减仓建议。用户问「还能不能开新仓」「要不要减仓」「组合风险」时使用。
---

# 回撤熔断纪律

## 何时用
评估组合当前是否触发风控：组合回撤熔断、单票止损、行业上限、市场闸门。

## 平台现有规则（与纸面/实盘同源）
- **组合熔断**：净值较峰值回撤 ≥10% → 停止开新仓（已有持仓按止损/到期退出）
- **市场闸门**：沪深300 收盘 < MA20 的连续前 2 天禁买、第 3 天强制放行（禁禁买）
- **单票止损**：-8%（balanced，按信号偏好 ±）→ 触发即退出
- **行业上限**：单一行业市值占比 >45% 告警

## 检查步骤
1. 组合净值与回撤：
   ```sql
   WITH e AS (SELECT trade_date, equity FROM paper_trades WHERE action='EOD'
              ORDER BY trade_date)
   SELECT trade_date, equity,
          ROUND((MAX(equity) OVER (ORDER BY trade_date) - equity)
                / NULLIF(MAX(equity) OVER (ORDER BY trade_date),0) * 100, 2) AS dd_pct
   FROM e ORDER BY trade_date DESC LIMIT 10;
   ```
2. 当前持仓与浮亏：
   ```sql
   SELECT p.stock_code, p.stock_name, p.shares, p.buy_price,
          (SELECT close_hfq FROM daily_quote q WHERE q.stock_code=p.stock_code
           ORDER BY trade_date DESC LIMIT 1) AS last_hfq
   FROM paper_positions p;
   ```
3. 行业集中度：
   ```sql
   SELECT sm.industry_l1, COUNT(*), SUM(p.shares * p.buy_price) AS mv
   FROM paper_positions p JOIN stock_master sm ON sm.stock_code=p.stock_code
   GROUP BY 1 ORDER BY mv DESC;
   ```
4. 市场闸门状态（指数 vs MA20）：
   ```sql
   SELECT trade_date, close,
          AVG(close) OVER (ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20
   FROM index_daily_quote WHERE index_code='000300'
   ORDER BY trade_date DESC LIMIT 1;
   ```
5. 今日风控告警：`SELECT kind, level, title FROM risk_alerts ORDER BY id DESC LIMIT 10;`

## 输出格式
1. **能否开新仓**（是/否 + 触发的是哪条规则）
2. 回撤表：日期、净值、回撤%
3. 需要减仓的持仓（浮亏接近止损线的，附距止损距离）
4. 纪律建议：明确「停止开仓/可正常开仓」「需减仓 N 只」，不提供具体买卖点

## 注意
- 熔断判定看**收盘净值**，不看盘中
- 若回撤未触发但临界（8%~10%），提示「距熔断 2 个百分点以内」
- 规则数值来自 strategy_config（paper_portfolio / risk_rules），与页面一致
