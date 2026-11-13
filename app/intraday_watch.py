"""盘中 watcher：实时提醒（P1）+ 分钟数据采集（P3）。

运行方式：app/main.py lifespan 启动 asyncio 后台任务；只在 A 股连续竞价时段
(09:30-11:30 / 13:00-15:00，且 trade_calendar 判定为交易日) 拉取腾讯快照，
其余时间休眠，不占配额、不产生外部请求。两条链路共用同一批快照：
- P1 提醒：evaluate_tick → risk_alerts → 既有 WS 推送；
- P3 采集：10s 快照在分钟切换时闭合成 1 分钟 bar，UPSERT 到 stock_minute_bar，
  供 scripts/train_intraday.py 做分钟级研究（不参与生产信号）。

两个开关独立：enabled（提醒）/ collect_minute（采集）；都关掉才完全休眠。

规则（每条 code+rule 有冷却，默认 30 分钟；跨进程重启靠 risk_alerts.created_at 兜底）：
- it_stop / it_take   持仓成本止损/止盈（portfolio.cost_price，盘中实时价）
- it_limit_up/down    逼近涨停/跌停（默认距限价 0.5% 内，只在状态切换时报）
- it_surge / it_drop  N 分钟急涨/急跌（默认 5 分钟 2%）
- it_high / it_low    日内新高/新低（首轮只建基线不报）
- it_vol              量比异动（默认 2.0，开盘 15 分钟后启用）
- it_vwap_up/down     上穿/下破当日 VWAP（均价线，默认 0.1% 缓冲）
- it_or_up/down       突破/跌破开盘 N 分钟区间（默认 15 分钟；盘中晚于窗口启动则当天不产生 OR 信号）
- it_prev_high/low    突破/跌破昨日最高/最低（取今天之前最近一个有有效价的交易日）

设计取舍：
- 复用 risk_alerts 而不是新表：status.py 的 WS 循环已经在增量推送新告警，
  前端 Chrome 通知/详情页列表都能直接复用；新增 stock_code 列用于按个股过滤。
- 告警按「一只票一次 tick 最多一条」+ 全局每日上限 + 每 tick 上限，避免急跌时刷屏。
- 快照批量接口一次 ≤50 只；关注池通常只有几十到几百只，一轮 1-10 个请求。
- 单进程假设：内存冷却不跨进程共享；DB 冷却查询兜住重启，但多 worker 并发仍可能重复
  插入（当前 uvicorn 单进程部署，未加 advisory lock）。
"""
import asyncio
import json
import time
from collections import deque
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import text

from app.db.connection import get_sync_db
from crawler.adapters.tencent_adapter import _quote_symbol, realtime_quotes

CONFIG_KEY = 'intraday_rules'
DEFAULT_CONFIG = {
    'enabled': True,
    'collect_minute': True,        # P3：是否把 10s 快照合成 1 分钟 bar 落库（与提醒开关独立）
    'interval_s': 10,              # 盘中轮询间隔
    'rapid_window_min': 5,         # 急涨急跌观察窗
    'rapid_move_pct': 2.0,         # 窗口内涨跌幅阈值（%）
    'volume_ratio': 2.0,           # 量比异动阈值
    'volume_ratio_min_minutes': 15,  # 开盘多少分钟后才看量比（避开集合竞价/开盘噪声）
    'near_limit_pct': 0.5,         # 距涨跌停多少 % 内算「逼近」
    'high_low_min_pct': 0.2,       # 新高/新低相对上次提醒位的最小幅度（过滤 0.01 元级噪声）
    'stop_loss_pct': 8.0,          # 持仓成本回撤阈值
    'take_profit_pct': 20.0,       # 持仓成本盈利阈值
    'cooldown_min': 30,            # 同类提醒默认冷却
    'stock_cooldown_min': 5,       # 同股非 critical 提醒冷却（压单股刷屏；止损/跌停/急跌不受限）
    'family_cooldown_min': 15,     # 同族提醒冷却（VWAP 上/下、OR 上/下、昨高/昨低共用一个窗口）
    'max_alerts_per_day': 30,      # 单日盘中提醒上限
    'max_per_tick': 3,             # 单轮最多推几条（急跌时防刷屏，其余下轮再评估）
    # 关键价位突破（VWAP / 开盘区间 / 昨高昨低）
    'vwap_break_enabled': True,
    'vwap_break_min_pct': 0.1,     # 相对 VWAP 的最小穿越幅度；另保底 1 个价位 0.01 元
    'or_break_enabled': True,
    'or_minutes': 15,              # 开盘区间长度（09:30 起算）
    'or_break_min_pct': 0.1,       # 相对 OR 上/下沿的最小突破幅度
    'prev_level_enabled': True,
    'prev_level_min_pct': 0.1,     # 相对昨高/昨低的最小突破幅度
    # P1 事件 AI：默认这些高信号提醒自动触发解读（新高/新低/量比不触发）
    'ai_event_enabled': True,
    'ai_event_kinds': ['it_stop', 'it_take', 'it_limit_up', 'it_limit_down',
                       'it_surge', 'it_drop',
                       'it_vwap_up', 'it_vwap_down', 'it_or_up', 'it_or_down',
                       'it_prev_high', 'it_prev_low'],
    'ai_event_cooldown_min': 15,   # 同股冷却：15 分钟内只解读一次
    'ai_event_max_per_day': 10,    # 全局单日 AI 解读上限
}

# 止损/止盈一天只提醒一次（4 小时覆盖整个连续竞价时段）；新高/新低/量比/关键价位 1 小时
COOLDOWN_MIN = {
    'it_stop': 240, 'it_take': 240,
    'it_high': 60, 'it_low': 60, 'it_vol': 60,
    'it_vwap_up': 30, 'it_vwap_down': 30,
    'it_or_up': 60, 'it_or_down': 60,
    'it_prev_high': 60, 'it_prev_low': 60,
}
# 同族共冷却：避免价格在 VWAP/OR/昨高昨低附近来回穿越时 up/down 交替刷屏
COOLDOWN_FAMILY = {
    'it_vwap_up': 'vwap', 'it_vwap_down': 'vwap',
    'it_or_up': 'or', 'it_or_down': 'or',
    'it_prev_high': 'prev', 'it_prev_low': 'prev',
    'it_high': 'hl', 'it_low': 'hl',
}
FAMILY_KINDS = {}
for _k, _fam in COOLDOWN_FAMILY.items():
    FAMILY_KINDS.setdefault(_fam, []).append(_k)
# 这些高信号不受「同股 5 分钟冷却」限制；否则急涨/止盈可能被前面的 VWAP 提醒压掉
STOCK_COOLDOWN_BYPASS = {'it_stop', 'it_take', 'it_limit_up', 'it_limit_down',
                         'it_drop', 'it_surge'}
PRIORITY = {
    'it_stop': 0, 'it_take': 0, 'it_limit_down': 1, 'it_limit_up': 2,
    'it_drop': 3, 'it_surge': 4,
    'it_prev_low': 5, 'it_or_down': 6, 'it_vwap_down': 7,
    'it_prev_high': 8, 'it_or_up': 9, 'it_vwap_up': 10,
    'it_high': 11, 'it_low': 12, 'it_vol': 13,
}
KIND_LABEL = {
    'it_stop': '触及止损', 'it_take': '触及止盈',
    'it_limit_up': '逼近涨停', 'it_limit_down': '逼近跌停',
    'it_surge': '盘中急涨', 'it_drop': '盘中急跌',
    'it_high': '日内新高', 'it_low': '日内新低', 'it_vol': '量比异动',
    'it_vwap_up': '上穿 VWAP', 'it_vwap_down': '下破 VWAP',
    'it_or_up': '突破开盘区间', 'it_or_down': '跌破开盘区间',
    'it_prev_high': '突破昨高', 'it_prev_low': '跌破昨低',
}
CRITICAL = {'it_stop', 'it_limit_down', 'it_drop'}

_CTX_TTL_S = 60
_ctx = {'ts': 0.0, 'rules': dict(DEFAULT_CONFIG), 'pool': [], 'costs': {},
        'prev_levels': {}, 'trade_day': True}
_trade_day_cache = {'day': None, 'ok': True}
_prev_cache = {'day': None, 'key': None, 'levels': {}}
_state: dict = {}          # code -> 日内状态（高低/量比/限价/采样 + VWAP/OR/昨高昨低侧别）
_started = False


def _bj_now() -> datetime:
    """北京时间（naive），与库内 timestamp 口径一致。"""
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)


def _num(v, default, cast=float):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return default


def _in_session(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return (9 * 60 + 30 <= hm <= 11 * 60 + 30) or (13 * 60 <= hm <= 15 * 60)


def _session_minutes(now: datetime) -> int:
    """09:30=0，11:30=120，13:00=120，15:00=240。"""
    hm = now.hour * 60 + now.minute
    if hm <= 11 * 60 + 30:
        return max(0, hm - (9 * 60 + 30))
    return 120 + max(0, hm - 13 * 60)


def get_intraday_rules(db) -> dict:
    """读取 strategy_config.intraday_rules；无记录时用默认值（启用）。"""
    row = db.execute(text(
        "SELECT params, enabled FROM strategy_config WHERE strategy_name = :k LIMIT 1"
    ), {"k": CONFIG_KEY}).fetchone()
    rules = dict(DEFAULT_CONFIG)
    if row:
        try:
            parsed = json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
            if isinstance(parsed, dict):
                rules.update(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
        rules['enabled'] = bool(row[1]) and bool(rules.get('enabled', True))
    return rules


def save_intraday_rules(db, rules: dict, enabled: bool = True) -> dict:
    merged = {**DEFAULT_CONFIG, **(rules or {}), 'enabled': bool(enabled)}
    db.execute(text("""
        INSERT INTO strategy_config (strategy_name, display_name, enabled, params, updated_by)
        VALUES (:k, '盘中提醒', :e, :p, 'manual')
        ON CONFLICT (strategy_name) DO UPDATE SET
            enabled = EXCLUDED.enabled, params = EXCLUDED.params,
            updated_at = CURRENT_TIMESTAMP
    """), {"k": CONFIG_KEY, "e": merged['enabled'], "p": json.dumps(merged)})
    db.commit()
    _ctx['ts'] = 0.0          # 下一轮立即生效
    return merged


def _is_trade_day(db, day) -> bool:
    key = day.isoformat()
    if _trade_day_cache['day'] == key:
        return _trade_day_cache['ok']
    try:
        v = db.execute(text(
            "SELECT is_trade_day FROM trade_calendar WHERE cal_date=:d AND exchange='SSE'"
        ), {"d": day}).scalar()
        ok = True if v is None else bool(v)
    except Exception:
        ok = True             # 日历没同步时放行，靠快照 trade_date 再兜一层
    _trade_day_cache.update(day=key, ok=ok)
    return ok


def _load_pool(db) -> list:
    """关注池 = 持仓 ∪ 自选；只保留 stock/etf（指数不做盘中提醒）。"""
    rows = db.execute(text("""
        SELECT DISTINCT fp.stock_code, sm.stock_name, sm.stock_type
        FROM v_focus_pool fp
        JOIN LATERAL (
            SELECT stock_name, stock_type FROM stock_master
            WHERE stock_code = fp.stock_code AND stock_type IN ('stock', 'etf')
            ORDER BY CASE stock_type WHEN 'stock' THEN 1 ELSE 2 END
            LIMIT 1
        ) sm ON true
        ORDER BY fp.stock_code
    """)).fetchall()
    return [{'code': r[0], 'name': r[1] or r[0], 'type': r[2] or 'stock'} for r in rows]


def _load_costs(db) -> dict:
    rows = db.execute(text(
        "SELECT stock_code, stock_name, cost_price FROM portfolio "
        "WHERE is_active = true AND cost_price > 0"
    )).fetchall()
    return {r[0]: {'name': r[1], 'cost': float(r[2])} for r in rows}


def _load_prev_levels(db, codes: list, day) -> dict:
    """昨高/昨低：每个 code 取 day 之前最近一个有有效价的交易日。

    按「日期 + 代码集合」缓存，池子不变时一天只查一次；查询失败不写缓存，
    下一轮（60s 后）自动重试。
    """
    key = ','.join(sorted(codes))
    day_key = day.isoformat()
    if _prev_cache['day'] == day_key and _prev_cache['key'] == key:
        return _prev_cache['levels']
    levels = {}
    if codes:
        try:
            rows = db.execute(text("""
                SELECT DISTINCT ON (stock_code) stock_code, high, low, trade_date
                FROM daily_quote
                WHERE stock_code = ANY(CAST(:codes AS varchar[]))
                  AND trade_date < :d
                  AND close > 0 AND high > 0 AND low > 0
                ORDER BY stock_code, trade_date DESC
            """), {'codes': list(codes), 'd': day}).fetchall()
            levels = {
                r[0]: {'high': float(r[1]), 'low': float(r[2]), 'date': str(r[3])[:10]}
                for r in rows
            }
        except Exception as e:
            logger.warning(f"[intraday] 昨高昨低加载失败: {str(e)[:120]}")
            return {}
    _prev_cache.update(day=day_key, key=key, levels=levels)
    return levels


def _load_context() -> dict:
    now = time.monotonic()
    if now - _ctx['ts'] < _CTX_TTL_S:
        return _ctx
    db = get_sync_db()
    try:
        pool = _load_pool(db)
        _ctx.update(
            ts=now,
            rules=get_intraday_rules(db),
            pool=pool,
            costs=_load_costs(db),
            prev_levels=_load_prev_levels(db, [p['code'] for p in pool], _bj_now().date()),
            trade_day=_is_trade_day(db, _bj_now().date()),
        )
    except Exception as e:
        logger.warning(f"[intraday] 上下文加载失败，沿用旧缓存: {e}")
    finally:
        db.close()
    return _ctx


def _fetch_quotes(pool: list) -> dict:
    codes = []
    for p in pool:
        try:
            _quote_symbol(p['code'], 'stock')     # 前缀校验；非法代码整批会炸
            codes.append(p['code'])
        except ValueError:
            continue
    if not codes:
        return {}
    try:
        raw = realtime_quotes(codes, 'stock')
    except Exception as e:
        logger.warning(f"[intraday] 快照拉取失败: {e}")
        return {}
    return {q['code']: q for q in raw.values()}


def _fmt(v) -> str:
    return f'{v:.2f}' if v is not None else '—'


def _cand(code, name, kind, body) -> dict:
    label = KIND_LABEL.get(kind, kind)
    return {
        'stock_code': code,
        'kind': kind,
        'level': 'critical' if kind in CRITICAL else 'warn',
        'title': f'{name}({code}) {label}'[:100],
        'body': body,
    }


def _break_side(price: float, level: float, min_pct: float):
    """price 相对单一价位（VWAP/昨高/昨低）的侧别；0=缓冲区内。

    缓冲取 max(level*min_pct%, 0.01 元)：低价股不会因为不足一个价位反复穿越。
    """
    if not level or level <= 0:
        return None
    buf = max(level * max(0.0, min_pct) / 100, 0.01)
    if price >= level + buf:
        return 1
    if price <= level - buf:
        return -1
    return 0


def _prev_side(price: float, prev: dict | None, min_pct: float):
    """price 相对昨高/昨低的侧别；昨高之上=1，昨低之下=-1，区间内=0。"""
    if not prev:
        return None
    hi, lo = prev.get('high'), prev.get('low')
    if hi and price >= hi + max(hi * max(0.0, min_pct) / 100, 0.01):
        return 1
    if lo and price <= lo - max(lo * max(0.0, min_pct) / 100, 0.01):
        return -1
    return 0


def _or_side(price: float, hi, lo, min_pct: float):
    if not hi or not lo or hi <= lo:
        return None
    if price >= hi + max(hi * max(0.0, min_pct) / 100, 0.01):
        return 1
    if price <= lo - max(lo * max(0.0, min_pct) / 100, 0.01):
        return -1
    return 0


def _or_window_min(rules: dict) -> int:
    """开盘区间长度：最多到上午收盘（120 分钟），避免把午后行情算进 OR。"""
    return min(120, max(1, _num(rules.get('or_minutes'), 15, int)))


def _update_or_state(st: dict, q: dict, price: float, rules: dict, now: datetime):
    """累计开盘 N 分钟区间。必须在任何 `continue` 之前调用，否则前 15 分钟会被止损/急涨跳过。"""
    if not rules.get('or_break_enabled', True) or st.get('or_unavailable'):
        return
    or_min = _or_window_min(rules)
    mins = _session_minutes(now)
    if st.get('or_high') is None or st.get('or_low') is None:
        if mins < or_min:
            st['or_high'] = q.get('high') or price
            st['or_low'] = q.get('low') or price
        else:
            # 盘中晚于 OR 窗口启动：当天无法还原区间，直接标记不可用
            st['or_unavailable'] = True
        return
    if st.get('or_ready'):
        return
    if mins < or_min:
        st['or_high'] = max(st['or_high'], q.get('high') or price)
        st['or_low'] = min(st['or_low'], q.get('low') or price)
    elif st['or_high'] > st['or_low']:
        st['or_ready'] = True
    else:
        st['or_unavailable'] = True


def evaluate_tick(quotes: dict, state: dict, costs: dict, rules: dict,
                  now: datetime, prev_levels: dict | None = None) -> list:
    """纯规则评估：返回候选告警（未落库、未查冷却）。同一只票一次最多一条。

    state 会被就地更新（日内高低基线、量比状态、急涨急跌采样、
    VWAP/OR/昨高昨低侧别）。prev_levels: code -> {high, low, date}。
    """
    today = now.strftime('%Y-%m-%d')
    now_ts = now.timestamp()
    rapid_window = max(1, _num(rules.get('rapid_window_min'), 5, int))
    rapid_pct = _num(rules.get('rapid_move_pct'), 2.0)
    vol_th = _num(rules.get('volume_ratio'), 2.0)
    vol_min_min = _num(rules.get('volume_ratio_min_minutes'), 15, int)
    near_pct = _num(rules.get('near_limit_pct'), 0.5)
    hl_min = max(0.0, _num(rules.get('high_low_min_pct'), 0.2))
    stop_pct = _num(rules.get('stop_loss_pct'), 8.0)
    take_pct = _num(rules.get('take_profit_pct'), 20.0)
    vwap_pct = max(0.0, _num(rules.get('vwap_break_min_pct'), 0.1))
    or_pct = max(0.0, _num(rules.get('or_break_min_pct'), 0.1))
    prev_pct = max(0.0, _num(rules.get('prev_level_min_pct'), 0.1))
    prev_levels = prev_levels or {}
    out = []

    for code, q in quotes.items():
        price = q.get('price')
        if not price or price <= 0:
            continue
        # 停牌/非交易日：快照的 trade_date 不是今天 → 所有规则都不该响
        if q.get('trade_date') != today:
            continue

        st = state.get(code)
        first = st is None or st.get('day') != today
        if first:
            hi = q.get('high') or price
            lo = q.get('low') or price
            st = {'day': today, 'high': hi, 'low': lo, 'vol_hot': False,
                  'limit_near': None, 'samples': deque(),
                  'vwap_side': None, 'prev_side': None,
                  'or_high': None, 'or_low': None, 'or_ready': False,
                  'or_unavailable': False, 'or_side': 0}
            state[code] = st
        name = (costs.get(code) or {}).get('name') or q.get('name') or code
        st['samples'].append((now_ts, price))
        while st['samples'] and st['samples'][0][0] < now_ts - 15 * 60:
            st['samples'].popleft()

        # 开盘区间必须在任何 continue 之前累计（否则前 15 分钟会被止损/急涨规则跳过）
        _update_or_state(st, q, price, rules, now)
        prev = prev_levels.get(code)
        if first:
            # 首轮只建基线，不报穿越（避免启动瞬间把「当前就在价位上方」当突破）
            st['vwap_side'] = _break_side(price, q.get('avg_price'), vwap_pct)
            st['prev_side'] = _prev_side(price, prev, prev_pct)

        # 1) 持仓止损/止盈（真实价 vs 持仓成本价，均不复权）
        pos = costs.get(code) or {}
        cost = pos.get('cost')
        if cost and stop_pct > 0:
            chg = price / cost - 1
            if chg <= -stop_pct / 100:
                out.append(_cand(code, name, 'it_stop',
                                 f'现价 {_fmt(price)}，成本 {_fmt(cost)}，回撤 {chg * 100:.2f}%'
                                 f'（阈值 {stop_pct:.1f}%）'))
                continue
            if take_pct > 0 and chg >= take_pct / 100:
                out.append(_cand(code, name, 'it_take',
                                 f'现价 {_fmt(price)}，成本 {_fmt(cost)}，盈利 +{chg * 100:.2f}%'
                                 f'（阈值 {take_pct:.1f}%）'))
                continue

        # 2) 逼近涨跌停：只在状态切换时报，避免全天横在涨停反复推
        lu, ld = q.get('limit_up'), q.get('limit_down')
        near_up = bool(lu and price >= lu * (1 - near_pct / 100))
        near_down = bool(ld and price <= ld * (1 + near_pct / 100))
        if near_up or near_down:
            if near_up and st['limit_near'] != 'up':
                st['limit_near'] = 'up'
                out.append(_cand(code, name, 'it_limit_up',
                                 f'现价 {_fmt(price)}，涨停价 {_fmt(lu)}，'
                                 f'距涨停 {(lu / price - 1) * 100:.2f}%'))
            elif near_down and st['limit_near'] != 'down':
                st['limit_near'] = 'down'
                out.append(_cand(code, name, 'it_limit_down',
                                 f'现价 {_fmt(price)}，跌停价 {_fmt(ld)}，'
                                 f'距跌停 {(price / ld - 1) * 100:.2f}%'))
            # 已在限价附近就只保留这一条，不再叠加急涨/新高/量比
            continue
        st['limit_near'] = None

        # 3) N 分钟急涨/急跌：采样不足窗口的 80% 不报（避免启动后立刻误报）
        win = [s for s in st['samples'] if s[0] >= now_ts - rapid_window * 60]
        if len(win) >= 2 and (now_ts - win[0][0]) >= rapid_window * 60 * 0.8:
            base = win[0][1]
            chg = price / base - 1
            if chg * 100 >= rapid_pct:
                out.append(_cand(code, name, 'it_surge',
                                 f'现价 {_fmt(price)}，{rapid_window} 分钟前 {_fmt(base)}，'
                                 f'涨幅 +{chg * 100:.2f}%（阈值 {rapid_pct:.1f}%）'))
                continue
            if chg * 100 <= -rapid_pct:
                out.append(_cand(code, name, 'it_drop',
                                 f'现价 {_fmt(price)}，{rapid_window} 分钟前 {_fmt(base)}，'
                                 f'跌幅 {chg * 100:.2f}%（阈值 {rapid_pct:.1f}%）'))
                continue

        # 4) 昨高/昨低突破（结构性价位，优先于 OR/VWAP）
        if rules.get('prev_level_enabled', True) and prev:
            side = _prev_side(price, prev, prev_pct)
            if side is not None:
                if st.get('prev_side') is None:
                    st['prev_side'] = side      # 兼容旧状态：首轮只建基线
                elif side != 0 and side != st['prev_side']:
                    st['prev_side'] = side
                    if side > 0:
                        diff = (price / prev['high'] - 1) * 100
                        out.append(_cand(code, name, 'it_prev_high',
                                         f"现价 {_fmt(price)}，昨高 {_fmt(prev['high'])}"
                                         f"（{prev.get('date') or '—'}），突破 +{diff:.2f}%"))
                    else:
                        diff = (prev['low'] / price - 1) * 100
                        out.append(_cand(code, name, 'it_prev_low',
                                         f"现价 {_fmt(price)}，昨低 {_fmt(prev['low'])}"
                                         f"（{prev.get('date') or '—'}），跌破 -{diff:.2f}%"))
                    continue

        # 5) 开盘区间（OR）突破
        if rules.get('or_break_enabled', True) and st.get('or_ready'):
            side = _or_side(price, st.get('or_high'), st.get('or_low'), or_pct)
            if side is not None:
                if st.get('or_side') is None:
                    st['or_side'] = 0
                if side != 0 and side != st.get('or_side'):
                    st['or_side'] = side
                    or_hi, or_lo = st['or_high'], st['or_low']
                    or_min = _or_window_min(rules)
                    if side > 0:
                        diff = (price / or_hi - 1) * 100
                        out.append(_cand(code, name, 'it_or_up',
                                         f'现价 {_fmt(price)}，开盘 {or_min} 分钟区间 '
                                         f'{_fmt(or_lo)}-{_fmt(or_hi)}，突破 +{diff:.2f}%'))
                    else:
                        diff = (or_lo / price - 1) * 100
                        out.append(_cand(code, name, 'it_or_down',
                                         f'现价 {_fmt(price)}，开盘 {or_min} 分钟区间 '
                                         f'{_fmt(or_lo)}-{_fmt(or_hi)}，跌破 -{diff:.2f}%'))
                    continue

        # 6) VWAP（均价线）上穿/下破
        if rules.get('vwap_break_enabled', True):
            vwap = q.get('avg_price')
            side = _break_side(price, vwap, vwap_pct)
            if side is not None:
                if st.get('vwap_side') is None:
                    st['vwap_side'] = side      # 兼容旧状态：首轮只建基线
                elif side != 0 and side != st['vwap_side']:
                    st['vwap_side'] = side
                    diff = abs(price / vwap - 1) * 100
                    out.append(_cand(code, name,
                                     'it_vwap_up' if side > 0 else 'it_vwap_down',
                                     f"现价 {_fmt(price)}，{'上穿' if side > 0 else '下破'} "
                                     f'VWAP {_fmt(vwap)}（{diff:.2f}%）'))
                    continue

        # 7) 日内新高/新低：首轮只建基线；状态始终更新，冷却只影响是否推送
        hi, lo = q.get('high'), q.get('low')
        if not first:
            # 只在相对上次提醒位走出 min_pct 时才报：开盘 0.01 元级的新高/新低是噪声，
            # 阈值内的微突破不更新基线，让涨幅累积到阈值后一次触发
            if hi and hi > st['high'] * (1 + hl_min / 100):
                st['high'] = hi
                out.append(_cand(code, name, 'it_high',
                                 f'现价 {_fmt(price)}，日内新高 {_fmt(hi)}'))
                continue
            if lo and lo < st['low'] * (1 - hl_min / 100):
                st['low'] = lo
                out.append(_cand(code, name, 'it_low',
                                 f'现价 {_fmt(price)}，日内新低 {_fmt(lo)}'))
                continue
        else:
            st['high'] = max(st['high'], hi or price)
            st['low'] = min(st['low'], lo or price)

        # 8) 量比异动：只在从低位上穿阈值时报一次
        vr = q.get('volume_ratio')
        if vr is not None and _session_minutes(now) >= vol_min_min:
            if vr >= vol_th and not st['vol_hot']:
                st['vol_hot'] = True
                out.append(_cand(code, name, 'it_vol',
                                 f'现价 {_fmt(price)}，量比 {vr:.2f}（阈值 {vol_th:.1f}）'))
            elif vr < vol_th - 0.2:
                st['vol_hot'] = False

    return out


def _persist(candidates: list, rules: dict, now: datetime) -> int:
    """冷却/单日上限过滤后写 risk_alerts；返回实际插入条数。"""
    if not candidates:
        return 0
    max_day = max(0, _num(rules.get('max_alerts_per_day'), 30, int))
    max_tick = max(1, _num(rules.get('max_per_tick'), 3, int))
    cooldown_default = max(1, _num(rules.get('cooldown_min'), 30, int))
    stock_cd = max(0, _num(rules.get('stock_cooldown_min'), 5, int))
    family_cd = max(0, _num(rules.get('family_cooldown_min'), 15, int))
    candidates = sorted(candidates, key=lambda c: PRIORITY.get(c['kind'], 9))[:max_tick]
    ai_enabled = bool(rules.get('ai_event_enabled', True))
    ai_kinds = set(rules.get('ai_event_kinds') or [])
    to_enqueue = []

    db = get_sync_db()
    inserted = 0
    try:
        count = db.execute(text(
            "SELECT count(*) FROM risk_alerts WHERE trade_date=:d AND kind LIKE 'it%'"
        ), {"d": now.date()}).scalar() or 0
        for c in candidates:
            # 单日上限用于压噪声，不压止损/跌停/急跌等 critical 告警
            if count >= max_day and not (max_day > 0 and c['level'] == 'critical'):
                continue
            cd = COOLDOWN_MIN.get(c['kind'], cooldown_default)
            cutoff = now - timedelta(minutes=cd)
            seen = db.execute(text(
                "SELECT 1 FROM risk_alerts WHERE stock_code=:c AND kind=:k "
                "AND created_at >= :cut LIMIT 1"
            ), {"c": c['stock_code'], "k": c['kind'], "cut": cutoff}).fetchone()
            if seen:
                continue
            # 同族冷却：VWAP 上/下、OR 上/下、昨高/昨低共享窗口，压住 30 秒内反向穿越
            fam = COOLDOWN_FAMILY.get(c['kind'])
            if fam and family_cd:
                fam_cut = now - timedelta(minutes=family_cd)
                seen_fam = db.execute(text(
                    "SELECT 1 FROM risk_alerts WHERE stock_code=:c "
                    "AND kind = ANY(CAST(:ks AS varchar[])) AND created_at >= :cut LIMIT 1"
                ), {"c": c['stock_code'], "ks": FAMILY_KINDS.get(fam, []),
                    "cut": fam_cut}).fetchone()
                if seen_fam:
                    continue
            # 同股冷却：只压结构化/噪声类提醒；止损/止盈/涨跌停/急涨急跌不受限
            if stock_cd and c['kind'] not in STOCK_COOLDOWN_BYPASS:
                stock_cut = now - timedelta(minutes=stock_cd)
                seen_stock = db.execute(text(
                    "SELECT 1 FROM risk_alerts WHERE stock_code=:c AND trade_date=:d "
                    "AND kind LIKE 'it%' AND created_at >= :cut LIMIT 1"
                ), {"c": c['stock_code'], "d": now.date(), "cut": stock_cut}).fetchone()
                if seen_stock:
                    continue
            row = db.execute(text(
                "INSERT INTO risk_alerts "
                "(trade_date, kind, stock_code, level, title, body, created_at) "
                "VALUES (:d, :k, :c, :lv, :t, :b, :ts) RETURNING id"
            ), {"d": now.date(), "k": c['kind'], "c": c['stock_code'],
                "lv": c['level'], "t": c['title'], "b": c['body'], "ts": now}).fetchone()
            count += 1
            inserted += 1
            logger.info(f"[intraday] {c['title']} | {c['body']}")
            if row and ai_enabled and c['kind'] in ai_kinds:
                to_enqueue.append({
                    'alert_id': row[0],
                    'stock_code': c['stock_code'],
                    'kind': c['kind'],
                    'title': c['title'],
                    'body': c['body'],
                })
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"[intraday] 告警落库失败: {e}")
        return 0
    finally:
        db.close()

    # commit 之后再入队：worker 需要能读到已提交的 alert 行
    if to_enqueue:
        try:
            from app.intraday_ai import enqueue_ai_event
            for item in to_enqueue:
                enqueue_ai_event(item)
        except Exception as e:
            logger.warning(f"[intraday] 事件 AI 入队失败: {str(e)[:120]}")
    return inserted


# ══════════════════════════════════════════
# P3：10s 快照 → 1 分钟 bar（只落最终 bar，分钟切换/收盘 flush）
# ══════════════════════════════════════════

_bar_state: dict = {}      # code -> 当前未完成分钟 bar


def _new_bar(q: dict, price: float, minute: datetime) -> dict:
    return {
        'stock_code': q['code'], 'bar_time': minute,
        'open': price, 'high': price, 'low': price, 'close': price,
        'first_vol': q.get('volume'), 'first_amount': q.get('amount'),
        'last_vol': q.get('volume'), 'last_amount': q.get('amount'),
        'vwap': q.get('avg_price'), 'volume_ratio': q.get('volume_ratio'),
        'snapshots': 1,
    }


def _update_bar(st: dict, q: dict, price: float):
    st['high'] = max(st['high'], price)
    st['low'] = min(st['low'], price)
    st['close'] = price
    st['last_vol'] = q.get('volume')
    st['last_amount'] = q.get('amount')
    st['vwap'] = q.get('avg_price')
    st['volume_ratio'] = q.get('volume_ratio')
    st['snapshots'] += 1


def _finalize_bar(st: dict, end_vol=None, end_amount=None) -> dict:
    """闭合成一根 bar。end_vol/amount 用下一分钟首个快照的累计值作边界；
    收盘 flush 时传空，退回本分钟最后一个快照（最后一根轻微少算尾部几秒）。"""
    ev = end_vol if end_vol is not None else st['last_vol']
    ea = end_amount if end_amount is not None else st['last_amount']
    volume = amount = None
    if ev is not None and st['first_vol'] is not None:
        volume = max(0, int(ev) - int(st['first_vol']))
    if ea is not None and st['first_amount'] is not None:
        amount = max(0.0, float(ea) - float(st['first_amount']))
    return {
        'stock_code': st['stock_code'], 'bar_time': st['bar_time'],
        'open': st['open'], 'high': st['high'], 'low': st['low'], 'close': st['close'],
        'volume': volume, 'amount': amount,
        'vwap': st['vwap'], 'volume_ratio': st['volume_ratio'],
        'snapshots': st['snapshots'],
    }


def _collect_bars(quotes: dict, now: datetime) -> list:
    """把本轮快照并入当前分钟 bar；分钟切换时返回上一根（缺口>1分钟则丢弃，避免跨分钟量价错配）。"""
    today = now.strftime('%Y-%m-%d')
    minute = now.replace(second=0, microsecond=0)
    rows = []
    for code, q in quotes.items():
        price = q.get('price')
        if not price or price <= 0 or q.get('trade_date') != today:
            continue
        st = _bar_state.get(code)
        if st is None:
            _bar_state[code] = _new_bar(q, price, minute)
            continue
        gap = (minute - st['bar_time']).total_seconds() / 60
        if gap >= 1:
            if gap == 1:
                rows.append(_finalize_bar(st, q.get('volume'), q.get('amount')))
            else:
                logger.debug(f'[intraday] {code} 分钟缺口 {gap:.0f}min，丢弃 '
                             f'{st["bar_time"]} 的 bar（避免跨分钟量价错配）')
            _bar_state[code] = _new_bar(q, price, minute)
        else:
            _update_bar(st, q, price)
    return rows


def _save_bars(rows: list) -> int:
    if not rows:
        return 0
    db = get_sync_db()
    try:
        for r in rows:
            db.execute(text("""
                INSERT INTO stock_minute_bar
                (stock_code, bar_time, open, high, low, close, volume, amount,
                 vwap, volume_ratio, snapshots)
                VALUES (:stock_code, :bar_time, :open, :high, :low, :close, :volume,
                        :amount, :vwap, :volume_ratio, :snapshots)
                ON CONFLICT (stock_code, bar_time) DO UPDATE SET
                    open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                    close=EXCLUDED.close, volume=EXCLUDED.volume,
                    amount=EXCLUDED.amount, vwap=EXCLUDED.vwap,
                    volume_ratio=EXCLUDED.volume_ratio, snapshots=EXCLUDED.snapshots,
                    updated_at=CURRENT_TIMESTAMP
            """), r)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f'[intraday] 分钟 bar 落库失败: {e}')
        return 0
    finally:
        db.close()
    return len(rows)


def _flush_bars() -> list:
    rows = [_finalize_bar(st) for st in _bar_state.values() if st['snapshots'] > 0]
    _bar_state.clear()
    return rows


def _flush_and_save() -> int:
    return _save_bars(_flush_bars())


def _tick(now: datetime, rules: dict | None = None) -> int:
    ctx = _load_context()
    rules = rules or ctx['rules']
    quotes = _fetch_quotes(ctx['pool'])
    if not quotes:
        return 0
    if rules.get('collect_minute', True):
        _save_bars(_collect_bars(quotes, now))
    if not rules.get('enabled'):
        return 0
    candidates = evaluate_tick(quotes, _state, ctx['costs'], rules, now,
                               ctx.get('prev_levels'))
    return _persist(candidates, rules, now)


async def intraday_watch_loop():
    """lifespan 后台任务：盘中每 interval_s 秒评估一次，非交易时段 60s 醒一次。"""
    global _started
    if _started:
        return
    _started = True
    logger.info("[intraday] 盘中提醒 + 分钟数据 watcher 启动")
    was_session = False
    try:
        while True:
            try:
                now = _bj_now()
                ctx = await asyncio.to_thread(_load_context)
                rules = ctx['rules']
                collect = bool(rules.get('collect_minute', True))
                active = bool(rules.get('enabled')) or collect
                in_session = active and ctx['trade_day'] and _in_session(now)
                if not in_session:
                    if was_session:
                        await asyncio.to_thread(_flush_and_save)
                        was_session = False
                    await asyncio.sleep(20 if collect else 60)
                    continue
                was_session = True
                await asyncio.to_thread(_tick, now, rules)
                await asyncio.sleep(max(5, _num(rules.get('interval_s'), 10, int)))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[intraday] 循环异常: {e}")
                await asyncio.sleep(30)
    finally:
        try:
            _flush_and_save()          # 进程退出时把最后一根未完成 bar 落盘
        except Exception as e:
            logger.warning(f"[intraday] 退出前 flush 失败: {e}")
        _started = False
        logger.info("[intraday] 盘中提醒 + 分钟数据 watcher 停止")
