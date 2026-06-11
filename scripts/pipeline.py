#!/usr/bin/env python3
"""
K线更新后的流水线 —— 订阅者模式。
每次日K增量更新完成后自动触发，也可手动运行:
  python3 scripts/pipeline.py 2026-06-08
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from typing import Callable, Dict, List

# ── 事件注册表 ──
_subscribers: Dict[str, List[Callable]] = {}


def subscribe(event: str, fn: Callable):
    """注册事件订阅者。"""
    _subscribers.setdefault(event, []).append(fn)


def emit(event: str, *args, **kwargs):
    """触发事件，依次执行所有订阅者。"""
    results = []
    for fn in _subscribers.get(event, []):
        try:
            logger.info(f"[pipeline] 执行 {fn.__name__}...")
            t0 = time.time()
            result = fn(*args, **kwargs)
            elapsed = time.time() - t0
            logger.info(f"[pipeline] {fn.__name__} 完成 ({elapsed:.1f}s)")
            results.append((fn.__name__, True, result))
        except Exception as e:
            logger.error(f"[pipeline] {fn.__name__} 失败: {e}")
            results.append((fn.__name__, False, str(e)))
    return results


# ══════════════════════════════════════════
# 订阅者：生成树图数据
# ══════════════════════════════════════════

def generate_treemap(trade_date: str, metric: str = 'mcap'):
    """为指定日期生成树图数据（mcap/volume/amount），写入 stock_treemap_cache。"""
    from app.db.connection import get_sync_db, is_sqlite
    from sqlalchemy import text
    import json

    db = get_sync_db()
    try:
        # 1. 获取所有活跃A股的最新行情 + 行业 + 市值/量/额（排除指数和ETF）
        query_col = {"mcap": "d.close, f.industry, f.market_cap, f.total_shares",
                     "volume": "d.close, d.volume, f.industry",
                     "amount": "d.close, d.amount, f.industry",
                     "pe": "d.close, f.industry, NULL, NULL"}.get(metric, "d.close, f.industry, f.market_cap, f.total_shares")
        rows = db.execute(text(f"""
            SELECT d.stock_code, d.stock_name, {query_col},
                   d.trade_date
            FROM daily_quote d
            JOIN stock_master sm ON sm.stock_code = d.stock_code
            LEFT JOIN stock_fundamentals f ON f.stock_code = d.stock_code
            WHERE d.trade_date = :d AND d.exchange != 'BSE'
              AND sm.stock_type = 'stock'
        """), {"d": trade_date}).fetchall()

        if not rows:
            logger.warning(f"  {trade_date} 无行情数据，跳过")
            return 0

        logger.info(f"  {trade_date}: {len(rows)} 只股票")

        # 2. 获取 20 天前的收盘价用于趋势计算
        from datetime import datetime, timedelta
        d20 = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=20)).strftime("%Y-%m-%d")
        codes = [r[0] for r in rows]
        trends = {}
        for code in codes:
            rows20 = db.execute(text("""
                SELECT close FROM daily_quote WHERE stock_code=:c AND trade_date<=:d
                ORDER BY trade_date DESC LIMIT 20
            """), {"c": code, "d": trade_date}).fetchall()
            if len(rows20) >= 20:
                ma20 = sum(float(x[0]) for x in rows20) / len(rows20)
                cur = float(rows20[0][0]) if rows20[0][0] else 0
                trends[code] = cur >= ma20
            else:
                trends[code] = True

        # 3. 按行业层级组织
        # L1: 一级行业 A,B,C ...
        # L2: 二级行业 C15,C26 ...
        l1_map = {}
        for r in rows:
            code, name = r[0], r[1]
            price = float(r[2]) if r[2] else 0
            if metric == 'mcap':
                cols = [x[0] for x in db.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='stock_fundamentals' AND column_name IN ('market_cap','total_shares')")).fetchall()]
                mcap_raw = float(r[4]) if len(r) > 4 and r[4] else None
                shares = float(r[5]) if len(r) > 5 and r[5] else None
                if mcap_raw: val = mcap_raw
                elif shares: val = shares * price
                else: val = price * 100000000
                industry = str(r[3]) if len(r) > 3 and r[3] else ''
            elif metric == 'volume':
                val = float(r[3]) if len(r) > 3 and r[3] else 0
                industry = str(r[4]) if len(r) > 4 and r[4] else ''
            elif metric == 'pe':
                val = 50  # 默认中间值
                industry = str(r[3]) if len(r) > 3 and r[3] else ''
                # 从 fundamentals_history 获取近1年 PE 计算百分位
                try:
                    pe_rows = db.execute(text("""
                        SELECT pe_ttm FROM stock_fundamentals_history
                        WHERE stock_code=:c AND report_date >= :start
                        ORDER BY report_date ASC
                    """), {"c": code, "start": f"{int(trade_date[:4])-1}-{trade_date[5:]}"}).fetchall()
                    pes = [float(rr[0]) for rr in pe_rows if rr[0]]
                    if len(pes) >= 4:
                        import numpy as np
                        arr = np.array(pes)
                        latest = pes[-1]
                        pct = np.sum(arr <= latest) / len(arr) * 100
                        val = 100 - pct  # 百分位越低，面积越大
                except:
                    pass
            else:  # amount
                val = float(r[3]) if len(r) > 3 and r[3] else 0
                industry = str(r[4]) if len(r) > 4 and r[4] else ''

            chg = 0  # 涨跌幅从 prev_close 计算
            # prev_close
            prev = db.execute(text("""
                SELECT close FROM daily_quote WHERE stock_code=:c AND trade_date<:d
                ORDER BY trade_date DESC LIMIT 1
            """), {"c": code, "d": trade_date}).fetchone()
            if prev and prev[0]:
                chg = (price - float(prev[0])) / float(prev[0]) * 100

            industry = industry or "U00其他"
            l1 = industry[0]  # A,B,C...
            l2 = industry[:3] if len(industry) >= 3 else l1  # A01,C15...
            # L2 display name
            l2_name = industry.split(" ", 1)[-1] if " " in industry else industry

            if l1 not in l1_map:
                l1_map[l1] = {"stocks": [], "l2s": {}, "total_mcap": 0}
            if l2 not in l1_map[l1]["l2s"]:
                l1_map[l1]["l2s"][l2] = {"name": l2_name, "stocks": [], "total_mcap": 0}

            stock_item = {
                "code": code, "name": name, "price": round(price, 2),
                "chg": round(chg, 2), "val": round(val, 2),
                "trend_up": trends.get(code, True)
            }
            l1_map[l1]["l2s"][l2]["stocks"].append(stock_item)
            l1_map[l1]["l2s"][l2]["total_mcap"] += val
            l1_map[l1]["total_mcap"] += val

        # 4. 写入 cache 表
        upsert = """
            INSERT INTO stock_treemap_cache
            (trade_date, metric, parent, node_id, name, value, chg_pct, trend_up, node_type, detail)
            VALUES (:d, :m, :p, :id, :n, :v, :chg, :up, :t, :dt)
            ON CONFLICT (trade_date, metric, node_id) DO UPDATE SET
            name=EXCLUDED.name, value=EXCLUDED.value, chg_pct=EXCLUDED.chg_pct,
            trend_up=EXCLUDED.trend_up, detail=EXCLUDED.detail
        """
        # 清空旧数据
        db.execute(text("DELETE FROM stock_treemap_cache WHERE trade_date=:d AND metric=:m"), {"d": trade_date, "m": metric})
        db.commit()

        total = 0

        for l1_code in sorted(l1_map.keys()):
            l1_data = l1_map[l1_code]
            avg_chg = sum(s["chg"] for l2d in l1_data["l2s"].values() for s in l2d["stocks"])
            cnt = sum(len(l2d["stocks"]) for l2d in l1_data["l2s"].values())
            avg_chg = round(avg_chg / cnt, 2) if cnt else 0

            # L1 中文名
            l1_names = {'A':'农林牧渔','B':'采矿业','C':'制造业','D':'电力热力','E':'建筑业',
                        'F':'批发零售','G':'交通运输','H':'住宿餐饮','I':'信息技术',
                        'J':'金融业','K':'房地产业','L':'租赁商务','M':'科研服务',
                        'N':'环保水利','O':'居民服务','P':'教育','Q':'卫生','R':'文体娱乐',
                        'S':'综合','U':'其他'}
            l1_display = l1_names.get(l1_code, l1_code)
            db.execute(text(upsert), {
                "d": trade_date, "m": metric, "p": "root", "id": l1_code,
                "n": l1_display, "v": round(l1_data["total_mcap"], 2),
                "chg": avg_chg, "up": True, "t": "l1",
                "dt": json.dumps({"count": cnt})
            })
            total += 1

            for l2_code, l2_data in l1_data["l2s"].items():
                l2_avg = round(sum(s["chg"] for s in l2_data["stocks"]) / len(l2_data["stocks"]), 2) if l2_data["stocks"] else 0

                db.execute(text(upsert), {
                    "d": trade_date, "m": metric, "p": l1_code, "id": l2_code,
                    "n": l2_data["name"], "v": round(l2_data["total_mcap"], 2),
                    "chg": l2_avg, "up": True, "t": "l2",
                    "dt": json.dumps({"count": len(l2_data["stocks"])})
                })
                total += 1

                for stock in l2_data["stocks"]:
                    db.execute(text(upsert), {
                        "d": trade_date, "m": metric, "p": l2_code, "id": stock["code"],
                        "n": stock["name"], "v": stock["val"],
                        "chg": stock["chg"], "up": stock["trend_up"],
                        "t": "stock",
                        "dt": json.dumps(stock)
                    })
                    total += 1

        db.commit()
        logger.info(f"  树图数据写入: {total} 条")
        return total
    finally:
        db.close()


# ── 策略计算订阅者 ──

def run_strategies(trade_date: str):
    """全市场策略扫描 + 信号保存。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    from strategy.engine import StrategyEngine
    import json as _json

    logger.info(f"[pipeline] 策略计算 {trade_date}...")

    # 加载策略配置
    db = get_sync_db()
    try:
        configs = db.execute(text(
            "SELECT strategy_name, params FROM strategy_config WHERE enabled=true"
        )).fetchall()
        strategy_params = {}
        for r in configs:
            try: strategy_params[r[0]] = _json.loads(r[1])
            except: strategy_params[r[0]] = {}
    finally:
        db.close()

    from crawler.data_loader import StrategyDataLoader
    loader = StrategyDataLoader()
    all_data = loader.load_all_stocks_data()
    loader.close()

    engine = StrategyEngine()
    signals = engine.run_all(all_data, trade_date, strategy_params)

    db = get_sync_db()
    saved = 0
    for sig in signals:
        try:
            db.execute(text("""
                INSERT INTO signal_history (signal_date, stock_code, stock_name,
                    direction, strength, strategy_name, reason, price,
                    preference, suggested_action, combined_signal, source_strategies)
                VALUES (:d, :c, :n, :dir, :st, :sn, :r, :p, :pref, :sa, :cs, :ss)
            """), {
                "d": trade_date, "c": sig.stock_code, "n": sig.stock_name,
                "dir": sig.direction, "st": sig.strength, "sn": sig.strategy_name,
                "r": sig.reason, "p": sig.price, "pref": sig.preference,
                "sa": sig.suggested_action, "cs": sig.combined_signal,
                "ss": _json.dumps(sig.source_strategies) if sig.source_strategies else None
            })
            saved += 1
        except:
            pass
    db.commit()
    db.close()
    logger.info(f"[pipeline] 策略完成: {saved} 信号")


# ── 注册 ──
for m in ['mcap', 'volume', 'amount', 'pe']:
    subscribe("after_kline", lambda date, mm=m: generate_treemap(date, mm))
subscribe("after_kline", run_strategies)


# ── CLI 入口 ──
if __name__ == '__main__':
    date_str = sys.argv[1] if len(sys.argv) > 1 else ""
    if not date_str:
        from datetime import date
        date_str = date.today().isoformat()
    logger.info(f"===== 流水线触发: {date_str} =====")
    for m in ['mcap', 'volume', 'amount', 'pe']:
        generate_treemap(date_str, m)
