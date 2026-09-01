"""绩效报告（对齐 design/05 §3.5 与 Qlib evaluate.py:26-93 精读）。

- risk_analysis：Qlib 原样移植（sum=算术累计默认，product=几何累计）；
  Qlib 的"年化收益"按算术 mean×N 设计（避免复合曲线指数偏斜），IR=mean/std×√N。
  N=238（A 股年均交易日，Qlib Freq.NORM_FREQ_DAY 标定）。
- performance_report：净值 + 基准 + 逐日记录 → IR/alpha/beta/excess/换手/胜率细化，
  供归因分析 API 与前端展示消费。
"""
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


def risk_analysis(r, N: int = 238, mode: str = "sum") -> Dict[str, float]:
    """Qlib risk_analysis 移植（qlib/contrib/evaluate.py）。

    Args:
        r: 日收益序列（pd.Series 或一维数组）
        N: 年化系数（day=238，Qlib 标定；week=50/month=12）
        mode: sum=算术累计（Qlib 默认）；product=几何累计（复利）
    Returns:
        {mean, std, annualized_return, information_ratio, max_drawdown}
    """
    if len(r) == 0:
        return {'mean': 0.0, 'std': 0.0, 'annualized_return': 0.0,
                'information_ratio': 0.0, 'max_drawdown': 0.0}
    r = pd.Series(r, dtype=float)
    if mode == "sum":
        mean = float(r.mean())
        std = float(r.std(ddof=1)) if len(r) > 1 else 0.0
        annualized_return = mean * N
        max_drawdown = float((r.cumsum() - r.cumsum().cummax()).min())
    elif mode == "product":
        cumulative_curve = (1 + r).cumprod()
        # 几何平均（复合年增长率口径的日均值）
        mean = float(cumulative_curve.iloc[-1] ** (1 / len(r)) - 1)
        # 对数收益波动率
        std = float(np.log(1 + r).std(ddof=1)) if len(r) > 1 else 0.0
        cumulative_return = float(cumulative_curve.iloc[-1] - 1)
        annualized_return = (1 + cumulative_return) ** (N / len(r)) - 1
        max_drawdown = float((cumulative_curve / cumulative_curve.cummax() - 1).min())
    else:
        raise ValueError(f"risk_analysis accumulation mode {mode} is not supported. Expected 'sum' or 'product'.")
    information_ratio = mean / std * np.sqrt(N) if std > 1e-12 else 0.0
    return {'mean': round(mean, 6), 'std': round(std, 6),
            'annualized_return': round(float(annualized_return), 6),
            'information_ratio': round(float(information_ratio), 4),
            'max_drawdown': round(max_drawdown, 6)}


def _daily_returns(curve) -> np.ndarray:
    """净值序列 → 日收益数组（首元素为基准位，不产生收益）。"""
    eq = np.asarray(curve, dtype=float)
    if len(eq) < 2:
        return np.array([])
    return eq[1:] / eq[:-1] - 1


def alpha_beta(rets, bench_rets) -> Dict[str, float]:
    """对基准日收益一元回归：beta=cov(r,rb)/var(rb)；alpha 日均→年化（×N，design/05 §3.5）。"""
    r = np.asarray(rets, dtype=float)
    rb = np.asarray(bench_rets, dtype=float)
    n = min(len(r), len(rb))
    if n < 2:
        return {'alpha_daily': 0.0, 'beta': 0.0, 'alpha_annualized': 0.0}
    r, rb = r[-n:], rb[-n:]
    var_b = float(np.var(rb, ddof=1))
    if var_b < 1e-16:
        return {'alpha_daily': 0.0, 'beta': 0.0, 'alpha_annualized': 0.0}
    beta = float(np.cov(r, rb, ddof=1)[0, 1] / var_b)
    alpha_daily = float(np.mean(r) - beta * np.mean(rb))
    return {'alpha_daily': round(alpha_daily, 6), 'beta': round(beta, 4),
            'alpha_annualized': round(alpha_daily * 238, 4)}


def performance_report(equity_curve, bench_close: Optional[List[float]] = None,
                       daily_records: Optional[List] = None,
                       trades: Optional[List[dict]] = None,
                       N: int = 238) -> Dict:
    """完整绩效报告：IR/年化/回撤（sum+product）+ alpha/beta/excess/换手/胜率。

    Args:
        equity_curve: 净值序列（[0] 为初始资金基准位）
        bench_close: 基准收盘序列（与 equity_curve 同长对齐——首元素填首日基准价）
        daily_records: 引擎 DailyRecord 列表（取 turnover/account 算换手率）
        trades: 成交列表（取 pnl 算按单胜率 pos 口径）
    """
    rets = _daily_returns(equity_curve)
    out = {
        'n_days': len(rets),
        'sum': risk_analysis(rets, N, 'sum'),
        'product': risk_analysis(rets, N, 'product'),
    }
    if bench_close is not None and len(bench_close) >= 2:
        bench = np.asarray(bench_close, dtype=float)
        brets = bench[1:] / bench[:-1] - 1
        n = min(len(rets), len(brets))
        r, rb = rets[-n:], brets[-n:]
        ab = alpha_beta(r, rb)
        excess = r - rb
        out['excess'] = {
            **ab,
            'annualized_return': round(float(excess.mean()) * N, 4),
            'information_ratio': (round(float(excess.mean() / excess.std(ddof=1) * np.sqrt(N)), 4)
                                  if excess.std(ddof=1) > 1e-12 else 0.0),
            'benchmark_return': round(float(np.prod(1 + rb) - 1), 4),
        }
    if daily_records:
        turnover = [getattr(rec, 'turnover', 0) or 0 for rec in daily_records]
        accounts = [getattr(rec, 'account', 0) or 0 for rec in daily_records]
        avg_acc = float(np.mean(accounts)) if accounts else 0.0
        n = len(daily_records)
        out['turnover'] = {
            'total': round(float(np.sum(turnover)), 2),
            'daily_avg': round(float(np.sum(turnover)) / avg_acc / n, 4) if avg_acc > 0 and n else 0.0,
        }
    if trades:
        pnls = [t['pnl'] for t in trades if t.get('pnl') is not None]
        if pnls:
            out['win_rate_pos'] = round(len([p for p in pnls if p > 0]) / len(pnls), 4)
    return out
