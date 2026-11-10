---
name: intraday-realtime
title: 实时分时（盘中）
description: 获取个股当日盘中实时分时数据——现价、涨跌幅、分时均价、分钟量能、量比、日内高低点与最近 30 分钟异动，可出分时图。用户问「现在多少钱 / 今天走势怎么样 / 盘中放量了吗 / 分时图 / 尾盘有没有异动」时使用。
---

# 实时分时（盘中）

## 何时用
- 「茅台现在多少」「今天走势怎么样」「盘中放量了吗」「分时图看一下」
- 「尾盘是不是有异动」「日内高点/低点在哪」「现价在均价线上方吗」
- 盘前/盘后问「今天」时：自动落到最近交易日，并明确标注「非实时」

**不适用**（改用其他数据源，别硬套本技能）：
| 需求 | 正确来源 |
|---|---|
| 指数盘中分时（上证/沪深300） | 项目未接入，只有 `index_daily_quote` 日线 |
| 北交所个股 | 腾讯分钟线为空，只有日线 |
| 超过最近 320 根的历史分时 | 项目未存分钟表，取不到 |
| 主力资金/大单方向 | `stock_moneyflow`（日频，T+1） |
| 日 K、复权收益、技术指标 | `daily_quote` + `close_hfq` / `feature_values` |

## 数据源与口径（先看，避免答错）
- **唯一实时源**：`crawler.adapters.tencent_adapter.minute_kline`——详情页 `/api/stock/{code}/minkline` 用的同一条链路，纯代理**不入库**。
- 分钟线**不复权**；对比昨收必须用 `daily_quote.close`（真实价），**不要用 close_hfq**。
- 分钟成交量单位是**手**（×100 = 股）；`daily_quote.volume` 是**股**，两边不可直接相加。
- 接口**没有逐分钟成交额和买卖方向**，成交额只能按典型价估算，不能据此推断「主力」。
- 只返回**最近 ≤320 根**：`m1` 覆盖约 1.3 个交易日（今天 + 昨日尾段），`m5` 覆盖约 6.7 个交易日。
- 三个入口轮换、单入口约 600 次后冷却 120s：**一个问题只取一次**，不要写轮询循环；多只股票间隔 0.3s、最多 10 只。

## 步骤

1. **确认代码**（用户给名称时）：
   ```sql
   SELECT stock_code, stock_name FROM stock_master
   WHERE stock_name LIKE '%关键词%' AND stock_type='stock' AND status='N' LIMIT 10
   ```
   同名/多只时先跟用户确认。代码必须是 6 位 sh/sz（60/68/00/30/5/1 开头）；
   **指数代码不要传**——`000001` 会被腾讯当成平安银行（`sz000001`），不是上证指数。

2. **沙箱跑下面的模板**（`run_python`），按用户问题改 `CODE`；要 5 分钟粒度就改 `PERIOD='m5'`。

3. **读结果**：JSON 是标准口径；「最近 10 根」用于回答尾盘/刚刚发生了什么。需要图时再执行模板末尾的分时图段
   （先 `pip_install(['matplotlib'])`，会话内只需装一次）。

## 代码模板

```python
# ── 实时分时：腾讯分钟线（与详情页 /api/stock/{code}/minkline 同源） ──
import sys, json
from pathlib import Path
from datetime import datetime, timedelta, timezone

# 沙箱 cwd 是 data/agent_ws/<session>，向上找到项目根
ROOT = Path.cwd()
while ROOT != ROOT.parent and not (ROOT / 'crawler' / 'adapters' / 'tencent_adapter.py').exists():
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / '.env')          # 沙箱 cwd 不是项目根；必须在 import app.* 之前

from crawler.adapters.tencent_adapter import minute_kline
from app.db.connection import get_sync_db
from sqlalchemy import text

CODE = '600519'                     # ← 只改这里（6 位 sh/sz；北交所/指数不支持）
PERIOD = 'm1'                       # m1/m5/m15/m30/m60
COUNT = 320                         # 上限 320

bars = minute_kline(CODE, PERIOD, COUNT)     # 实时源；只取一次，别轮询

# 最近交易日 = 最后一根 bar 的日期（盘前/停牌时不是今天，不能硬用 date.today）
day = bars[-1]['datetime'][:10]
sess = [b for b in bars if b['datetime'][:10] == day]
last = sess[-1]

db = get_sync_db()
try:
    prev = db.execute(text("""
        SELECT trade_date, close FROM daily_quote
        WHERE stock_code=:c AND trade_date < CAST(:d AS date) AND close > 0
        ORDER BY trade_date DESC LIMIT 1"""), {'c': CODE, 'd': day}).fetchone()
    vols = db.execute(text("""
        SELECT volume FROM daily_quote
        WHERE stock_code=:c AND trade_date < CAST(:d AS date) AND close > 0 AND volume > 0
        ORDER BY trade_date DESC LIMIT 5"""), {'c': CODE, 'd': day}).fetchall()
    name = db.execute(text(
        "SELECT stock_name FROM stock_master WHERE stock_code=:c"), {'c': CODE}).scalar()
finally:
    db.rollback(); db.close()       # 读完释放事务，避免长事务持锁

prev_close = float(prev[1]) if prev else None
avg_vol = sum(float(r[0]) for r in vols) / len(vols) if vols else None   # 股

# ── 统计 ──
prices = [b['close'] for b in sess]
high, low = max(b['high'] for b in sess), min(b['low'] for b in sess)
high_t = next(b['datetime'][11:] for b in sess if b['high'] == high)
low_t = next(b['datetime'][11:] for b in sess if b['low'] == low)
cum_lots = sum(b['volume'] for b in sess)                 # 手
cum_shares = cum_lots * 100                               # 股
amount = sum((b['high'] + b['low'] + b['close']) / 3 * b['volume'] * 100 for b in sess)  # 元（估算）
vwap = amount / cum_shares if cum_shares else None

def elapsed(hhmm: str) -> int:      # 09:30 竞价 = 0，11:30 = 120，13:01 = 121，15:00 = 240
    h, m = map(int, hhmm.split(':'))
    t = h * 60 + m
    return max(0, t - 9 * 60 - 30) if t <= 11 * 60 + 30 else 120 + t - 13 * 60

def pct(a, b):
    return round((a / b - 1) * 100, 2) if a is not None and b else None

m = elapsed(last['datetime'][11:])
# 量比（同期口径）：当前累计量 /（近 5 日日均量 × 时间进度）；开盘 15 分钟内不计
vol_ratio = round(cum_shares / (avg_vol * m / 240), 2) if avg_vol and m >= 15 else None

# 最近 30 分钟（盘中即「截至现在」；早盘不足 30 分钟就是全部）
tail = [b for b in sess if elapsed(b['datetime'][11:]) >= m - 29]
tail_chg = pct(tail[-1]['close'], tail[0]['open']) if len(tail) > 1 else None
tail_share = round(sum(b['volume'] for b in tail) / cum_lots * 100, 1) if cum_lots else None

bj_now = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
last_dt = datetime.strptime(last['datetime'], '%Y-%m-%d %H:%M')
gap = (bj_now - last_dt).total_seconds()
live = -60 <= gap <= 600            # 最后一根 bar 在 10 分钟内 = 实时

hm = bj_now.hour * 60 + bj_now.minute
if day != bj_now.strftime('%Y-%m-%d'):
    status = '非实时（最近交易日/停牌）'
elif hm < 9 * 60 + 30:
    status = '未开盘'
elif hm <= 11 * 60 + 30:
    status = '盘中'
elif hm < 13 * 60:
    status = '午间休市'
elif hm <= 15 * 60:
    status = '盘中'
else:
    status = '已收盘'

result = {
    'code': CODE, 'name': name, 'session_date': day,
    'last_bar': last['datetime'], 'fetched_at': bj_now.strftime('%Y-%m-%d %H:%M:%S'),
    'is_realtime': live, 'session_status': status, 'bars': len(sess), 'period': PERIOD,
    'last': last['close'], 'prev_close': prev_close,
    'chg_pct': pct(last['close'], prev_close), 'open': sess[0]['open'],
    'open_gap_pct': pct(sess[0]['open'], prev_close),
    'high': high, 'high_time': high_t, 'low': low, 'low_time': low_t,
    'amplitude_pct': round((high - low) / prev_close * 100, 2) if prev_close else None,
    'day_position_pct': round((last['close'] - low) / (high - low) * 100, 1) if high > low else None,
    'vwap': round(vwap, 2) if vwap else None,
    'last_vs_vwap_pct': pct(last['close'], vwap),
    'cum_volume_lots': int(cum_lots),
    'amount_est_yi': round(amount / 1e8, 2),
    'turnover_pct': round(sum(b['turnover_rate_pct'] or 0 for b in sess), 2),
    'volume_ratio': vol_ratio,
    'last30m_chg_pct': tail_chg, 'last30m_vol_share_pct': tail_share,
}
print(json.dumps(result, ensure_ascii=False, indent=2))

# 存一份给「分时图」段复用（run_python 每次是新进程，变量不跨调用）
Path('intraday_last.json').write_text(json.dumps({
    'code': CODE, 'name': name, 'day': day, 'prev_close': prev_close,
    'vwap': result['vwap'], 'bars': sess,
}, ensure_ascii=False), encoding='utf-8')

print('\n最近 10 根：')
for b in sess[-10:]:
    print(f"{b['datetime'][11:]}  close={b['close']:.2f}  vol={b['volume']:.0f}手")
```

需要分时图时，先 `pip_install(['matplotlib'])`（会话内一次），再在**新的 run_python 调用**里执行
（变量不跨调用，上一段已把数据存到 `intraday_last.json`）：

```python
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

d = json.loads(Path('intraday_last.json').read_text(encoding='utf-8'))
CODE, name, day, prev_close = d['code'], d['name'], d['day'], d['prev_close']
sess, vwap = d['bars'], d['vwap']
prices = [b['close'] for b in sess]
last = sess[-1]

plt.rcParams['font.sans-serif'] = ['Noto Sans CJK SC', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

xs = list(range(len(sess)))
fig, (ax, axv) = plt.subplots(2, 1, figsize=(11, 5.6), sharex=True,
                              gridspec_kw={'height_ratios': [3, 1], 'hspace': 0.06})
up = prev_close is not None and last['close'] >= prev_close
ax.plot(xs, prices, lw=1.1, color='#d64545' if up else '#1f9d55', label='价格')
if vwap:
    ax.axhline(vwap, lw=1.0, ls='--', color='#e0a021', label=f'均价 {vwap:.2f}')
if prev_close:
    ax.axhline(prev_close, lw=0.8, ls=':', color='#999999', label=f'昨收 {prev_close:.2f}')
ax.set_title(f"{CODE} {name or ''}  {day} 分时（{last['datetime'][11:]}）", fontsize=11)
ax.legend(loc='best', fontsize=8, frameon=False)
ax.grid(alpha=.25, lw=.4)

colors = ['#d64545' if (i > 0 and sess[i]['close'] >= sess[i - 1]['close'])
          else '#1f9d55' for i in xs]
axv.bar(xs, [b['volume'] for b in sess], color=colors, width=.8)
axv.set_ylabel('量(手)', fontsize=8)
axv.grid(alpha=.25, lw=.4)

ticks = [i for i, b in enumerate(sess) if b['datetime'][11:] in
         ('09:30', '10:30', '11:30', '14:00', '15:00')]
axv.set_xticks(ticks)
axv.set_xticklabels([sess[i]['datetime'][11:] for i in ticks], fontsize=8)
out = f"intraday_{CODE}_{day.replace('-', '')}.png"
fig.savefig(out, dpi=140, bbox_inches='tight')
print('CHART:', Path(out).resolve())
```

## 判读要点（只讲数据支持的结论）
- **涨跌**：`chg_pct` 对昨收；`open_gap_pct` 判断高开/低开；`day_position_pct` 0=日内最低、100=最高。
- **强弱**：现价在 `vwap`（分时均价）上方 = 当日买方占优，下方 = 卖方占优——这是粗判，不是买卖建议。
- **量能**：`volume_ratio` > 1.2 放量、< 0.8 缩量（同期口径，已按时间进度折算）；早盘 15 分钟内为 null 是正常的。
- **异动**：`last30m_chg_pct` 与 `last30m_vol_share_pct` 一起看——涨幅 > 0.5% 且量占比明显高于 30/240≈12.5% → 尾盘/近期放量拉升；反之跳水。
- **口径提醒**：分钟线不复权；成交额是典型价估算；`turnover_pct` 是分钟换手率求和（腾讯口径）。
- **不要**用分时数据推断主力资金、大单净流入——接口没有买卖方向；这类问题查 `stock_moneyflow`。

## 输出格式
1. **一句话**：`贵州茅台 1437.88（-0.49%），分时均价 1438.57，量比 0.45`——先给现价与时间戳。
2. **关键数据表**：现价/昨收/涨跌幅、开盘缺口、日内高/低（时间）、振幅、均价、量比、成交额（估算）、换手。
3. **盘中结构**：高开低走/低开高走、均价线上下、日内高低点时段、最近 30 分钟量价。
4. **图**（用户要「看一下」时附上）。
5. **口径一句**：数据来自腾讯分钟线（不复权），`last_bar` 时间与 `session_status`（盘中/午休/已收盘/非实时）。

## 限制与失败处理
- `minute_kline` 抛 `ValueError`：代码格式/period/count 不合法，或北交所；不要重试同一参数。
- 抛 `RuntimeError`（三入口均不可用/限流）：等 1-2 分钟再试一次；仍失败就退回 `daily_quote` 最新日线并说明「实时源不可用」。
- 返回的最后一根 bar 不是今天：停牌、未开盘或非交易日——先说明，再按「最近交易日」口径展示，不要伪装成实时。
- `prev_close is None`（库里缺昨收）：只报绝对价与日内结构，涨跌幅/振幅写「缺昨收，无法计算」。
- 用户要的历史日期超出 320 根覆盖：明确说项目只保留最近 320 根分钟线，改看日线。
