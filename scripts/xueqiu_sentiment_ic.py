"""雪球情绪信号初检（design/06 §9.10 的 IC 体检前置版）。

回答三个问题：
 1. 情绪是「领先」还是「跟随」价格？（情绪 vs 同日收益 vs 次日收益）
 2. 净看多能否预测次日收益？（分日 RankIC + 分组收益）
 3. 「注意力」（帖子数）是否比「方向」（净看多）更可靠？

口径：只统计已打分帖；按发帖日聚合（北京时间），前瞻收益用 daily_quote 的下一交易日。
"""
import sys
sys.path.insert(0, '/home/bnbnyu/projects/stone')
import numpy as np
from app.db.connection import get_sync_db
from sqlalchemy import text

db = get_sync_db()
db.execute(text("SET statement_timeout = 60000"))

# 交易日轴（有行情的日子）
tds = [str(r[0]) for r in db.execute(text(
    "SELECT DISTINCT trade_date FROM daily_quote WHERE trade_date >= '2026-09-18' "
    "ORDER BY trade_date")).fetchall()]
print(f"交易日: {tds}")

# 情绪聚合（贴数 >= 阈值）
rows = db.execute(text("""
    SELECT (s.created_at AT TIME ZONE 'Asia/Shanghai')::date AS d, s.code,
           count(*) n,
           SUM(CASE WHEN se.sentiment = 1  THEN 1 ELSE 0 END) bull,
           SUM(CASE WHEN se.sentiment = -1 THEN 1 ELSE 0 END) bear,
           COALESCE(SUM(s.like_count + s.reply_count * 3), 0) heat
    FROM xueqiu_sentiment se JOIN xueqiu_status s USING (status_id)
    WHERE (s.created_at AT TIME ZONE 'Asia/Shanghai')::date >= '2026-09-21'
    GROUP BY 1, 2
""")).fetchall()

# 行情：每只股票的日收益（用后复权算，除权不影响横截面比较）
px = {}
for r in db.execute(text("""
    SELECT stock_code, trade_date, close_hfq FROM daily_quote
    WHERE trade_date >= '2026-09-18' AND close_hfq > 0
""")).fetchall():
    px.setdefault(str(r[1]), {})[r[0]] = float(r[2])


def fwd_ret(code, d):
    """d 日收盘 → 下一交易日收盘 的收益；以及 d 日当日收益（用于同期检验）。"""
    if d not in tds:
        return None, None
    i = tds.index(d)
    cur = px.get(d, {}).get(code)
    same = None
    if i > 0:
        prev = px.get(tds[i - 1], {}).get(code)
        if prev and cur:
            same = cur / prev - 1
    nxt = None
    if i + 1 < len(tds):
        n2 = px.get(tds[i + 1], {}).get(code)
        if cur and n2:
            nxt = n2 / cur - 1
    return same, nxt


def spearman(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 5:
        return np.nan
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    ra -= ra.mean(); rb -= rb.mean()
    den = np.sqrt((ra ** 2).sum() * (rb ** 2).sum())
    return float((ra * rb).sum() / den) if den else np.nan


for MINN in (3,):
    print(f"\n{'='*72}\n样本门槛: 每股每日 >= {MINN} 帖\n{'='*72}")
    per_day = {}
    for d, code, n, bull, bear, heat in rows:
        d = str(d)
        if n < MINN:
            continue
        same, nxt = fwd_ret(code, d)
        per_day.setdefault(d, []).append({
            'code': code, 'n': n, 'net': (bull - bear) / n,
            'bull_r': bull / n, 'heat': float(heat), 'same': same, 'nxt': nxt})

    print(f"\n{'日期':<12}{'股票数':>7}{'同期IC(净分vs当日)':>20}{'前瞻IC(净分vs次日)':>21}{'前瞻IC(帖数vs次日)':>21}")
    ics_same, ics_nxt, ics_cnt = [], [], []
    for d in sorted(per_day):
        g = [x for x in per_day[d] if x['nxt'] is not None]
        if len(g) < 20:
            print(f"{d:<12}{len(g):>7}  (样本不足)")
            continue
        net = [x['net'] for x in g]; cnt = [x['n'] for x in g]
        same = [x['same'] for x in g]; nxt = [x['nxt'] for x in g]
        ic_s = spearman(net, [0 if s is None else s for s in same])
        ic_n = spearman(net, nxt)
        ic_c = spearman(cnt, nxt)
        ics_same.append(ic_s); ics_nxt.append(ic_n); ics_cnt.append(ic_c)
        print(f"{d:<12}{len(g):>7}{ic_s:>20.3f}{ic_n:>21.3f}{ic_c:>21.3f}")
    if ics_nxt:
        print(f"\n{'均值':<12}{'':>7}{np.nanmean(ics_same):>20.3f}"
              f"{np.nanmean(ics_nxt):>21.3f}{np.nanmean(ics_cnt):>21.3f}")
        print(f"  前瞻 IC 同号天数: {sum(1 for x in ics_nxt if x > 0)}/{len(ics_nxt)} 为正")

    # 分组：净看多五档 → 次日收益（跨日合并）
    print(f"\n--- 净看多分组 → 次日收益（跨日合并）---")
    allobs = [x for d in per_day for x in per_day[d] if x['nxt'] is not None]
    if allobs:
        nets = np.array([x['net'] for x in allobs])
        rets = np.array([x['nxt'] for x in allobs])
        mkt = rets.mean()
        print(f"  全样本均值(市场基准) = {mkt*100:+.2f}%   n={len(allobs)}  涉及 {len(set(x['code'] for x in allobs))} 只股")
        qs = np.quantile(nets, [0, .2, .4, .6, .8, 1.0])
        for i in range(5):
            m = (nets >= qs[i]) & (nets <= qs[i+1] if i == 4 else nets < qs[i+1])
            if m.sum():
                print(f"  Q{i+1} 净分[{qs[i]:+.2f},{qs[i+1]:+.2f}]  n={m.sum():>4}  "
                      f"次日={rets[m].mean()*100:+.2f}%  超额={rets[m].mean()*100-mkt*100:+.2f}pp")
        # 极端组：净看多最高/最低 10%
        hi = nets >= np.quantile(nets, 0.9); lo = nets <= np.quantile(nets, 0.1)
        print(f"  极端看多(top10%) n={hi.sum():>4} 次日={rets[hi].mean()*100:+.2f}%  超额={rets[hi].mean()*100-mkt*100:+.2f}pp")
        print(f"  极端看空(bot10%) n={lo.sum():>4} 次日={rets[lo].mean()*100:+.2f}%  超额={rets[lo].mean()*100-mkt*100:+.2f}pp")

    # 注意力：帖数分组
    print(f"\n--- 帖数（注意力）分组 → 次日收益 ---")
    if allobs:
        cnts = np.array([x['n'] for x in allobs], float)
        rets = np.array([x['nxt'] for x in allobs]); mkt = rets.mean()
        for lo_, hi_ in [(3, 4), (5, 9), (10, 19), (20, 10**9)]:
            m = (cnts >= lo_) & (cnts <= hi_)
            if m.sum() >= 10:
                print(f"  {lo_}-{hi_ if hi_<10**9 else '+'} 帖  n={m.sum():>4}  "
                      f"次日={rets[m].mean()*100:+.2f}%  超额={rets[m].mean()*100-mkt*100:+.2f}pp")

    # 热度（互动）分组
    print(f"\n--- 热度（like+reply*3 聚合）分组 → 次日收益 ---")
    if allobs:
        hs = np.array([x['heat'] for x in allobs], float)
        rets = np.array([x['nxt'] for x in allobs]); mkt = rets.mean()
        qs = np.quantile(hs, [.2, .4, .6, .8])
        for lbl, m in [('Q1 最低', hs <= qs[0]), ('Q2', (hs > qs[0]) & (hs <= qs[1])),
                       ('Q3', (hs > qs[1]) & (hs <= qs[2])), ('Q4', (hs > qs[2]) & (hs <= qs[3])),
                       ('Q5 最高', hs > qs[3])]:
            if m.sum():
                print(f"  {lbl:<8} n={m.sum():>4}  次日={rets[m].mean()*100:+.2f}%  "
                      f"超额={rets[m].mean()*100-mkt*100:+.2f}pp")
db.close()
