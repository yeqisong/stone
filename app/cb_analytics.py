"""可转债规则分析（纯函数，无 DB/网络依赖）。

- 转股价值/溢价率/双低/纯债溢价/换手/到期天数/强赎观察统一口径。
- 评分是规则筛选，不是预测，也不构成投资建议。
- 输入字段约定：cb_basic/cb_daily/cb_share/daily_quote 的行 dict（可来自 SQLAlchemy row._mapping）。
"""
from datetime import date, datetime

DISCOUNT_RATE = 0.035   # 纯债价值近似折现率
FORCE_LINE_K = 1.3      # 常见强赎线 = 转股价 × 130%
FORCE_LINE_DAYS = 30    # 近 30 个交易日观察窗
FORCE_LINE_MIN = 15     # ≥15 天视为接近/满足强赎条件


def _f(v):
    try:
        if v is None or v == '':
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_date(v):
    if v is None or v == '':
        return None
    if isinstance(v, date):
        return v
    if isinstance(v, datetime):
        return v.date()
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _today(today=None):
    if isinstance(today, date):
        return today
    if today:
        return _parse_date(today) or date.today()
    return date.today()


def days_until(target, today=None):
    d = _parse_date(target)
    if not d:
        return None
    return (d - _today(today)).days


def conversion_value(conv_price, stock_close):
    """转股价值 = 100 / 转股价 × 正股收盘。"""
    conv_price = _f(conv_price)
    stock_close = _f(stock_close)
    if not conv_price or conv_price <= 0 or not stock_close or stock_close <= 0:
        return None
    return 100.0 / conv_price * stock_close


def premium_rate(cb_close, conv_value):
    """转股溢价率（小数）：转债价 / 转股价值 - 1。"""
    cb_close = _f(cb_close)
    conv_value = _f(conv_value)
    if not cb_close or cb_close <= 0 or not conv_value or conv_value <= 0:
        return None
    return cb_close / conv_value - 1


def double_low(cb_close, premium):
    """双低 = 价格 + 溢价率×100。"""
    cb_close = _f(cb_close)
    premium = _f(premium)
    if not cb_close or cb_close <= 0 or premium is None:
        return None
    return cb_close + premium * 100


def pure_bond_value(coupon_rate=None, maturity=None, discount_rate=DISCOUNT_RATE, par=100.0):
    """纯债价值近似（年度付息 + 到期还本；阶梯利率未逐段展开，仅近似）。"""
    coupon = _f(coupon_rate)
    n = _f(maturity)
    if not coupon or coupon <= 0 or not n or n <= 0:
        return None
    full_years = int(n)
    frac = n - full_years
    pv = 0.0
    coupon_cash = coupon / 100.0 * par
    for t in range(1, full_years + 1):
        pv += coupon_cash / (1.0 + discount_rate) ** t
    if frac > 0:
        pv += coupon_cash * frac / (1.0 + discount_rate) ** n
    pv += par / (1.0 + discount_rate) ** n
    return pv


def pure_bond_premium(cb_close, bond_value):
    cb_close = _f(cb_close)
    bond_value = _f(bond_value)
    if not cb_close or cb_close <= 0 or not bond_value or bond_value <= 0:
        return None
    return cb_close / bond_value - 1


def turnover_pct(amount_wan=None, remain_size=None):
    """当日换手率(%) ≈ 成交金额(万元)×10000 / 剩余规模(元)。"""
    amount = _f(amount_wan)
    remain = _f(remain_size)
    if not amount or amount <= 0 or not remain or remain <= 0:
        return None
    return amount * 10000.0 / remain * 100.0


def force_line(conv_price):
    conv_price = _f(conv_price)
    if not conv_price or conv_price <= 0:
        return None
    return conv_price * FORCE_LINE_K


def stock_to_force_pct(stock_close, conv_price):
    """正股收盘距强赎线百分比：>0 表示已站上强赎线。"""
    stock_close = _f(stock_close)
    line = force_line(conv_price)
    if not stock_close or stock_close <= 0 or not line or line <= 0:
        return None
    return stock_close / line - 1


def forced_redemption_days(stock_closes, conv_price):
    """近 FORCE_LINE_DAYS 个交易日里，正股收盘 ≥ 强赎线的天数。"""
    line = force_line(conv_price)
    if not line or line <= 0 or not stock_closes:
        return None
    closes = [c for c in (_f(x) for x in stock_closes[-FORCE_LINE_DAYS:]) if c]
    return sum(1 for c in closes if c >= line)


def price_percentile(price, history):
    """当日收盘在近 N 日历史中的分位（0-100，越高越贵）。"""
    price = _f(price)
    hist = [h for h in (_f(x) for x in (history or [])) if h and h > 0]
    if price is None or price <= 0 or len(hist) < 10:
        return None
    return sum(1 for h in hist if h <= price) / len(hist) * 100.0


def _risk_flags(item):
    """结构性风险标记（用于列表标签与评分扣分）。"""
    flags = []
    price = _f(item.get('price'))
    premium = _f(item.get('premium_rate'))
    remain = _f(item.get('remaining_size'))
    dm = item.get('days_to_maturity')
    dc = item.get('days_to_conv_stop')
    dd = item.get('days_to_delist')
    if price is not None and price >= 150:
        flags.append('高价位')
    if premium is not None and premium >= 0.8:
        flags.append('高溢价')
    if remain is not None and remain < 0.5e8:
        flags.append('规模过小')
    if dm is not None and dm <= 90:
        flags.append('临近到期')
    if dc is not None and dc <= 30:
        flags.append('停止转股临近')
    if dd is not None and dd <= 30:
        flags.append('退市临近')
    tof = item.get('stock_to_force_pct')
    if tof is not None and tof >= 0:
        flags.append('正股站上强赎线')
    return flags


def score_bond(item):
    """规则评分 0-100 + 标签 + 理由（item 需含 price/premium_rate/…）。"""
    score = 50.0
    reasons = []
    price = _f(item.get('price'))
    premium = _f(item.get('premium_rate'))
    dl = _f(item.get('double_low'))
    pure_prem = _f(item.get('pure_bond_premium'))
    remain = _f(item.get('remaining_size'))
    turnover = _f(item.get('turnover_pct'))
    stock_close = _f(item.get('stock_close'))
    ma20 = _f(item.get('stock_ma20'))
    ma60 = _f(item.get('stock_ma60'))

    # 价格：债底保护 vs 高价回撤
    if price is not None:
        if price <= 115:
            score += 8; reasons.append('价格低，债底保护强')
        elif price <= 140:
            score += 4
        elif price <= 150:
            score -= 5; reasons.append('价格偏高')
        else:
            score -= 25; reasons.append('价格过高，回撤空间大')

    # 溢价：低溢价跟随正股弹性
    if premium is not None:
        pp = premium * 100
        if pp <= 10:
            score += 15; reasons.append('溢价率极低')
        elif pp <= 20:
            score += 10; reasons.append('低溢价')
        elif pp <= 40:
            score += 3
        elif pp <= 80:
            score -= 10; reasons.append('溢价偏高')
        else:
            score -= 25; reasons.append('溢价过高')

    # 双低综合
    if dl is not None:
        if dl <= 145:
            score += 12; reasons.append('双低优秀')
        elif dl <= 155:
            score += 6; reasons.append('双低偏优')
        elif dl > 170:
            score -= 10; reasons.append('双低偏高')

    # 纯债保护
    if pure_prem is not None:
        if pure_prem <= 0.1:
            score += 5; reasons.append('纯债溢价低')
        elif pure_prem > 0.4:
            score -= 5; reasons.append('纯债溢价高')

    # 规模：流动性/退市风险 vs 弹性
    if remain is not None:
        if remain < 0.5e8:
            score -= 15; reasons.append('剩余规模过小')
        elif remain < 2e8:
            score -= 3
        elif remain <= 20e8:
            score += 6; reasons.append('剩余规模适中，弹性足')
        elif remain > 50e8:
            score -= 3

    # 正股动量
    if stock_close and ma20 and ma60:
        if stock_close > ma20 > ma60:
            score += 10; reasons.append('正股多头排列')
        elif stock_close < ma20 < ma60:
            score -= 10; reasons.append('正股空头排列')

    # 换手极低：流动性差
    if turnover is not None and turnover < 0.1:
        score -= 3; reasons.append('成交清淡')

    # 期限/退市风险
    dm = item.get('days_to_maturity')
    if dm is not None:
        if dm <= 90:
            score -= 20; reasons.append('临近到期')
        elif dm <= 365:
            score -= 5
    dc = item.get('days_to_conv_stop')
    if dc is not None and dc <= 30:
        score -= 20; reasons.append('停止转股临近')
    dd = item.get('days_to_delist')
    if dd is not None and dd <= 30:
        score -= 25; reasons.append('退市临近')

    risk_flags = _risk_flags(item)
    score = max(0.0, min(100.0, score))
    if score >= 70:
        label = '进攻'
    elif score >= 55:
        label = '均衡'
    elif score >= 40:
        label = '防守'
    else:
        label = '回避'
    return {
        'score': round(score, 1),
        'label': label,
        'reasons': reasons[:8],
        'risk_flags': risk_flags,
    }


def strategy_keys(item):
    """根据阈值把单只债映射到策略池（可多选）。"""
    price = _f(item.get('price'))
    premium = _f(item.get('premium_rate'))
    dl = _f(item.get('double_low'))
    pure_prem = _f(item.get('pure_bond_premium'))
    remain = _f(item.get('remaining_size'))
    stock_close = _f(item.get('stock_close'))
    ma20 = _f(item.get('stock_ma20'))
    keys = []
    if (price is not None and premium is not None and remain is not None
            and price <= 140 and premium <= 0.20 and remain >= 1e8
            and stock_close and ma20 and stock_close > ma20):
        keys.append('low_premium')
    if (price is not None and premium is not None
            and price <= 130 and premium <= 0.40):
        keys.append('double_low')
    if (price is not None and premium is not None and remain is not None
            and 100 <= price <= 150 and premium <= 0.40 and remain <= 5e8):
        keys.append('small_elastic')
    if (price is not None and pure_prem is not None and remain is not None
            and price <= 115 and pure_prem <= 0.20 and remain >= 2e8):
        keys.append('defensive')
    if _risk_flags(item):
        keys.append('risk')
    return keys


STRATEGY_LABELS = {
    'low_premium': '低溢价进攻',
    'double_low': '双低防守',
    'small_elastic': '小盘弹性',
    'defensive': '防守债性',
    'risk': '风险回避',
}


def build_strategy_pools(items):
    """把已计算指标的可转债列表分到策略池，池内按 score 降序。"""
    pools = {k: [] for k in STRATEGY_LABELS}
    for item in items:
        for k in strategy_keys(item):
            pools[k].append(item)
    for k in pools:
        pools[k].sort(key=lambda x: (_f(x.get('score')) or 0) * -1)
    return pools
