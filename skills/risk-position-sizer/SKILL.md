---
name: risk-position-sizer
title: 风险仓位计算（A股）
description: 按风险预算计算个股买入股数与仓位——1% 风险法则、A 股 100 股整手、含佣金/印花税/滑点，输出建议股数、占用资金与最大亏损。用户问「买多少」「仓位怎么配」时使用。
---

# 风险仓位计算（A股）

## 何时用
用户给出标的与资金规模，问「买多少股 / 仓位多大」；或要按组合风险预算再平衡。

## 输入（缺就先问或从库里取）
1. 总资金：优先 `SELECT 1,000,000`（净值取纸面/实盘最新 EOD）：
   ```sql
   SELECT equity FROM paper_trades WHERE action='EOD' ORDER BY trade_date DESC LIMIT 1;
   ```
2. 现价：`SELECT close, close_hfq FROM daily_quote WHERE stock_code='600519' ORDER BY trade_date DESC LIMIT 1`
   —— 展示用 `close`（真实价）。
3. 止损位：由用户给定，或按 ATR 建议（`ATR14 × 2` 从上一个止损价往下）：
   ```sql
   SELECT value FROM feature_values
   WHERE stock_code='600519' AND feature_name='atr_14'
     AND trade_date=(SELECT MAX(trade_date) FROM feature_values);
   ```
4. 已有持仓与行业集中度：
   ```sql
   SELECT stock_code, quantity, cost_price FROM portfolio WHERE is_active=true;
   ```

## 计算
- 单笔风险预算 R = 总资金 × 1%（用户可改 0.5%~2%）
- 每股风险 = 现价 − 止损价（若止损价 ≥ 现价 → 拒绝，提示无效止损）
- 理论股数 = R ÷ 每股风险，
  **向下取整到 100 股整手**
- 占用资金 = 股数 × 现价；若 > 总资金 × 30%（单票上限，用户可改）→ 按上限反算股数
- 成本 = 佣金（万 2.5，最低 5 元）+ 卖出印花税 0.1%（预先计入）+ 滑点 0.1%
- 最大亏损（含成本）= 股数 × 每股风险 + 成本

## 输出格式
1. 结论行：`建议买入 N 股（占用资金 ¥X，占净值 Y%，最大亏损 ¥Z ≈ 净值 A%）`
2. 计算明细表：总资金 / 风险预算 / 现价 / 止损价 / 每股风险 / 理论股数 / 整手后股数 / 成本
3. 风险提示：单票上限、行业集中度（该股行业已有持仓占比）、止损有效性

## 注意
- A 股 T+1、100 股整手；科创板/创业板 200 股起（688/300/301 开头）
- 不要给出「必须买」的指令性表述，只给基于风险预算的数字与假设
- 止损位必须低于现价，且说明依据（前低/ATR/固定百分比）
