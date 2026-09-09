"""风控规则引擎（step3）：纸面组合的规则评估 + 告警落库 + WS 广播桥。

规则存 strategy_config（strategy_name='risk_rules'，params JSON 文本），默认关闭。
评估入口 evaluate_risk_rules(db, trade_date)：
- stop_loss_pct    单票自建仓价回撤超阈值
- max_drawdown_pct 组合净值自峰值回撤超阈值（paper_trades.equity 序列）
- industry_cap_pct 单行业持仓市值占比上限（行业取 stock_master.industry_l1）

告警写 risk_alerts（按 trade_date+kind+title 去重，同日同因不重复告警），
status.py 的 WS 广播循环轮询新告警推给前端，前端以 Chrome 通知呈现。
"""
import json
from datetime import date

from sqlalchemy import text

RULES_CONFIG_KEY = 'risk_rules'

DEFAULT_RULES = {
    'enabled': False,
    'stop_loss_pct': 0.08,        # 单票自建仓价回撤 8% 告警
    'max_drawdown_pct': 0.15,     # 组合净值自峰值回撤 15% 熔断告警
    'industry_cap_pct': 0.45,     # 单行业持仓市值占比 45% 上限告警
    'float_days_ahead': 30,       # 持仓股解禁日程提前 30 天告警
    'max_alerts_per_day': 5,      # 止损类逐票告警的单日上限（防刷屏）
}


def get_risk_rules(db) -> dict:
    row = db.execute(text(
        "SELECT params, enabled FROM strategy_config WHERE strategy_name = :k ORDER BY id DESC LIMIT 1"
    ), {"k": RULES_CONFIG_KEY}).fetchone()
    rules = dict(DEFAULT_RULES)
    if row:
        try:
            rules.update(json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {}))
        except (json.JSONDecodeError, TypeError):
            pass
        rules['enabled'] = bool(row[1]) and bool(rules.get('enabled', False))
    return rules


def save_risk_rules(db, rules: dict, enabled: bool):
    rules = {**DEFAULT_RULES, **(rules or {}), 'enabled': bool(enabled)}
    db.execute(text("DELETE FROM strategy_config WHERE strategy_name = :k"), {"k": RULES_CONFIG_KEY})
    db.execute(text(
        "INSERT INTO strategy_config (strategy_name, enabled, params) VALUES (:k, :e, :p)"
    ), {"k": RULES_CONFIG_KEY, "e": enabled, "p": json.dumps(rules)})
    db.commit()
    return rules


def _insert_alert(db, trade_date, kind, level, title, body) -> bool:
    """同日同因去重；返回是否新插入。"""
    row = db.execute(text(
        "SELECT id FROM risk_alerts WHERE trade_date = :d AND kind = :k AND title = :t LIMIT 1"
    ), {"d": trade_date, "k": kind, "t": title}).fetchone()
    if row:
        return False
    db.execute(text(
        "INSERT INTO risk_alerts (trade_date, kind, level, title, body) "
        "VALUES (:d, :k, :lv, :t, :b)"
    ), {"d": trade_date, "k": kind, "lv": level, "t": title, "b": body})
    return True


def evaluate_risk_rules(db, trade_date=None) -> list:
    """评估全部启用的风控规则，新告警落库并返回（供 WS 推送/调用方展示）。"""
    td = str(trade_date or date.today())[:10]
    rules = get_risk_rules(db)
    if not rules.get('enabled'):
        return []
    new_alerts = []
    max_per_day = int(rules.get('max_alerts_per_day', 5))
    day_count = db.execute(text(
        "SELECT count(*) FROM risk_alerts WHERE trade_date = :d"), {"d": td}).scalar() or 0

    def emit(kind, level, title, body):
        nonlocal day_count
        if day_count >= max_per_day and kind == 'stop_loss':
            return
        if _insert_alert(db, td, kind, level, title, body):
            new_alerts.append({'trade_date': td, 'kind': kind, 'level': level,
                               'title': title, 'body': body})
            day_count += 1

    # ── 1. 单票止损：paper_positions 成本价 vs 最新收盘 ──
    stop_pct = float(rules.get('stop_loss_pct') or 0)
    if stop_pct > 0:
        # 注意：paper 引擎全程用后复权价（close_hfq），成本比较必须同口径
        rows = db.execute(text("""
            SELECT p.stock_code, p.buy_price, q.close_hfq
            FROM paper_positions p
            LEFT JOIN LATERAL (
                SELECT close_hfq FROM daily_quote q
                WHERE q.stock_code = p.stock_code AND q.close_hfq > 0
                ORDER BY trade_date DESC LIMIT 1
            ) q ON true
            WHERE p.model_version = (SELECT version FROM model_versions WHERE status='ACTIVE' ORDER BY activated_at DESC NULLS LAST LIMIT 1)
        """)).fetchall()
        for code, buy, cur in rows:
            if not buy or not cur:
                continue
            chg = float(cur) / float(buy) - 1
            if chg <= -abs(stop_pct):
                emit('stop_loss', 'warn',
                     f'止损告警 {code}',
                     f'现价 {float(cur):.2f} 较成本 {float(buy):.2f} 回撤 {chg * 100:.1f}%（阈值 {stop_pct * 100:.0f}%）')

    # ── 2. 组合回撤熔断：paper_trades.equity 序列自峰值回撤 ──
    dd_pct = float(rules.get('max_drawdown_pct') or 0)
    if dd_pct > 0:
        nav_rows = db.execute(text(
            "SELECT trade_date, equity FROM paper_trades "
            "WHERE equity IS NOT NULL AND model_version = (SELECT version FROM model_versions WHERE status='ACTIVE' ORDER BY activated_at DESC NULLS LAST LIMIT 1) ORDER BY trade_date"
        )).fetchall()
        if len(nav_rows) >= 2:
            equities = [float(r[1]) for r in nav_rows]
            peak = max(equities)
            cur = equities[-1]
            if peak > 0:
                dd = 1 - cur / peak
                if dd >= dd_pct:
                    emit('drawdown', 'critical',
                         f'回撤熔断 {dd * 100:.1f}%',
                         f'组合净值 {cur:,.0f} 自峰值 {peak:,.0f} 回撤 {dd * 100:.1f}%（阈值 {dd_pct * 100:.0f}%），建议暂停开新仓')

    # ── 3. 行业暴露上限：当前持仓市值按 industry_l1 聚合 ──
    cap_pct = float(rules.get('industry_cap_pct') or 0)
    if cap_pct > 0:
        rows = db.execute(text("""
            SELECT COALESCE(sm.industry_l1, '未知') AS ind,
                   SUM(p.shares * q.close_hfq) AS mv
            FROM paper_positions p
            LEFT JOIN stock_master sm
              ON sm.stock_code = p.stock_code AND sm.stock_type = 'stock'
            LEFT JOIN LATERAL (
                SELECT close_hfq FROM daily_quote q
                WHERE q.stock_code = p.stock_code AND q.close_hfq > 0
                ORDER BY trade_date DESC LIMIT 1
            ) q ON true
            WHERE p.model_version = (SELECT version FROM model_versions WHERE status='ACTIVE' ORDER BY activated_at DESC NULLS LAST LIMIT 1)
              AND q.close_hfq IS NOT NULL
            GROUP BY ind
        """)).fetchall()
        total_mv = sum(float(r[1]) for r in rows)
        if total_mv > 0:
            for ind, mv in rows:
                weight = float(mv) / total_mv
                if weight > cap_pct:
                    emit('industry_cap', 'warn',
                         f'行业超限 {ind}',
                         f'行业 {ind} 持仓占比 {weight * 100:.1f}%（上限 {cap_pct * 100:.0f}%），建议分散')

    # ── 4. 解禁临近：持仓股未来 float_days_ahead 天内有限售解禁 ──
    fdays = int(rules.get('float_days_ahead') or 0)
    if fdays > 0:
        rows = db.execute(text("""
            SELECT p.stock_code, sf.float_date, sf.shares, sf.float_ratio
            FROM paper_positions p
            JOIN stock_share_float sf ON sf.stock_code = p.stock_code
              AND sf.float_date BETWEEN CURRENT_DATE AND CURRENT_DATE + :fd
            WHERE p.model_version = (SELECT version FROM model_versions WHERE status='ACTIVE' ORDER BY activated_at DESC NULLS LAST LIMIT 1)
        """), {"fd": fdays}).fetchall()
        for code, fdate, shares, ratio in rows:
            emit('share_float', 'warn',
                 f'解禁临近 {code}',
                 f'{fdate} 解禁 {shares or 0:.0f} 万股（占总股本 {ratio or 0:.1f}%），注意抛压')

    db.commit()
    return new_alerts
