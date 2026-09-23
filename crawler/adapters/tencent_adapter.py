"""腾讯财经分钟K线 adapter（design/06 外延，借鉴 a-stock-data V3.9 §1.2 设计）。

接口：GET {host}/appstock/app/kline/mkline?param={symbol},{m1|m5|m15|m30|m60},,{count}
- 三个入口同一后端、限流独立（实测单入口 ~600 次后返空 JSON）→ 轮换 + 120s 冷却；
- 分钟线只有不复权、只能取最近 ≤320 根、无成交额（第 8 字段是换手率基点 ÷100）；
- 校验：同时刻重复行、行格式变化、param error、北交所直接拒绝（腾讯对 BJ 返回空分钟线）。

实测（2026-09-23）：ifzq.gtimg.cn 与 proxy.finance.qq.com 本机 0.2s 可用；
web.ifzq.gtimg.cn 会 302 到不存在的 web3 子域——入口顺序把可用性差的放末位。
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


def _prefix(code: str) -> str:
    """6 位码 → sh/sz 前缀。北交所不支持（腾讯分钟线为空）。"""
    if code.startswith(('60', '68', '90', '5')):
        return 'sh'
    if code.startswith(('00', '30', '20', '1')):
        return 'sz'
    raise ValueError(f'腾讯分钟线不支持该代码: {code}（北交所分钟线为空）')


def minute_kline(code: str, period: str = 'm5', count: int = 120) -> list[dict]:
    """分钟K线（不复权，最近 count 根 ≤320）。返回 dict 列表：

    {datetime: 'YYYY-MM-DD HH:MM', open, close, high, low, volume(手), turnover_rate_pct}
    """
    period = period.lower()
    if period not in _PERIODS:
        raise ValueError(f'period 仅支持 {"/".join(_PERIODS)}')
    count = int(count)
    if not 1 <= count <= 320:
        raise ValueError('count 范围 1-320')
    symbol = _prefix(code) + code
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
