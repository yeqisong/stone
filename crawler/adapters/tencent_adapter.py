"""腾讯财经行情 adapter（design/06 外延，借鉴 a-stock-data V3.9 §1.2 设计）。

两条链路：
1. 分钟K线：GET {host}/appstock/app/kline/mkline?param={symbol},{m1|m5|m15|m30|m60},,{count}
   - 三个入口同一后端、限流独立（实测单入口 ~600 次后返空 JSON）→ 轮换 + 120s 冷却；
   - 分钟线只有不复权、只能取最近 ≤320 根、无成交额（第 8 字段是换手率基点 ÷100）；
   - 校验：同时刻重复行、行格式变化、param error、北交所直接拒绝（腾讯对 BJ 返回空分钟线）。
2. 实时快照：GET {host}/q={symbol,symbol,...}
   - 个股/指数/ETF 同一接口，支持批量；GBK 编码，字段用 ~ 分隔；
   - 返回现价/昨收/开高低/量额/换手/量比/均价/涨跌停/五档买卖盘；
   - 非官方接口，无 SLA：调用方必须做服务端缓存，禁止前端直连。

实测（2026-09-23）：ifzq.gtimg.cn 与 proxy.finance.qq.com 本机 0.2s 可用；
web.ifzq.gtimg.cn 会 302 到不存在的 web3 子域——入口顺序把可用性差的放末位。
实测（2026-11-11 盘中）：qt.gtimg.cn / sqt.gtimg.cn 快照可用（含指数、ETF）。
"""
import logging
import time
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

# (host, path) —— 同一后端三入口，限流各自独立
_HOSTS = [
    ('https://ifzq.gtimg.cn', '/appstock/app/kline/mkline'),
    ('https://proxy.finance.qq.com', '/ifzqgtimg/appstock/app/kline/mkline'),
    ('https://web.ifzq.gtimg.cn', '/appstock/app/kline/mkline'),
]
_COOLDOWN_S = 120
_PERIODS = ('m1', 'm5', 'm15', 'm30', 'm60')
_down_until: dict[str, float] = {}
_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')


def minute_kline(code: str, period: str = 'm5', count: int = 120,
                 security_type: str = 'stock') -> list[dict]:
    """分钟K线（不复权，最近 count 根 ≤320）。返回 dict 列表：

    {datetime: 'YYYY-MM-DD HH:MM', open, close, high, low, volume(手), turnover_rate_pct}

    security_type='index' 时支持 sh000300/sz399006 等指数（实测可用）；
    北交所个股分钟线为空，会走 RuntimeError。
    """
    period = period.lower()
    if period not in _PERIODS:
        raise ValueError(f'period 仅支持 {"/".join(_PERIODS)}')
    count = int(count)
    if not 1 <= count <= 320:
        raise ValueError('count 范围 1-320')
    symbol = _quote_symbol(code, security_type)
    param = f'{symbol},{period},,{count}'

    errors = []
    for host, path in _HOSTS:
        if _down_until.get(host, 0) > time.time():
            continue
        try:
            r = requests.get(host + path, params={'param': param},
                             headers={'User-Agent': _UA, 'Referer': 'https://gu.qq.com/'},
                             timeout=(5, 15))
        except requests.RequestException as e:
            errors.append(f'{host}: {type(e).__name__}')
            _down_until[host] = time.time() + _COOLDOWN_S
            continue
        try:
            payload = r.json() if r.text.strip() else {}
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            errors.append(f'{host}: 顶层 {type(payload).__name__}')
            _down_until[host] = time.time() + _COOLDOWN_S
            continue
        if payload.get('msg') == 'param error':
            raise ValueError(f'腾讯分钟线参数错误（代码不存在?）: {param}')
        node = (payload.get('data') or {}).get(symbol)
        if not isinstance(node, dict) or not isinstance(node.get(period), list):
            errors.append(f'{host}: 空响应/格式变化')
            _down_until[host] = time.time() + _COOLDOWN_S
            continue

        rows, seen = [], set()
        for item in node[period]:
            try:
                stamp = datetime.strptime(item[0], '%Y%m%d%H%M')
                if stamp in seen:
                    raise ValueError(f'同时刻 {stamp} 重复出现')
                seen.add(stamp)
                rows.append({
                    'datetime': stamp.strftime('%Y-%m-%d %H:%M'),
                    'open': float(item[1]), 'close': float(item[2]),
                    'high': float(item[3]), 'low': float(item[4]),
                    'volume': float(item[5]),                      # 手
                    'turnover_rate_pct': (float(item[7]) / 100      # 换手率基点→%
                                          if len(item) > 7 and str(item[7]).replace('.', '', 1).lstrip('-').isdigit()
                                          else None),
                })
            except (ValueError, TypeError, IndexError) as e:
                # 源格式变化（行变短/类型漂移）——整批丢弃比半批脏数据安全
                raise RuntimeError(f'腾讯分钟线 {symbol} 行格式变化: {e}') from e
        if not rows:
            raise RuntimeError(f'腾讯分钟线 {symbol} {period} 返回 0 根')
        return rows

    raise RuntimeError(f'腾讯分钟线三入口均不可用（疑似限流）: {"; ".join(errors)}')


# ══════════════════════════════════════════
# 实时快照（qt.gtimg.cn）——详情页报价条 / 盘中监测共用
# ══════════════════════════════════════════

_QUOTE_HOSTS = ('https://qt.gtimg.cn', 'https://sqt.gtimg.cn')
_QUOTE_MAX_BATCH = 50


def _quote_symbol(code: str, security_type: str = 'stock') -> str:
    """6 位码 → 腾讯行情代码。指数前缀与个股不同（000300 是 sh000300）。"""
    if security_type == 'index':
        if code.startswith('399'):
            return 'sz' + code
        if code.startswith('899'):
            return 'bj' + code
        return 'sh' + code          # 000/880/930/999 等上证/中证系列
    if code.startswith(('60', '68', '90', '5')):
        return 'sh' + code
    if code.startswith(('00', '30', '20', '1')):
        return 'sz' + code
    if code.startswith(('4', '8', '92')):
        return 'bj' + code          # 快照支持北交所（分钟线不支持，别混用）
    raise ValueError(f'腾讯行情不支持该代码: {code}')


def _qnum(v):
    """行情字段 → float；空串/非法值返回 None（字段漂移不炸整批）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None       # 过滤 nan


def _parse_quote_line(line: str) -> dict | None:
    """解析一行 v_sh600519="..."。返回 None 表示空行/无效代码/格式变化。"""
    if '="' not in line:
        return None
    var, raw = line.split('="', 1)
    raw = raw.strip().rstrip(';').strip('"')
    if not raw:
        return None
    f = raw.split('~')
    if len(f) < 52:                    # 腾讯快照固定 88 字段，过短视为格式变化
        return None
    symbol = var.strip()
    if symbol.startswith('v_'):
        symbol = symbol[2:]
    price = _qnum(f[3])
    prev_close = _qnum(f[4])
    if price is not None and price <= 0:   # 停牌/集合竞价前偶发 0 价，退回昨收
        price = prev_close

    change = _qnum(f[31])
    change_pct = _qnum(f[32])
    if change is None and price is not None and prev_close:
        change = price - prev_close
    if change_pct is None and change is not None and prev_close:
        change_pct = change / prev_close * 100

    stamp = f[30].strip()
    ts = None
    if len(stamp) == 14 and stamp.isdigit():
        try:
            ts = datetime.strptime(stamp, '%Y%m%d%H%M%S')
        except ValueError:
            ts = None

    bids, asks = [], []
    for i in range(5):
        bp, bv = _qnum(f[9 + 2 * i]), _qnum(f[10 + 2 * i])
        if bp:
            bids.append({'price': bp, 'volume': int(bv or 0)})
        ap, av = _qnum(f[19 + 2 * i]), _qnum(f[20 + 2 * i])
        if ap:
            asks.append({'price': ap, 'volume': int(av or 0)})

    amount_wan = _qnum(f[37])          # 第 37 字段单位：万元
    limit_up = _qnum(f[47])
    limit_down = _qnum(f[48])
    return {
        'symbol': symbol,
        'code': f[2] or symbol[2:],
        'name': f[1],
        'price': price,
        'prev_close': prev_close,
        'open': _qnum(f[5]),
        'high': _qnum(f[33]),
        'low': _qnum(f[34]),
        'change': change,
        'change_pct': change_pct,
        'volume': int(_qnum(f[6]) or 0),              # 手
        'amount': amount_wan * 1e4 if amount_wan is not None else None,  # 元
        'turnover_pct': _qnum(f[38]),
        'pe': _qnum(f[39]),
        'amplitude_pct': _qnum(f[43]),
        'circ_mv_yi': _qnum(f[44]),                   # 亿元
        'total_mv_yi': _qnum(f[45]),                  # 亿元
        'pb': _qnum(f[46]),
        'limit_up': limit_up if limit_up and limit_up > 0 else None,
        'limit_down': limit_down if limit_down and limit_down > 0 else None,
        'volume_ratio': _qnum(f[49]),
        'avg_price': _qnum(f[51]),
        'bids': bids,
        'asks': asks,
        'timestamp': ts.strftime('%Y-%m-%d %H:%M:%S') if ts else None,
        'trade_date': ts.strftime('%Y-%m-%d') if ts else None,
    }


def realtime_quotes(codes, security_type: str = 'stock') -> dict[str, dict]:
    """批量实时快照。返回 {symbol: quote}，symbol 形如 sh600519。

    非官方接口：单次 ≤50 只，失败走 qt → sqt 两入口；调用方必须缓存（详情页 3s）。
    """
    if isinstance(codes, str):
        codes = [codes]
    codes = [str(c).strip() for c in codes if str(c).strip()]
    if not codes:
        return {}
    symbols = [_quote_symbol(c, security_type) for c in codes]
    out: dict[str, dict] = {}
    for i in range(0, len(symbols), _QUOTE_MAX_BATCH):
        chunk = symbols[i:i + _QUOTE_MAX_BATCH]
        errors = []
        for host in _QUOTE_HOSTS:
            try:
                r = requests.get(
                    f'{host}/q={",".join(chunk)}',
                    headers={'User-Agent': _UA, 'Referer': 'https://gu.qq.com/'},
                    timeout=(5, 10))
            except requests.RequestException as e:
                errors.append(f'{host}: {type(e).__name__}')
                continue
            text = r.content.decode('gbk', errors='replace')
            parsed = [q for q in (_parse_quote_line(ln) for ln in text.splitlines()) if q]
            if parsed:
                out.update({q['symbol']: q for q in parsed})
                break
            errors.append(f'{host}: 空响应/格式变化')
        else:
            raise RuntimeError(f'腾讯实时快照不可用: {"; ".join(errors)}')
    return out


def realtime_quote(code: str, security_type: str = 'stock') -> dict:
    """单只实时快照；代码不存在/源不可用抛 RuntimeError，前缀非法抛 ValueError。"""
    symbol = _quote_symbol(code, security_type)
    quote = realtime_quotes([code], security_type).get(symbol)
    if not quote:
        raise RuntimeError(f'腾讯实时快照无该代码: {symbol}')
    return quote
