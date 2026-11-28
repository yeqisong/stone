"""市场择时空仓闸门（regime gate）——app 层单一事实源。

原实现住在 scripts/pipeline.py（2026-09-05 拍板引入、2026-10-10 局部计数重写）；
v3.26 起实时空仓闸门联动（design/13 批次三）也需要它，下沉到 app 层供
pipeline / buy_check.live_gate 共用，pipeline 保留导入别名（test_regime_gate.py
继续从 scripts.pipeline 导入，行为不变）。

闸门口径（daily-close，日频）：指数收盘 < MA(N) 为风险期，风险期连续前
max_skip_days 天不开新仓，「不能连续空仓 3 天」——禁、禁、买循环保留反弹敞口。
"""
from __future__ import annotations

from datetime import date as _date, datetime as _dtm, timedelta as _tdm, timezone
from typing import Optional

from sqlalchemy import text


def compute_regime_gates(db, dates, cfg=None):
    """市场择时空仓闸门：指数收盘 < MA(N) 为风险期，风险期连续前 max_skip_days 天不开新仓。

    「不能连续空仓 3 天」约束（2026-09-05 用户拍板）：风险期第 1、2 天禁止买入，
    第 3 天强制放行——熊市中呈「禁、禁、买」循环，保留反弹敞口。
    cfg 来自 model_versions.config['regime']：
      {"enabled": true, "index_code": "000300", "ma_window": 20, "max_skip_days": 2}
    cfg 缺 enabled 键视为未启用；指数数据不足 win+1 天时不启用（不误伤）。
    返回 gated 日期字符串集合（这些日期不产生新买入）。

    判定用「连续低于 MA 天数 mod (max_skip+1)」的局部计数（2026-10-10 重写）：
    旧实现是累计相位（放行日归零），streak 相位依赖回看窗口起点——窗口随调用日
    前移时相位漂移，曾在持续下跌段把「禁禁买」循环搅成连续全禁（2026-09-24 起
    连续 8 个交易日无放行）。局部计数的 gate 决定只依赖「回溯到上一个站上 MA 日
    的连续段长度」，窗口怎么截都不影响结果（前提：窗口覆盖当前连续段，回看已
    拉长到一年日历兜底超长熊市段）。
    """
    import pandas as _pd2
    cfg = cfg or {}
    if not cfg.get('enabled'):
        return set()
    idx_code = cfg.get('index_code', '000300')
    win = int(cfg.get('ma_window', 20) or 20)
    max_skip = int(cfg.get('max_skip_days', 2) or 2)
    ds = sorted({str(d)[:10] for d in (dates if dates is not None else [])})
    if not ds:
        return set()
    # 回看窗口：MA 预热 + 足够覆盖当前连续低于段（超长熊市段兜底，多查几百行成本可忽略）
    start = (_dtm.strptime(ds[0], '%Y-%m-%d') - _tdm(days=max(win * 3, 370))).isoformat()
    rows = db.execute(text(
        "SELECT trade_date, close FROM index_daily_quote "
        "WHERE index_code=:c AND trade_date BETWEEN :s AND :e AND close IS NOT NULL "
        "ORDER BY trade_date"
    ), {"c": idx_code, "s": start, "e": ds[-1]}).fetchall()
    if len(rows) < win + 1:
        return set()
    idx = _pd2.DataFrame(rows, columns=['d', 'close'])
    idx['close'] = idx['close'].astype(float)
    idx['ma'] = idx['close'].rolling(win).mean()
    gated_all = set()
    run = 0   # 连续低于 MA 天数（局部计数：站上 MA 归零，放行日不归零）
    for r in idx.itertuples(index=False):
        if _pd2.isna(r.ma):
            continue  # MA 未成形期不设闸
        d10 = str(r.d)[:10]
        if r.close < r.ma:
            run += 1
            if run % (max_skip + 1) != 0:
                gated_all.add(d10)
            # run 是 max_skip+1 的整倍数 → 强制放行日（第 3、6、9…个连续低于日）
        else:
            run = 0
    return {d for d in ds if d in gated_all}


def get_active_regime_cfg(db) -> dict:
    """ACTIVE 模型的 regime 配置（无 ACTIVE 模型/无 regime 键 → {}，视为未启用）。"""
    import json as _json
    row = db.execute(text(
        "SELECT config FROM model_versions "
        "WHERE status='ACTIVE' ORDER BY activated_at DESC NULLS LAST LIMIT 1"
    )).fetchone()
    if row is None or not row[0]:
        return {}
    try:
        cfg = _json.loads(row[0]) if isinstance(row[0], str) else dict(row[0])
    except Exception:
        return {}
    reg = cfg.get('regime')
    return dict(reg) if isinstance(reg, dict) else {}


_CST = timezone(_tdm(hours=8))


def bj_today() -> _date:
    """北京时间的自然日。「今天是否禁买」不能依赖主机时区（UTC 主机在北京时间
    00:00–08:00 会把今天算成昨天）。"""
    return _dtm.now(_CST).date()


def regime_gated_today(db, today=None) -> tuple[bool, dict]:
    """今天是否处于 regime 禁买日。返回 (gated, cfg)；未启用 → (False, {})。"""
    today = today or bj_today()
    cfg = get_active_regime_cfg(db)
    if not cfg.get('enabled'):
        return False, cfg
    gated = str(today) in compute_regime_gates(db, [str(today)], cfg)
    return gated, cfg
