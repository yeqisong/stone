#!/usr/bin/env python3
"""
DAG 流水线 —— 有向无环图任务调度。
每个节点声明依赖，调度器按拓扑排序执行 + 手工触发自动传播下游。

手动运行:
  python3 scripts/pipeline.py 2026-06-08        全量执行
  python3 scripts/pipeline.py 2026-06-08 kline  只触发 kline 链路
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from scripts.dag import DagNode, DagExecutor


# ══════════════════════════════════════════
# 任务函数（原有 generate_treemap / run_strategies / generate_stats 保持不变）
# ══════════════════════════════════════════

def generate_treemap(trade_date: str, metric: str = 'mcap'):
    """为指定日期生成树图数据（mcap/volume/amount/pe），写入 stock_treemap_cache。"""
    from app.db.connection import get_sync_db, is_sqlite
    from sqlalchemy import text
    import json

    db = get_sync_db()
    try:
        query_col = {"mcap": "d.close, f.industry, f.market_cap, f.total_shares",
                     "volume": "d.close, d.volume, f.industry",
                     "amount": "d.close, d.amount, f.industry",
                     "pe": "d.close, f.industry, NULL, NULL"}.get(metric, "d.close, f.industry, f.market_cap, f.total_shares")
        rows = db.execute(text(f"""
            SELECT d.stock_code, d.stock_name, {query_col}, d.trade_date
            FROM daily_quote d
            JOIN stock_master sm ON sm.stock_code = d.stock_code
            LEFT JOIN stock_fundamentals f ON f.stock_code = d.stock_code
            WHERE d.trade_date = :d AND d.exchange != 'BSE' AND sm.stock_type = 'stock'
        """), {"d": trade_date}).fetchall()
        if not rows: return logger.warning(f"  {trade_date} 无行情数据，跳过") or 0
        logger.info(f"  {trade_date}: {len(rows)} 只股票")

        from datetime import datetime, timedelta
        d20 = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=20)).strftime("%Y-%m-%d")
        trends = {}
        for code in {r[0] for r in rows}:
            rows20 = db.execute(text("SELECT close FROM daily_quote WHERE stock_code=:c AND trade_date<=:d ORDER BY trade_date DESC LIMIT 20"), {"c": code, "d": trade_date}).fetchall()
            trends[code] = (len(rows20) >= 20 and float(rows20[0][0]) >= sum(float(x[0]) for x in rows20) / len(rows20)) if rows20 else True

        l1_map, l1_names = {}, {'A':'农林牧渔','B':'采矿业','C':'制造业','D':'电力热力','E':'建筑业','F':'批发零售','G':'交通运输','H':'住宿餐饮','I':'信息技术','J':'金融业','K':'房地产业','L':'租赁商务','M':'科研服务','N':'环保水利','O':'居民服务','P':'教育','Q':'卫生','R':'文体娱乐','S':'综合','U':'其他'}
        for r in rows:
            code, name, price_str = r[0], r[1], float(r[2]) if r[2] else 0
            price = price_str
            try:
                if metric == 'mcap':
                    mcap_raw = float(r[4]) if len(r) > 4 and r[4] else None
                    shares = float(r[5]) if len(r) > 5 and r[5] else None
                    val = mcap_raw or (shares * price if shares else price * 100000000)
                    industry = str(r[3]) if len(r) > 3 and r[3] else ''
                elif metric == 'volume':
                    val = float(r[3]) if len(r) > 3 and r[3] else 0
                    industry = str(r[4]) if len(r) > 4 and r[4] else ''
                elif metric == 'pe':
                    val = 50; industry = str(r[3]) if len(r) > 3 and r[3] else ''
                    try:
                        pe_rows = db.execute(text("SELECT pe_ttm FROM stock_fundamentals_history WHERE stock_code=:c AND report_date>=:start ORDER BY report_date ASC"), {"c": code, "start": f"{int(trade_date[:4])-1}-{trade_date[5:]}"}).fetchall()
                        pes = [float(rr[0]) for rr in pe_rows if rr[0]]
                        if len(pes) >= 4:
                            import numpy as np
                            pct = np.sum(np.array(pes) <= pes[-1]) / len(pes) * 100
                            val = 100 - pct
                    except: pass
                else:  # amount
                    val = float(r[3]) if len(r) > 3 and r[3] else 0
                    industry = str(r[4]) if len(r) > 4 and r[4] else ''
            except:
                val = 0; industry = ''

            chg = 0
            prev = db.execute(text("SELECT close FROM daily_quote WHERE stock_code=:c AND trade_date<:d ORDER BY trade_date DESC LIMIT 1"), {"c": code, "d": trade_date}).fetchone()
            if prev and prev[0]: chg = (price - float(prev[0])) / float(prev[0]) * 100

            industry = industry or "U00其他"
            l1 = industry[0]
            l2 = industry[:3] if len(industry) >= 3 else l1
            l2_name = industry.split(" ", 1)[-1] if " " in industry else industry

            l1_map.setdefault(l1, {"l2s": {}, "total_mcap": 0})
            l1_map[l1].setdefault("l2s", {}).setdefault(l2, {"name": l2_name, "stocks": [], "total_mcap": 0})
            l1_map[l1]["l2s"][l2]["stocks"].append({"code": code, "name": name, "price": round(price, 2), "chg": round(chg, 2), "val": round(val, 2), "trend_up": trends.get(code, True)})
            l1_map[l1]["l2s"][l2]["total_mcap"] += val
            l1_map[l1]["total_mcap"] += val

        upsert = "INSERT INTO stock_treemap_cache (trade_date, metric, parent, node_id, name, value, chg_pct, trend_up, node_type, detail) VALUES (:d, :m, :p, :id, :n, :v, :chg, :up, :t, :dt) ON CONFLICT (trade_date, metric, node_id) DO UPDATE SET name=EXCLUDED.name, value=EXCLUDED.value, chg_pct=EXCLUDED.chg_pct, trend_up=EXCLUDED.trend_up, detail=EXCLUDED.detail"
        db.execute(text("DELETE FROM stock_treemap_cache WHERE trade_date=:d AND metric=:m"), {"d": trade_date, "m": metric}); db.commit()

        total = 0
        for l1_code in sorted(l1_map.keys()):
            l1_data = l1_map[l1_code]
            stocks_flat = [s for l2d in l1_data["l2s"].values() for s in l2d["stocks"]]
            avg_chg = round(sum(s["chg"] for s in stocks_flat) / len(stocks_flat), 2) if stocks_flat else 0
            db.execute(text(upsert), {"d": trade_date, "m": metric, "p": "root", "id": l1_code, "n": l1_names.get(l1_code, l1_code), "v": round(l1_data["total_mcap"], 2), "chg": avg_chg, "up": True, "t": "l1", "dt": json.dumps({"count": len(stocks_flat)})}); total += 1
            for l2_code, l2_data in l1_data["l2s"].items():
                l2_avg = round(sum(s["chg"] for s in l2_data["stocks"]) / len(l2_data["stocks"]), 2) if l2_data["stocks"] else 0
                db.execute(text(upsert), {"d": trade_date, "m": metric, "p": l1_code, "id": l2_code, "n": l2_data["name"], "v": round(l2_data["total_mcap"], 2), "chg": l2_avg, "up": True, "t": "l2", "dt": json.dumps({"count": len(l2_data["stocks"])})}); total += 1
                for stock in l2_data["stocks"]:
                    db.execute(text(upsert), {"d": trade_date, "m": metric, "p": l2_code, "id": stock["code"], "n": stock["name"], "v": stock["val"], "chg": stock["chg"], "up": stock["trend_up"], "t": "stock", "dt": json.dumps(stock)}); total += 1
        db.commit()
        logger.info(f"  树图写入: {total} 条")
        return total
    finally:
        db.close()


def run_strategies(trade_date: str):
    """全市场策略扫描 + 信号保存。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    from strategy.engine import StrategyEngine
    import json as _json
    logger.info(f"[pipeline] 策略计算 {trade_date}...")
    db = get_sync_db()
    try:
        configs = db.execute(text("SELECT strategy_name, params FROM strategy_config WHERE enabled=true")).fetchall()
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
    db = get_sync_db(); saved = 0
    for sig in signals:
        try:
            db.execute(text("INSERT INTO signal_history (signal_date, stock_code, stock_name, direction, strength, strategy_name, reason, price, preference, suggested_action, combined_signal, source_strategies) VALUES (:d, :c, :n, :dir, :st, :sn, :r, :p, :pref, :sa, :cs, :ss)"), {"d": trade_date, "c": sig.stock_code, "n": sig.stock_name, "dir": sig.direction, "st": sig.strength, "sn": sig.strategy_name, "r": sig.reason, "p": sig.price, "pref": sig.preference, "sa": sig.suggested_action, "cs": sig.combined_signal, "ss": _json.dumps(sig.source_strategies) if sig.source_strategies else None})
            saved += 1
        except: pass
    db.commit(); db.close()
    logger.info(f"[pipeline] 策略完成: {saved} 信号")
    return saved


def generate_stats(*args, **kwargs):
    """全库数据统计并写入 data_stats_cache。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    import json as _json
    from datetime import datetime
    logger.info("[pipeline] 数据统计...")
    db = get_sync_db()
    def q(query):
        try: return db.execute(text(query)).scalar()
        except: db.rollback(); return -1
    tables = [
        ('上交所A股', lambda: (q("SELECT COUNT(*) FROM daily_quote WHERE exchange='SSE'"), q("SELECT COUNT(*) FROM stock_master WHERE exchange='SSE' AND status='N' AND stock_type='stock'")),
         ("SELECT MIN(trade_date)::text FROM daily_quote WHERE exchange='SSE'", "SELECT MAX(trade_date)::text FROM daily_quote WHERE exchange='SSE'")),
        ('深交所A股', lambda: (q("SELECT COUNT(*) FROM daily_quote WHERE exchange='SZSE'"), q("SELECT COUNT(*) FROM stock_master WHERE exchange='SZSE' AND status='N' AND stock_type='stock'")),
         ("SELECT MIN(trade_date)::text FROM daily_quote WHERE exchange='SZSE'", "SELECT MAX(trade_date)::text FROM daily_quote WHERE exchange='SZSE'")),
        ('指数日K线', lambda: (q("SELECT COALESCE((SELECT reltuples::bigint FROM pg_class WHERE relname='index_daily_quote'),0)"), q("SELECT COUNT(*) FROM stock_master WHERE stock_type='index'")),
         ("SELECT MIN(trade_date)::text FROM index_daily_quote", "SELECT MAX(trade_date)::text FROM index_daily_quote")),
        ('ETF日K线', lambda: (round(q("SELECT reltuples::bigint FROM pg_class WHERE relname='daily_quote'") * q("SELECT COUNT(*) FROM stock_master WHERE stock_type='etf'") / max(q("SELECT COUNT(*) FROM stock_master WHERE status='N' AND stock_type IN ('stock','etf')"), 1)), q("SELECT COUNT(*) FROM stock_master WHERE stock_type='etf'")),
         (None, None)),
        ('基本面', lambda: (q("SELECT COUNT(*) FROM stock_fundamentals"), q("SELECT COUNT(DISTINCT stock_code) FROM stock_fundamentals")),
         ("SELECT MIN(updated_at)::text FROM stock_fundamentals", "SELECT MAX(updated_at)::text FROM stock_fundamentals")),
        ('交易信号', lambda: (q("SELECT COUNT(*) FROM signal_history"), q("SELECT COUNT(DISTINCT stock_code) FROM signal_history")),
         ("SELECT MIN(signal_date)::text FROM signal_history", "SELECT MAX(signal_date)::text FROM signal_history")),
        ('交易日历', lambda: (q("SELECT COUNT(*) FROM trade_calendar"), None),
         ("SELECT MIN(cal_date)::text FROM trade_calendar", "SELECT MAX(cal_date)::text FROM trade_calendar")),
    ]
    stats = []
    for label, fn, date_q in tables:
        try:
            rows, items = fn()
            s = {'label': label, 'rows': rows or 0, 'items': items}
            # 补充起止日期
            if date_q:
                sr = db.execute(text(date_q[0])).scalar()
                er = db.execute(text(date_q[1])).scalar()
                if sr: s['start'] = str(sr)[:10]
                if er: s['end'] = str(er)[:10]
            stats.append(s)
        except:
            db.rollback()
            stats.append({'label': label, 'rows': -1, 'items': 0})
    sig_buy = q("SELECT COUNT(*) FROM signal_history WHERE direction='buy'") or 0
    sig_sell = q("SELECT COUNT(*) FROM signal_history WHERE direction='sell'") or 0
    for s in stats:
        if s['label'] == '交易信号': s['detail'] = f'买{sig_buy} 卖{sig_sell}'
    db.commit(); db.close()
    logger.info(f"[pipeline] 数据统计完成: {len(stats)} 项")
    return len(stats)


# ══════════════════════════════════════════
# 运行日志
# ══════════════════════════════════════════

def write_node_log(trade_date: str, node_name: str, status: str = 'ok', rows: int = 0, detail: str = '', run_id: str = ''):
    """写入/更新节点运行日志。running 时自动设置 heartbeat_at。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    try:
        db = get_sync_db()
        if status == 'pending':
            db.execute(text("""
                INSERT INTO dag_run_log (trade_date, node_name, run_id, status, rows, detail, created_at)
                VALUES (:d, :n, :rid, 'pending', 0, :dt, CURRENT_TIMESTAMP)
            """), {"d": trade_date, "n": node_name, "rid": run_id, "dt": detail or '待进行'})
        elif status == 'running':
            updated = db.execute(text("""
                UPDATE dag_run_log SET status='running', started_at=CURRENT_TIMESTAMP,
                    heartbeat_at=CURRENT_TIMESTAMP, detail=:dt
                WHERE trade_date=:d AND node_name=:n AND run_id=:rid AND status='pending'
            """), {"d": trade_date, "n": node_name, "rid": run_id, "dt": detail or '进行中'})
            if updated.rowcount == 0:
                db.execute(text("""
                    INSERT INTO dag_run_log (trade_date, node_name, run_id, status, rows, detail, created_at, started_at, heartbeat_at)
                    VALUES (:d, :n, :rid, 'running', :rr, :dt, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """), {"d": trade_date, "n": node_name, "rid": run_id, "rr": rows, "dt": detail or '进行中'})
        else:
            updated = db.execute(text("""
                UPDATE dag_run_log SET status=:s, rows=:rr, detail=:dt, finished_at=CURRENT_TIMESTAMP
                WHERE trade_date=:d AND node_name=:n AND run_id=:rid AND status='running'
            """), {"d": trade_date, "n": node_name, "rid": run_id, "s": status, "rr": rows, "dt": detail})
            if updated.rowcount == 0:
                updated = db.execute(text("""
                    UPDATE dag_run_log SET status=:s, rows=:rr, detail=:dt, finished_at=CURRENT_TIMESTAMP
                    WHERE trade_date=:d AND node_name=:n AND run_id=:rid AND status='pending'
                """), {"d": trade_date, "n": node_name, "rid": run_id, "s": status, "rr": rows, "dt": detail})
            if updated.rowcount == 0:
                db.execute(text("""
                    INSERT INTO dag_run_log (trade_date, node_name, run_id, status, rows, detail, created_at, started_at, heartbeat_at, finished_at)
                    VALUES (:d, :n, :rid, :s, :rr, :dt, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """), {"d": trade_date, "n": node_name, "rid": run_id, "s": status, "rr": rows, "dt": detail})
        db.commit()
        db.close()
    except Exception:
        pass


def update_node_progress(trade_date: str, node_name: str, rows: int = None, detail: str = None, run_id: str = ''):
    """更新运行中节点的心跳 + 可选进度数据。每~30秒调用一次。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    try:
        db = get_sync_db()
        parts = ["heartbeat_at=CURRENT_TIMESTAMP"]
        if rows is not None:
            parts.append(f"rows={rows}")
        if detail is not None:
            parts.append("detail=:dt")
        params = {"d": trade_date, "n": node_name, "rid": run_id}
        if detail is not None:
            params["dt"] = detail
        db.execute(text(f"UPDATE dag_run_log SET {','.join(parts)} WHERE trade_date=:d AND node_name=:n AND run_id=:rid AND status='running'"), params)
        db.commit()
        db.close()
    except Exception:
        pass


# ══════════════════════════════════════════
# DAG 结构定义（唯一来源，供 API 和流程图使用）
# ══════════════════════════════════════════

DAG_STRUCTURE = [
    {"name":"cron", "deps":[], "label":"⏰ Corn"},
    {"name":"daily_update", "deps":["cron"], "label":"更新汇总"},
    {"name":"kline", "deps":["daily_update"], "label":"A股日K线"},
    {"name":"index", "deps":["daily_update"], "label":"指数"},
    {"name":"etf", "deps":["daily_update"], "label":"ETF"},
    {"name":"fund", "deps":["daily_update"], "label":"基本面"},
    {"name":"treemap", "deps":["kline"], "label":"树图"},
    {"name":"strategy", "deps":["kline"], "label":"策略"},
    {"name":"stats", "deps":["treemap","strategy","index","etf","fund"], "label":"统计"},
    {"name":"daily_completeness", "deps":["stats"], "label":"日历统计"},
]

# ══════════════════════════════════════════
# DAG 定义 + DAG 包裹函数
# ══════════════════════════════════════════

def _rid(kw): return kw.get('run_id', '')

def dag_task_kline(trade_date=None, **kw):
    from datetime import date; td = trade_date or str(date.today()); rid = _rid(kw)
    write_node_log(td, 'kline', 'running', 0, '采集中', rid)
    try:
        from crawler.baostock_crawler import BaostockCrawler
        c = BaostockCrawler()
        update_node_progress(td, 'kline', 0, '登录baostock中…', rid)
        r = c.download_daily_update(date.fromisoformat(td))
        c.logout()
        rows = r.get('rows', 0)
        update_node_progress(td, 'kline', rows, f'完成，{rows}条', rid)
        write_node_log(td, 'kline', 'ok', rows, r.get('detail', ''), rid)
        return r
    except Exception as e:
        try: c.logout()
        except: pass
        write_node_log(td, 'kline', 'error', 0, str(e), rid)
        raise

def dag_task_index(trade_date=None, **kw):
    from datetime import date; td = trade_date or str(date.today()); rid = _rid(kw)
    write_node_log(td, 'index', 'running', 0, '采集中', rid)
    try:
        from crawler.baostock_crawler import BaostockCrawler
        c = BaostockCrawler()
        update_node_progress(td, 'index', 0, '登录baostock…', rid)
        r = c.download_all_index_daily(td); c.logout()
        update_node_progress(td, 'index', r, f'完成，{r}条', rid)
        write_node_log(td, 'index', 'ok', r, '', rid)
        return r
    except Exception as e:
        write_node_log(td, 'index', 'error', 0, str(e), rid)
        raise

def dag_task_etf(trade_date=None, **kw):
    from datetime import date; td = trade_date or str(date.today()); rid = _rid(kw)
    write_node_log(td, 'etf', 'running', 0, '采集中', rid)
    try:
        from crawler.baostock_crawler import BaostockCrawler
        c = BaostockCrawler()
        update_node_progress(td, 'etf', 0, '登录baostock…', rid)
        r = c.download_etf_daily(td); c.logout()
        rows = r.get('rows', 0)
        update_node_progress(td, 'etf', rows, f'完成，{rows}条', rid)
        write_node_log(td, 'etf', 'ok', rows, '', rid)
        return r
    except Exception as e:
        write_node_log(td, 'etf', 'error', 0, str(e), rid)
        raise

def dag_task_fund(trade_date=None, **kw):
    td = str(kw.get('trade_date', '')) or (trade_date or ''); rid = _rid(kw)
    if not td: from datetime import date as _dd; td = str(_dd.today())
    write_node_log(td, 'fund', 'running', 0, '采集中', rid)
    try:
        from crawler.baostock_crawler import BaostockCrawler
        c = BaostockCrawler()
        update_node_progress(td, 'fund', 0, '登录baostock…', rid)
        r = c.download_fundamentals(skip_existing=False, skip_pe_pb=False); c.logout()
        update_node_progress(td, 'fund', r or 0, f'完成，{r}只', rid)
        write_node_log(td, 'fund', 'ok', r or 0, '', rid)
        return r
    except Exception as e:
        write_node_log(td, 'fund', 'error', 0, str(e), rid)
        raise

def dag_task_treemap(trade_date=None, **kw):
    from datetime import date; td = trade_date or str(date.today()); rid = _rid(kw)
    write_node_log(td, 'treemap', 'running', 0, '生成中', rid)
    try:
        for i, m in enumerate(['mcap', 'volume', 'amount', 'pe']):
            update_node_progress(td, 'treemap', i, f'生成{i+1}/4: {m}', rid)
            generate_treemap(td, m)
        update_node_progress(td, 'treemap', 4, '完成', rid)
        write_node_log(td, 'treemap', 'ok', 4, '', rid)
        return 4
    except Exception as e:
        write_node_log(td, 'treemap', 'error', 0, str(e), rid)
        raise

def dag_task_strategy(trade_date=None, **kw):
    from datetime import date; td = trade_date or str(date.today()); rid = _rid(kw)
    write_node_log(td, 'strategy', 'running', 0, '计算中', rid)
    # 后台线程定期发心跳，避免耗时计算被看门狗误杀
    import threading as _t, time as _tt
    _stop_hb = False
    def _heartbeat():
        while not _stop_hb:
            update_node_progress(td, 'strategy', None, '策略计算中…', rid)
            _tt.sleep(25)
    hb = _t.Thread(target=_heartbeat, daemon=True)
    hb.start()
    try:
        n = run_strategies(td)
        _stop_hb = True
        update_node_progress(td, 'strategy', n or 0, f'完成，{n}信号', rid)
        write_node_log(td, 'strategy', 'ok', n or 0, '', rid)
        return n
    except Exception as e:
        _stop_hb = True
        write_node_log(td, 'strategy', 'error', 0, str(e), rid)
        raise

def dag_task_stats(trade_date=None, **kw):
    from datetime import date; td = trade_date or str(date.today()); rid = _rid(kw)
    write_node_log(td, 'stats', 'running', 0, '统计中', rid)
    try:
        update_node_progress(td, 'stats', 0, '全库统计…', rid)
        r = generate_stats()
        update_node_progress(td, 'stats', r, f'完成，{r}项', rid)
        write_node_log(td, 'stats', 'ok', r or 7, '', rid)
        return r
    except Exception as e:
        write_node_log(td, 'stats', 'error', 0, str(e), rid)
        raise

def dag_task_completeness(trade_date=None, **kw):
    """计算当日数据完整度并写入 daily_completeness 表。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    from datetime import date as _dd; td = trade_date or str(_dd.today()); rid = _rid(kw)
    write_node_log(td, 'daily_completeness', 'running', 0, '计算中', rid)
    try:
        db = get_sync_db()
        update_node_progress(td, 'daily_completeness', 0, '查询个股数…', rid)
        for t, col in [('daily_quote', 'stock_rows'), ('index_daily_quote', 'index_rows')]:
            try:
                cnt = db.execute(text(f"SELECT COUNT(*) FROM {t} WHERE trade_date=:d"), {"d": td}).scalar() or 0
                db.execute(text(f"INSERT INTO daily_completeness (trade_date, {col}) VALUES (:d, :c) ON CONFLICT (trade_date) DO UPDATE SET {col}=:c, updated_at=CURRENT_TIMESTAMP"), {"d": td, "c": cnt})
            except: db.rollback()
        try:
            etf = db.execute(text("SELECT COUNT(*) FROM daily_quote WHERE trade_date=:d AND (stock_code LIKE '51%' OR stock_code LIKE '159%' OR stock_code LIKE '56%')"), {"d": td}).scalar() or 0
            db.execute(text("INSERT INTO daily_completeness (trade_date, etf_rows) VALUES (:d, :c) ON CONFLICT (trade_date) DO UPDATE SET etf_rows=:c, updated_at=CURRENT_TIMESTAMP"), {"d": td, "c": etf})
        except: pass
        try:
            fund = db.execute(text("SELECT COUNT(*) FROM stock_fundamentals WHERE updated_at::date=:d"), {"d": td}).scalar() or 0
            db.execute(text("INSERT INTO daily_completeness (trade_date, fund_rows) VALUES (:d, :c) ON CONFLICT (trade_date) DO UPDATE SET fund_rows=:c, updated_at=CURRENT_TIMESTAMP"), {"d": td, "c": fund})
        except: pass
        db.commit(); db.close()
        write_node_log(td, 'daily_completeness', 'ok', 4, '完成', rid)
        return 4
    except Exception as e:
        db.rollback(); db.close()
        write_node_log(td, 'daily_completeness', 'error', 0, str(e), rid)
        raise


dag = DagExecutor()

def _node_enter(name, status, **ctx):
    td = str(ctx.get('trade_date', '')) or ''
    rid = ctx.get('run_id', '') or ''
    if not td:
        from datetime import date as _dd; td = str(_dd.today())
    write_node_log(td, name, status, 0, '', rid)
dag.on_node_enter = _node_enter

def dag_task_daily_update(trade_date=None, **kw):
    td = str(kw.get('trade_date', '')) or (trade_date or '')
    rid = _rid(kw)
    write_node_log(td, 'daily_update', 'running', 0, '启动中', rid)
    write_node_log(td, 'daily_update', 'ok', 0, '启动完成', rid)
    return True

dag.add(DagNode("daily_update", deps=[],  fn=dag_task_daily_update))
# daily_update 的子节点全部跑完后更新状态（在 dag 执行器外部无法自动感知，由子节点各自负责）
dag.add(DagNode("kline",    deps=["daily_update"],        fn=dag_task_kline))
dag.add(DagNode("index",    deps=["daily_update"],        fn=dag_task_index))
dag.add(DagNode("etf",      deps=["daily_update"],        fn=dag_task_etf))
dag.add(DagNode("fund",     deps=["daily_update"],        fn=dag_task_fund))
dag.add(DagNode("treemap",  deps=["kline"],                fn=dag_task_treemap))
dag.add(DagNode("strategy", deps=["kline"],                fn=dag_task_strategy))
dag.add(DagNode("stats",    deps=["treemap","strategy","index","etf","fund"], fn=dag_task_stats))
dag.add(DagNode("daily_completeness", deps=["stats"], fn=dag_task_completeness))


# ══════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════
if __name__ == '__main__':
    date_str = sys.argv[1] if len(sys.argv) > 1 else ""
    if not date_str:
        from datetime import date as _dd
        date_str = _dd.today().isoformat()
    trigger = sys.argv[2] if len(sys.argv) > 2 else ""
    if trigger:
        logger.info(f"===== DAG 触发: {trigger} ({date_str}) =====")
        dag.run(trigger, trade_date=date_str)
    else:
        logger.info(f"===== DAG 全量执行 ({date_str}) =====")
        dag.run_all(trade_date=date_str)
    logger.info(f"===== DAG 完成 =====")
