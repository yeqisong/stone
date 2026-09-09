"""因子 IC 检验引擎 — 截面 RankIC / ICIR / 分层收益 / 衰减分析。

口径（与特征页「IC 体检」展示一致，页脚同步说明）：
- RankIC（主指标）：每日截面因子排名 vs 前瞻 N 日收益排名的 Spearman 相关
- ICIR = mean(IC)/std(IC)；t_stat = mean/std*sqrt(n)
- 分层：每日截面按因子值均分 layers 组（索引 0=因子最低组 … layers-1=最高组），
  组内等权，净值按"前瞻收益/持有天数"日均化近似累计——未计成本，仅用于观察单调性，
  不作为策略回测依据
- 红绿灯：|RankIC|≥0.02 且 |ICIR|≥0.30 且 同号占比≥55% → 绿；两项达标 → 黄；其余 → 红
- 前瞻收益用 SQL LEAD 窗口在 val_end+buffer 范围上计算，验证区间尾部截面不缺未来价
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import numpy as np
import pandas as pd
from sqlalchemy import text

# 红绿灯阈值（前端同款展示，改动需两处同步）
IC_GREEN = {'abs_rank_ic': 0.02, 'abs_icir': 0.30, 'same_sign': 0.55}
# 去冗推荐参数
DEDUP_THRESHOLD = 0.7   # 与已选因子 |截面相关| 超过则视为冗余
ICIR_FLOOR = 0.10       # |ICIR| 低于此值视为证据不足，不推荐入选


def greedy_dedup(corr, icir, threshold: float = DEDUP_THRESHOLD, icir_floor: float = ICIR_FLOOR):
    """贪心去冗推荐：按 |ICIR| 降序选因子，与已选因子 |ρ|>threshold 的跳过。

    corr: {(a, b): rho} 对称字典；icir: {factor: icir}（来自最新一次 factor_ic_stats）。
    |ICIR| < icir_floor 的因子即使不冗余也不推荐（证据不足）。
    Returns: (selected: [factor], skipped: [{'factor', 'with', 'rho', 'reason'}])
    """
    order = sorted(icir, key=lambda k: abs(icir[k] or 0), reverse=True)
    selected, skipped = [], []
    for f in order:
        best_rho, best_with = 0.0, None
        for s in selected:
            rho = abs(corr.get((f, s), corr.get((s, f), 0)) or 0)
            if rho > best_rho:
                best_rho, best_with = rho, s
        if best_rho > threshold:
            skipped.append({'factor': f, 'with': best_with, 'rho': round(best_rho, 3),
                            'reason': f'与 {best_with} 相关 {best_rho:.2f} > {threshold}'})
        elif abs(icir[f] or 0) < icir_floor:
            skipped.append({'factor': f, 'with': None, 'rho': None,
                            'reason': f'|ICIR| {abs(icir[f] or 0):.2f} < {icir_floor}，证据不足'})
        else:
            selected.append(f)
    return selected, skipped


def traffic_light(rank_ic: float, icir: float, same_sign: float) -> str:
    """绿=三项达标，黄=两项，红=其余。因子方向独立标注，不影响灯色。"""
    score = sum([abs(rank_ic) >= IC_GREEN['abs_rank_ic'],
                 abs(icir) >= IC_GREEN['abs_icir'],
                 same_sign >= IC_GREEN['same_sign']])
    return {3: 'green', 2: 'yellow'}.get(score, 'red')


def fetch_shared_panel(db, val_start: str, val_end: str, horizons,
                       buffer_days: int = 60) -> pd.DataFrame:
    """共享前向收益面板（与因子无关，批量场景只拉一次）。

    daily_quote 的 LEAD 前瞻价窗口是最重的一步（全市场 × 全区间排序窗口），
    逐因子重算一遍是 factor_ic 节点耗时 100 分钟的主因；此处一次拉取后所有因子共用。
    Returns: [trade_date, stock_code, close, fret_{h}...]
    """
    from datetime import date as _d, timedelta as _td
    buf_end = (_d.fromisoformat(str(val_end)[:10]) + _td(days=buffer_days)).isoformat()
    leads = ",\n                   ".join(f"LEAD(close_hfq, {h}) OVER w AS fwd_{h}" for h in horizons)
    fwd_cols = ", ".join(f"q.fwd_{h}" for h in horizons)
    sql = f"""
        SELECT stock_code, trade_date, close_hfq,
               {leads}
        FROM daily_quote
        WHERE trade_date BETWEEN :sd AND :buf AND close_hfq IS NOT NULL AND close_hfq > 0
          AND exchange IN ('SSE','SZSE')
        WINDOW w AS (PARTITION BY stock_code ORDER BY trade_date)
    """
    rows = db.execute(text(sql), {"sd": str(val_start)[:10], "buf": buf_end}).fetchall()
    cols = ['stock_code', 'trade_date', 'close'] + [f'fwd_{h}' for h in horizons]
    panel = pd.DataFrame(rows, columns=cols)
    if panel.empty:
        return panel
    for c in cols[2:]:
        panel[c] = pd.to_numeric(panel[c], errors='coerce')  # SQL NUMERIC(Decimal) → float
    for h in horizons:
        panel[f'fret_{h}'] = panel[f'fwd_{h}'] / panel['close'] - 1
    fret_cols = [f'fret_{h}' for h in horizons]
    panel[fret_cols] = panel[fret_cols].replace([np.inf, -np.inf], np.nan)
    return panel[['trade_date', 'stock_code', 'close'] + fret_cols]


def _factor_slice(db, feature_name: str, val_start: str, val_end: str) -> pd.DataFrame:
    """单因子的 feature_values 切片（不含行情，轻查询）。"""
    rows = db.execute(text(
        "SELECT trade_date, stock_code, value FROM feature_values "
        "WHERE feature_name = :fn AND trade_date BETWEEN :sd AND :ed AND value IS NOT NULL"
    ), {"fn": feature_name, "sd": str(val_start)[:10], "ed": str(val_end)[:10]}).fetchall()
    df = pd.DataFrame(rows, columns=['trade_date', 'stock_code', 'value'])
    if not df.empty:
        df['value'] = pd.to_numeric(df['value'], errors='coerce')
    return df


def _merge_panel(factor_df: pd.DataFrame, panel: pd.DataFrame,
                 horizons) -> pd.DataFrame:
    """因子切片 INNER JOIN 共享面板，产出与旧 fetch_ic_frame 相同形状的宽框。

    （旧 SQL 为 JOIN q ON stock_code+trade_date，inner 语义一致；
      close/前向收益已在面板算好，inf 兜底同旧版）
    """
    if factor_df.empty or panel.empty:
        return pd.DataFrame()
    df = factor_df.merge(panel, on=['trade_date', 'stock_code'], how='inner')
    return df


def fetch_ic_frame(db, feature_name: str, val_start: str, val_end: str,
                   horizons, buffer_days: int = 60) -> pd.DataFrame:
    """因子值 + 前瞻 N 日收益一次取回（所有 horizon 共用一次查询）。"""
    horizons = sorted({int(h) for h in horizons})
    panel = fetch_shared_panel(db, val_start, val_end, horizons, buffer_days)
    if panel.empty:
        return pd.DataFrame()
    fv = _factor_slice(db, feature_name, val_start, val_end)
    return _merge_panel(fv, panel, horizons)


def _daily_ic(df: pd.DataFrame, horizon: int, min_names: int = 30):
    """逐日截面 IC。Returns: (dates, pearson_ic 序列, rank_ic 序列, 每日样本数序列)。"""
    sub = df[['trade_date', 'value', f'fret_{horizon}']].dropna()
    fret = f'fret_{horizon}'

    def _pair(g):
        if len(g) < min_names:
            return (np.nan, np.nan, len(g))
        rv = g['value'].rank()
        rr = g[fret].rank()
        return (g['value'].corr(g[fret]), rv.corr(rr), len(g))

    out = sub.groupby('trade_date').apply(_pair)
    if out.empty:
        return [], [], [], []
    pairs = pd.DataFrame(out.tolist(), index=out.index, columns=['ic', 'rank_ic', 'n'])
    pairs = pairs.dropna(subset=['ic', 'rank_ic'])  # 常数截面等导致的 NaN 双侧剔除
    dates = [str(d)[:10] for d in pairs.index]
    return dates, pairs['ic'].tolist(), pairs['rank_ic'].tolist(), pairs['n'].tolist()


def _layer_stats(df: pd.DataFrame, horizon: int, layers: int = 5) -> dict:
    """分层净值（日均化近似）+ 各层平均前瞻收益。索引 0=因子最低组。"""
    sub = df[['trade_date', 'value', f'fret_{horizon}']].dropna()
    if sub.empty:
        return {}
    sub = sub.copy()
    sub['q'] = sub.groupby('trade_date')['value'].transform(
        lambda s: pd.qcut(s.rank(method='first'), layers, labels=False))
    daily = sub.groupby(['trade_date', 'q'])[f'fret_{horizon}'].agg(['mean', 'count']).reset_index()
    wide = daily.pivot(index='trade_date', columns='q', values='mean')
    counts = daily.pivot(index='trade_date', columns='q', values='count')
    wide = wide.reindex(columns=range(layers))
    nav = (wide / horizon + 1).cumprod()
    ls = (wide[layers - 1] - wide[0]) / horizon  # 多空 = 最高组 - 最低组（日均化）
    ls_nav = (ls + 1).cumprod()
    dates = [str(d)[:10] for d in wide.index]
    return {
        'dates': dates,
        'navs': {str(q): [round(v, 4) if pd.notna(v) else None for v in nav[q]] for q in range(layers)},
        'ls_nav': [round(v, 4) for v in ls_nav],
        'mean_fret': [round(float(wide[q].mean()), 6) for q in range(layers)],
        'annualized': [round(float(wide[q].mean() / horizon * 252), 4) for q in range(layers)],
        'avg_names': round(float(counts.stack().mean()), 1),
    }


def _upsert_horizons(db, feature_name: str, df: pd.DataFrame, val_start: str, val_end: str,
                     horizons, layers: int = 5, min_names: int = 30) -> list:
    """对已合并好的因子宽框逐 horizon 计算 IC 并落库（compute_factor_ic 与批量版共用主体）。"""
    results = []
    for h in horizons:
        dates, ic_list, rank_list, n_list = _daily_ic(df, h, min_names)
        if len(dates) < min_names:
            results.append({'feature_name': feature_name, 'horizon': h,
                            'error': f'有效截面不足（{len(dates)} 天 < {min_names}），样本太小'})
            continue
        ic_arr = np.array(ic_list); rk = np.array(rank_list)
        rk_mean = float(rk.mean()); rk_std = float(rk.std())
        rank_icir = rk_mean / rk_std if rk_std > 1e-12 else 0.0
        ic_mean = float(ic_arr.mean()); ic_std = float(ic_arr.std())
        icir = ic_mean / ic_std if ic_std > 1e-12 else 0.0
        win = float((rk > 0).mean())
        same_sign = max(win, 1 - win)
        direction = '-' if rk_mean < 0 else '+'
        t_stat = float(rk_mean / rk_std * np.sqrt(len(rk))) if rk_std > 1e-12 else 0.0
        layers_data = _layer_stats(df, h, layers)

        cum = np.cumsum(rk)
        payload = {
            'feature_name': feature_name, 'horizon': h,
            'val_start': str(val_start)[:10], 'val_end': str(val_end)[:10],
            'sample_days': int(len(rk)),
            'avg_names': float(np.mean(n_list)),
            'ic_mean': round(ic_mean, 6), 'rank_ic_mean': round(rk_mean, 6),
            'ic_ir': round(icir, 6), 'rank_ic_ir': round(rank_icir, 6),
            'ic_win_rate': round(win, 4), 't_stat': round(t_stat, 4),
            'direction': direction, 'traffic': traffic_light(rk_mean, rank_icir, same_sign),
            'ic_series': json.dumps({'dates': dates,
                                     'ic': [round(v, 6) for v in ic_list],
                                     'rank_ic': [round(v, 6) for v in rank_list],
                                     'cum_ic': [round(v, 4) for v in cum]}),
            'q_returns': json.dumps(layers_data),
        }
        db.execute(text("""
            INSERT INTO factor_ic_stats (feature_name, horizon, val_start, val_end,
                sample_days, avg_names, ic_mean, rank_ic_mean, ic_ir, rank_ic_ir,
                ic_win_rate, t_stat, direction, traffic, ic_series, q_returns)
            VALUES (:feature_name, :horizon, :val_start, :val_end,
                :sample_days, :avg_names, :ic_mean, :rank_ic_mean, :ic_ir, :rank_ic_ir,
                :ic_win_rate, :t_stat, :direction, :traffic, :ic_series, :q_returns)
            ON CONFLICT (feature_name, horizon, val_start, val_end) DO UPDATE SET
                sample_days = EXCLUDED.sample_days, avg_names = EXCLUDED.avg_names,
                ic_mean = EXCLUDED.ic_mean, rank_ic_mean = EXCLUDED.rank_ic_mean,
                ic_ir = EXCLUDED.ic_ir, rank_ic_ir = EXCLUDED.rank_ic_ir,
                ic_win_rate = EXCLUDED.ic_win_rate, t_stat = EXCLUDED.t_stat,
                direction = EXCLUDED.direction, traffic = EXCLUDED.traffic,
                ic_series = EXCLUDED.ic_series, q_returns = EXCLUDED.q_returns,
                created_at = CURRENT_TIMESTAMP
        """), payload)
        results.append(payload)
    db.commit()
    return results


def compute_factor_ic(db, feature_name: str, val_start: str, val_end: str,
                      horizons=(1, 5, 10, 20), layers: int = 5, min_names: int = 30) -> list:
    """对单因子计算各 horizon 的 IC 检验并落库（UPSERT factor_ic_stats）。

    Returns: 每个 horizon 一条结果 dict（含 traffic 红绿灯）；区间无数据抛 ValueError。
    """
    horizons = sorted({int(h) for h in horizons})
    df = fetch_ic_frame(db, feature_name, val_start, val_end, horizons)
    if df.empty:
        raise ValueError(f'区间 {val_start}~{val_end} 内无有效因子值，请先完成特征计算')
    return _upsert_horizons(db, feature_name, df, val_start, val_end, horizons, layers, min_names)


def compute_factor_ic_batch(db, feature_names: list, val_start: str, val_end: str,
                            horizons=(1, 5, 10, 20), layers: int = 5, min_names: int = 30,
                            on_progress=None) -> dict:
    """批量因子 IC：共享前向收益面板只拉一次，逐因子轻查询 + 同口径计算。

    单因子失败不中断批（返回 error），on_progress(done, total, feature_name) 用于心跳。
    Returns: {feature_name: results_list | {'error': str}}
    """
    horizons = sorted({int(h) for h in horizons})
    panel = fetch_shared_panel(db, val_start, val_end, horizons)
    if panel.empty:
        raise ValueError(f'区间 {val_start}~{val_end} 行情面板为空')
    out = {}
    total = len(feature_names)
    for i, fn in enumerate(feature_names, 1):
        try:
            fv = _factor_slice(db, fn, val_start, val_end)
            df = _merge_panel(fv, panel, horizons)
            if df.empty:
                raise ValueError(f'区间 {val_start}~{val_end} 内无有效因子值，请先完成特征计算')
            out[fn] = _upsert_horizons(db, fn, df, val_start, val_end, horizons, layers, min_names)
        except Exception as e:
            db.rollback()
            out[fn] = {'error': str(e)[:160]}
        if on_progress:
            try: on_progress(i, total, fn)
            except Exception: pass
    return out
