---
name: signal-review
title: 信号复盘
description: 按模型版本/信号日/方向统计信号的胜率、前瞻收益与退出原因，定位「为什么最近信号少/胜率低」。用户问某版模型表现、信号质量、为何没信号时使用。
---

# 信号复盘

## 何时用
- 「v15 最近表现怎么样」「为什么这几天没有信号」
- 「这批信号胜率如何」「哪个退出原因亏得最多」

## 数据口径（先讲清楚，再给数）
- `signal_history.direction='buy'` 才是模型买入信号；`strategy_name='model_signal'`。
- `forward_5d/10d/20d_return` 为**信号日收盘→N 个交易日后收盘**的真实收益（已按交易日历回填）。
- `status='closed'` + `close_reason` 是虚拟屏障口径（stop_loss/take_profit/hold_expire），
  **不是纸面组合真实成交**；两者不可混比。
- 纸面组合真实表现看 `paper_trades`（action=BUY/SELL 为成交，EOD 为逐日净值）。

## 常用查询

1. 某版本信号量与分布：
   ```sql
   SELECT signal_date, COUNT(*) AS n,
          COUNT(*) FILTER (WHERE forward_5d_return IS NOT NULL) AS matured
   FROM signal_history
   WHERE model_version='v15.0' AND direction='buy'
   GROUP BY signal_date ORDER BY signal_date DESC LIMIT 60
   ```
2. 前瞻收益与胜率（按方向/版本）：
   ```sql
   SELECT model_version, direction, COUNT(*) AS n,
          ROUND(AVG(forward_5d_return)*100, 2) AS avg_5d,
          ROUND(AVG(CASE WHEN forward_5d_return > 0 THEN 1.0 ELSE 0 END)*100, 1) AS win_5d
   FROM signal_history
   WHERE strategy_name='model_signal' AND forward_5d_return IS NOT NULL
   GROUP BY model_version, direction ORDER BY model_version
   ```
3. 屏障了结表现（虚拟口径）：
   ```sql
   SELECT close_reason, COUNT(*) AS n,
          ROUND(AVG(actual_return)*100, 2) AS avg_ret,
          ROUND(AVG(CASE WHEN actual_return > 0 THEN 1.0 ELSE 0 END)*100, 1) AS win_rate,
          ROUND(AVG(closed_at::date - signal_date), 1) AS avg_days
   FROM signal_history
   WHERE model_version='v15.0' AND direction='buy' AND status='closed'
   GROUP BY close_reason ORDER BY n DESC
   ```
4. 纸面组合（真实口径）：
   ```sql
   SELECT action, COUNT(*) AS n, ROUND(SUM(pnl), 0) AS pnl_sum
   FROM paper_trades WHERE action IN ('BUY','SELL') GROUP BY action;
   -- 最大回撤（EOD 净值）
   SELECT ROUND((MIN(equity) / MAX(equity) - 1) * 100, 2) FROM paper_trades WHERE action='EOD';
   ```
5. 为什么没信号（先查节点日志，再查闸门）：
   ```sql
   SELECT trade_date, status, rows, LEFT(detail, 120)
   FROM dag_run_log WHERE node_name='model_signal'
   ORDER BY trade_date DESC LIMIT 20
   ```
   detail 里出现「空仓信号日」= 开仓闸门（沪深300 低于 MA20 的风险期）在拦，
   节点本身是成功的；连续禁买超过 3 天则可能是闸门逻辑问题，需要看 `compute_regime_gates`。

## 输出格式
1. 结论（信号质量是否正常 / 为何为 0）
2. 一张随时间表（信号日、条数、成熟数、5d 均值、胜率）
3. 一张退出原因表
4. **样本量与置信度提醒**：少于 30 笔时不要下「模型失效」结论，明确写样本不足。

## 注意
- 不要把虚拟屏障胜率与回测胜率、纸面胜率混在一起比较。
- 前瞻列在信号发出后需等 N 个交易日才有值，未成熟显示为空是正常。
