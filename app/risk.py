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
from datetime import date, datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import text

from app.api.pricing import raw_price

RULES_CONFIG_KEY = 'risk_rules'

_CST = timezone(timedelta(hours=8))


def _bj_today() -> date:
    """北京时间的自然日（v3.27.1 §10.9）：告警归属的 trade_date 不能依赖主机时区，
    否则 UTC 主机在北京 00:00–08:00 会把告警记到昨天，当日上限/去重全部错位。"""
    return datetime.now(_CST).date()

DEFAULT_RULES = {
    'enabled': False,
    'stop_loss_pct': 0.08,        # 单票自建仓价回撤 8% 告警
    'max_drawdown_pct': 0.15,     # 组合净值自峰值回撤 15% 熔断告警
    'industry_cap_pct': 0.45,     # 单行业持仓市值占比 45% 上限告警
    'float_days_ahead': 30,       # 持仓股解禁日程提前 30 天告警
    'max_alerts_per_day': 5,      # 止损类逐票告警的单日上限（防刷屏）
}


def _parse_rules(row) -> dict:
    rules = dict(DEFAULT_RULES)
    if row:
        try:
            rules.update(json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {}))
        except (json.JSONDecodeError, TypeError):
            pass
        rules['enabled'] = bool(row[1]) and bool(rules.get('enabled', False))
    return rules


def get_user_risk_rules(db, user_id: int) -> dict:
    """个人实盘风控规则（user_config.risk_rules）。"""
    row = db.execute(text(
        "SELECT params, enabled FROM user_config WHERE user_id=:uid AND config_key=:k"
    ), {"uid": user_id, "k": RULES_CONFIG_KEY}).fetchone()
    return _parse_rules(row)


def save_user_risk_rules(db, user_id: int, rules: dict, enabled: bool) -> dict:
    merged = {**DEFAULT_RULES, **(rules or {}), 'enabled': bool(enabled)}
    db.execute(text("""
        INSERT INTO user_config (user_id, config_key, params, enabled, updated_at)
        VALUES (:uid, :k, CAST(:p AS jsonb), :e, CURRENT_TIMESTAMP)
        ON CONFLICT (user_id, config_key) DO UPDATE SET
            params=EXCLUDED.params, enabled=EXCLUDED.enabled,
            updated_at=CURRENT_TIMESTAMP
    """), {"uid": user_id, "k": RULES_CONFIG_KEY,
           "p": json.dumps(merged), "e": bool(enabled)})
    db.commit()
    return merged


def get_model_risk_rules(db) -> dict:
    """模型纸面风控规则（strategy_config.model_risk_rules；兼容旧 risk_rules）。"""
    row = db.execute(text(
        "SELECT params, enabled FROM strategy_config WHERE strategy_name = 'model_risk_rules' "
        "ORDER BY id DESC LIMIT 1"
    )).fetchone()
    if row is None:
        row = db.execute(text(
            "SELECT params, enabled FROM strategy_config WHERE strategy_name = :k "
            "ORDER BY id DESC LIMIT 1"
        ), {"k": RULES_CONFIG_KEY}).fetchone()
    return _parse_rules(row)


def get_risk_rules(db) -> dict:
    """兼容旧调用：模型纸面风控规则。"""
    return get_model_risk_rules(db)


def save_risk_rules(db, rules: dict, enabled: bool):
    """保存模型纸面风控规则（全局，系统任务/管理员使用）。"""
    rules = {**DEFAULT_RULES, **(rules or {}), 'enabled': bool(enabled)}
    db.execute(text(
        "INSERT INTO strategy_config (strategy_name, enabled, params) VALUES ('model_risk_rules', :e, :p) "
        "ON CONFLICT (strategy_name) DO UPDATE SET enabled=EXCLUDED.enabled, params=EXCLUDED.params, "
        "updated_at=CURRENT_TIMESTAMP"
    ), {"e": enabled, "p": json.dumps(rules)})
    db.commit()
    return rules


def _insert_alert(db, trade_date, kind, level, title, body, user_id: int | None = None) -> bool:
    """同日同因去重（按 owner）；返回是否新插入。"""
    row = db.execute(text(
        "SELECT id FROM risk_alerts WHERE trade_date = :d AND kind = :k AND title = :t "
        "AND user_id IS NOT DISTINCT FROM :uid LIMIT 1"
    ), {"d": trade_date, "k": kind, "t": title, "uid": user_id}).fetchone()
    if row:
        return False
    db.execute(text(
        "INSERT INTO risk_alerts (user_id, trade_date, kind, level, title, body) "
        "VALUES (:uid, :d, :k, :lv, :t, :b)"
    ), {"uid": user_id, "d": trade_date, "k": kind, "lv": level, "t": title, "b": body})
    return True


def evaluate_risk_rules(db, trade_date=None) -> list:
    """评估全部启用的风控规则，新告警落库并返回（供 WS 推送/调用方展示）。"""
    td = str(trade_date or _bj_today())[:10]
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
        # 注意：paper 引擎全程用后复权价（close_hfq），成本比较必须同口径；
        # 告警文案里的价格是给人看的，换算成真实价（同比例缩放到未复权空间）
        rows = db.execute(text("""
            SELECT p.stock_code, p.buy_price, q.close_hfq, q.close
            FROM paper_positions p
            LEFT JOIN LATERAL (
                SELECT close_hfq, close FROM daily_quote q
                WHERE q.stock_code = p.stock_code AND q.close_hfq > 0
                ORDER BY trade_date DESC LIMIT 1
            ) q ON true
            WHERE p.model_version = (SELECT version FROM model_versions WHERE status='ACTIVE' ORDER BY activated_at DESC NULLS LAST LIMIT 1)
        """)).fetchall()
        for code, buy, cur, cur_raw in rows:
            if not buy or not cur:
                continue
            chg = float(cur) / float(buy) - 1
            if chg <= -abs(stop_pct):
                factor = (float(cur_raw) / float(cur)) if cur_raw else None
                show_cur = raw_price(float(cur), factor)
                show_buy = raw_price(float(buy), factor)
                pct_txt = f'{show_cur:.2f} 较成本 {show_buy:.2f}' if show_cur and show_buy \
                    else f'{float(cur):.2f} 较成本 {float(buy):.2f}（后复权口径）'
                emit('stop_loss', 'warn', f'止损告警 {code}',
                     f'现价 {pct_txt} 回撤 {chg * 100:.1f}%（阈值 {stop_pct * 100:.0f}%）')

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

def evaluate_user_risk_rules(db, user_id: int, trade_date=None) -> list:
    """评估单个用户的实盘风控规则（portfolio + 个人 user_config.risk_rules）。

    - 止损：最新收盘（不复权）较持仓成本回撤；
    - 行业上限：按最新市值聚合 industry_l1 占比；
    - 个人没有纸面净值序列，组合回撤规则不在此评估（由纸面模型规则负责）。
    告警写入 risk_alerts(user_id=该用户)，WS 按 owner 推送。
    """
    td = str(trade_date or _bj_today())[:10]
    uid = int(user_id)
    rules = get_user_risk_rules(db, uid)
    if not rules.get('enabled'):
        return []
    new_alerts = []
    max_per_day = int(rules.get('max_alerts_per_day', 5))
    day_count = db.execute(text(
        "SELECT count(*) FROM risk_alerts WHERE user_id=:uid AND trade_date=:d"),
        {"uid": uid, "d": td}).scalar() or 0

    def emit(kind, level, title, body):
        nonlocal day_count
        if kind == 'stop_loss' and day_count >= max_per_day:
            return
        if _insert_alert(db, td, kind, level, title, body, user_id=uid):
            new_alerts.append({'trade_date': td, 'kind': kind, 'level': level,
                               'title': title, 'body': body, 'user_id': uid})
            day_count += 1

    stop_pct = float(rules.get('stop_loss_pct') or 0)
    if stop_pct > 0:
        rows = db.execute(text("""
            SELECT p.stock_code, p.stock_name, p.quantity, p.cost_price, q.close
            FROM portfolio p
            LEFT JOIN LATERAL (
                SELECT close FROM daily_quote q
                WHERE q.stock_code=p.stock_code AND q.close > 0
                ORDER BY q.trade_date DESC LIMIT 1
            ) q ON true
            WHERE p.user_id=:uid AND p.is_active=true
              AND p.cost_price > 0 AND q.close > 0
        """), {"uid": uid}).fetchall()
        for code, name, _qty, cost, cur in rows:
            chg = float(cur) / float(cost) - 1
            if chg <= -abs(stop_pct):
                emit('stop_loss', 'warn', f'个人止损 {code}',
                     f'{name or code} 现价 {float(cur):.2f} 较成本 {float(cost):.2f} '
                     f'回撤 {chg * 100:.1f}%（阈值 {stop_pct * 100:.0f}%）')

    cap_pct = float(rules.get('industry_cap_pct') or 0)
    if cap_pct > 0:
        rows = db.execute(text("""
            SELECT COALESCE(sm.industry_l1, '未知') AS ind,
                   SUM(p.quantity * q.close) AS mv
            FROM portfolio p
            LEFT JOIN stock_master sm
              ON sm.stock_code=p.stock_code AND sm.stock_type='stock'
            LEFT JOIN LATERAL (
                SELECT close FROM daily_quote q
                WHERE q.stock_code=p.stock_code AND q.close > 0
                ORDER BY q.trade_date DESC LIMIT 1
            ) q ON true
            WHERE p.user_id=:uid AND p.is_active=true AND q.close > 0
            GROUP BY 1
        """), {"uid": uid}).fetchall()
        total_mv = sum(float(r[1] or 0) for r in rows)
        if total_mv > 0:
            for ind, mv in rows:
                ratio = float(mv or 0) / total_mv
                if ratio >= cap_pct:
                    emit('industry_cap', 'warn', f'个人行业上限 {ind}',
                         f'{ind} 持仓市值占比 {ratio * 100:.1f}% '
                         f'（上限 {cap_pct * 100:.0f}%），建议分散')
    return new_alerts


def evaluate_all_user_risk_rules(db, trade_date=None) -> list:
    """DAG 每日评估：遍历所有启用了个人风控规则的用户。"""
    rows = db.execute(text("""
        SELECT u.id FROM users u
        JOIN user_config c ON c.user_id=u.id AND c.config_key=:k AND c.enabled=true
        WHERE u.status='active' ORDER BY u.id
    """), {"k": RULES_CONFIG_KEY}).fetchall()
    out = []
    for (uid,) in rows:
        db.execute(text("SAVEPOINT sp_user_risk"))
        try:
            out.extend(evaluate_user_risk_rules(db, int(uid), trade_date))
            db.execute(text("RELEASE SAVEPOINT sp_user_risk"))
        except Exception as e:
            # 单用户失败只回滚该用户，不能连带丢掉前面用户的告警
            try:
                db.execute(text("ROLLBACK TO SAVEPOINT sp_user_risk"))
                db.execute(text("RELEASE SAVEPOINT sp_user_risk"))
            except Exception:
                pass
            logger.warning(f"[risk] 用户 {uid} 风控评估失败: {str(e)[:160]}")
    return out

