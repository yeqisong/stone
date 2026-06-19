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
    from app.db.connection import get_sync_db
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
    # 一次 GROUP BY 同时拿到上交所和深交所行数
    dq_counts = {}
    try:
        rows = db.execute(text("SELECT exchange, COUNT(*) FROM daily_quote GROUP BY exchange")).fetchall()
        for r in rows: dq_counts[r[0]] = r[1]
    except: db.rollback()

    tables = [
        ('上交所A股', lambda: (dq_counts.get('SSE', 0), q("SELECT COUNT(*) FROM stock_master WHERE exchange='SSE' AND status='N' AND stock_type='stock'")),
         ("SELECT MIN(trade_date)::text FROM daily_quote WHERE exchange='SSE'", "SELECT MAX(trade_date)::text FROM daily_quote WHERE exchange='SSE'")),
        ('深交所A股', lambda: (dq_counts.get('SZSE', 0), q("SELECT COUNT(*) FROM stock_master WHERE exchange='SZSE' AND status='N' AND stock_type='stock'")),
         ("SELECT MIN(trade_date)::text FROM daily_quote WHERE exchange='SZSE'", "SELECT MAX(trade_date)::text FROM daily_quote WHERE exchange='SZSE'")),
        ('指数日K线', lambda: (q("SELECT COALESCE((SELECT reltuples::bigint FROM pg_class WHERE relname='index_daily_quote'),0)"), q("SELECT COUNT(*) FROM stock_master WHERE stock_type='index'")),
         ("SELECT MIN(trade_date)::text FROM index_daily_quote", "SELECT MAX(trade_date)::text FROM index_daily_quote")),
        ('ETF日K线', None, (None, None)),  # 下面单独处理
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
            if fn is None and label == 'ETF日K线':
                # ETF 行数：直接 SQL，避免 q() 内 text() 参数转义问题
                rows = 0
                try:
                    r = db.execute(text("SELECT COUNT(*) FROM daily_quote WHERE LEFT(stock_code,2)='15' OR LEFT(stock_code,1)='5'"))
                    rows = r.scalar() or 0
                except Exception as e:
                    logger.warning(f"[pipeline] ETF 行数查询失败: {e}")
                    try: db.rollback()
                    except: pass
                items = q("SELECT COUNT(*) FROM stock_master WHERE stock_type='etf'") or 0
                s = {'label': label, 'rows': rows, 'items': items}
            else:
                rows, items = fn()
                s = {'label': label, 'rows': rows or 0, 'items': items}
            # 补充起止日期
            if date_q and date_q[0]:
                sr = db.execute(text(date_q[0])).scalar()
                er = db.execute(text(date_q[1])).scalar()
                if sr: s['start'] = str(sr)[:10]
                if er: s['end'] = str(er)[:10]
            stats.append(s)
        except Exception as e:
            logger.warning(f"[pipeline] 统计 {label} 失败: {e}")
            try: db.rollback()
            except: pass
            stats.append({'label': label, 'rows': -1, 'items': 0})
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

    # 写入 data_stats_cache（API 从此表读取，避免每次实时 COUNT）
    db.execute(text("INSERT INTO data_stats_cache (stats_json, computed_at) VALUES (:json, CURRENT_TIMESTAMP)"),
               {"json": _json.dumps(stats, ensure_ascii=False)})
    db.commit()
    db.close()
    logger.info(f"[pipeline] 数据统计完成并落库: {len(stats)} 项")
    return len(stats)


# ══════════════════════════════════════════
# 运行日志
# ══════════════════════════════════════════

def write_node_log(trade_date: str = '', node_name: str = '', status: str = 'success',
                   rows: int = 0, detail: str = '', run_id: str = '', log_id: int = None):
    """写入/更新节点运行日志。

    有 log_id 时用主键直查 (WHERE id=:log_id)，否则回退到 4 列匹配。
    status='pending' 时执行 INSERT 并返回新行的 id。
    """
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    try:
        db = get_sync_db()
        if status == 'pending':
            result = db.execute(text("""
                INSERT INTO dag_run_log (trade_date, node_name, run_id, status, rows, detail, created_at)
                VALUES (:d, :n, :rid, 'pending', 0, :dt, CURRENT_TIMESTAMP)
                RETURNING id
            """), {"d": trade_date, "n": node_name, "rid": run_id, "dt": detail or '待进行'})
            new_id = result.scalar()
            db.commit()
            db.close()
            return new_id

        # 非 pending：构建 SET 子句 + WHERE 条件
        if log_id is not None:
            where = "WHERE id = :lid"
            params = {"lid": log_id}
        else:
            where = "WHERE trade_date=:d AND node_name=:n AND run_id=:rid AND status='running'"
            params = {"d": trade_date, "n": node_name, "rid": run_id}

        if status == 'running':
            db.execute(text(f"""
                UPDATE dag_run_log SET status='running', started_at=CURRENT_TIMESTAMP,
                    heartbeat_at=CURRENT_TIMESTAMP, detail=:dt
                {where}
            """), {**params, "dt": detail or '进行中'})
        else:
            db.execute(text(f"""
                UPDATE dag_run_log SET status=:s, rows=:rr, detail=:dt, finished_at=CURRENT_TIMESTAMP
                {where}
            """), {**params, "s": status, "rr": rows, "dt": detail})

        db.commit()
        db.close()
    except Exception:
        pass


def update_node_progress(log_id: int = None, trade_date: str = '', node_name: str = '',
                         rows: int = None, detail: str = None, run_id: str = ''):
    """更新运行中节点的心跳 + 可选进度数据。

    有 log_id 时用主键直查，否则回退到 4 列匹配。
    """
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    try:
        db = get_sync_db()
        parts = ["heartbeat_at=CURRENT_TIMESTAMP"]
        if rows is not None:
            parts.append(f"rows={rows}")
        if detail is not None:
            parts.append("detail=:dt")

        if log_id is not None:
            where = "WHERE id = :lid"
            params = {"lid": log_id}
        else:
            where = "WHERE trade_date=:d AND node_name=:n AND run_id=:rid AND status='running'"
            params = {"d": trade_date, "n": node_name, "rid": run_id}
        if detail is not None:
            params["dt"] = detail

        db.execute(text(f"UPDATE dag_run_log SET {','.join(parts)} {where}"), params)
        db.commit()
        db.close()
    except Exception:
        pass


# ══════════════════════════════════════════
# DAG 结构定义（唯一来源，供 API 和流程图使用）
# ══════════════════════════════════════════

def _load_dag_structure():
    """从 dag_config 表读取 DAG 结构（唯一来源）。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    try:
        db = get_sync_db()
        rows = db.execute(text("SELECT node_name, deps, label FROM dag_config ORDER BY sort_order")).fetchall()
        db.close()
        return [{"name": r[0], "deps": [d for d in r[1].split(',') if d], "label": r[2]} for r in rows]
    except:
        return []

DAG_STRUCTURE = _load_dag_structure()

# ══════════════════════════════════════════
# DAG 定义 + DAG 包裹函数
# ══════════════════════════════════════════

def _rid(kw): return kw.get('run_id', '')

import threading as _t
import time as _time

def _hb_thread(log_id, rid, stop, start_time):
    """心跳线程：每 25 秒更新 heartbeat + 已运行时长。超时 5 分钟无进展则标记失败。"""
    from app.signal import is_stop_requested, clear_stop_request
    last_rows = -1
    idle_start = None
    while not stop.is_set():
        if is_stop_requested(rid):
            _terminate_node(log_id, '用户手动终止')
            clear_stop_request(rid)
            stop.set()
            return
        elapsed = int(_time.time() - start_time)
        detail = f'采集中 (已运行 {elapsed//60}分{elapsed%60}秒)' if elapsed >= 60 else f'采集中 ({elapsed}秒)'
        try:
            from app.db.connection import get_sync_db
            from sqlalchemy import text as _sql
            db = get_sync_db()
            r = db.execute(_sql("SELECT rows FROM dag_run_log WHERE id=:lid"), {"lid": log_id}).scalar()
            rows_count = int(r) if r else 0
            db.close()
        except:
            rows_count = None
        update_node_progress(log_id=log_id, rows=rows_count, detail=detail)
        if rows_count is not None and rows_count == last_rows:
            if idle_start is None:
                idle_start = _time.time()
            elif _time.time() - idle_start > 300:
                write_node_log(log_id=log_id, status='failed', rows=rows_count,
                              detail=f'执行超时(5分钟无进展)')
                stop.set()
                return
        else:
            idle_start = None
            last_rows = rows_count if rows_count is not None else -1
        stop.wait(25)

def _with_hb(log_id, rid, fn):
    """带心跳保护执行函数。"""
    stop = _t.Event()
    start_time = _time.time()
    hb = _t.Thread(target=_hb_thread, args=(log_id, rid, stop, start_time), daemon=True)
    hb.start()
    try:
        return fn()
    finally:
        stop.set()

def _terminate_node(log_id, reason='用户手动终止'):
    """原子节点终止自身：更新日志状态为 failed。"""
    write_node_log(log_id=log_id, status='failed', detail=reason)

def dag_task_kline(trade_date=None, **kw):
    from datetime import date; td = trade_date or str(date.today()); rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('kline')
    force = (kw.get('_node_force', {}) or {}).get('kline', kw.get('force', False))
    write_node_log(log_id=log_id, status='running', detail='采集中')
    def _run():
        from crawler.adapters import get_data_source_manager
        manager = get_data_source_manager()
        source = manager.get_source()

        if source.name == "baostock":
            # 首选路径：baostock 高性能并行下载
            from crawler.baostock_crawler import BaostockCrawler
            c = BaostockCrawler()
            r = c.download_daily_update(date.fromisoformat(td), force=force)
            c.logout()
            r['_source'] = 'baostock'
            return r
        else:
            # Fallback 路径：通过适配器串行拉取
            from app.db.connection import get_sync_db
            from crawler.writers import batch_upsert_kline
            from sqlalchemy import text as _text
            db = get_sync_db()
            codes = [r[0] for r in db.execute(_text(
                "SELECT stock_code FROM stock_master WHERE status='N'"
            )).fetchall()]
            rows = source.fetch_stock_kline(codes, td, td)
            saved = batch_upsert_kline(db, rows)
            db.close()
            return {'rows': saved, '_source': source.name}
    try:
        r = _with_hb(log_id, rid, _run)
        rows = r.get('rows', 0)
        fatal = r.get('fatal', '')
        src = r.get('_source', '?')
        if fatal or rows == 0:
            write_node_log(log_id=log_id, status='failed', detail=f'失败: {fatal}')
        else:
            write_node_log(log_id=log_id, status='success', rows=rows, detail=f'完成 {rows} 行 (来源:{src})')
        return r
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e))
        raise

def dag_task_index(trade_date=None, **kw):
    from datetime import date; td = trade_date or str(date.today()); rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('index')
    force = (kw.get('_node_force', {}) or {}).get('index', kw.get('force', False))
    write_node_log(log_id=log_id, status='running', detail='采集中')
    def _run():
        from crawler.adapters import get_data_source_manager
        manager = get_data_source_manager()
        source = manager.get_source()

        if source.name == "baostock":
            from crawler.baostock_crawler import BaostockCrawler
            c = BaostockCrawler()
            r = c.download_all_index_daily(td, force=force)
            c.logout()
            r['_source'] = 'baostock'
            return r
        else:
            from app.db.connection import get_sync_db
            from crawler.writers import batch_upsert_index_kline
            from sqlalchemy import text as _text
            db = get_sync_db()
            codes = [r[0] for r in db.execute(_text(
                "SELECT DISTINCT index_code FROM index_daily_quote"
            )).fetchall()]
            rows = source.fetch_index_kline(codes, td, td)
            saved = batch_upsert_index_kline(db, rows)
            db.close()
            return {'rows': saved, '_source': source.name}
    try:
        r = _with_hb(log_id, rid, _run)
        rows = r.get('rows', 0) if isinstance(r, dict) else r
        src = r.get('_source', '?') if isinstance(r, dict) else '?'
        if rows == 0:
            write_node_log(log_id=log_id, status='failed', detail='失败: 无数据返回')
        else:
            write_node_log(log_id=log_id, status='success', rows=rows, detail=f'(来源:{src})')
        return r
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e))
        raise
def dag_task_etf(trade_date=None, **kw):
    from datetime import date; td = trade_date or str(date.today()); rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('etf')
    force = (kw.get('_node_force', {}) or {}).get('etf', kw.get('force', False))
    write_node_log(log_id=log_id, status='running', detail='采集中')
    def _run():
        from crawler.adapters import get_data_source_manager
        manager = get_data_source_manager()
        source = manager.get_source()

        if source.name == "baostock":
            from crawler.baostock_crawler import BaostockCrawler
            c = BaostockCrawler(); r = c.download_etf_daily(td, force=force); c.logout()
            r['_source'] = 'baostock'
            return r
        else:
            from app.db.connection import get_sync_db
            from crawler.writers import batch_upsert_kline
            from sqlalchemy import text as _text
            db = get_sync_db()
            codes = [r[0] for r in db.execute(_text(
                "SELECT stock_code FROM stock_master WHERE stock_type='etf'"
            )).fetchall()]
            rows = source.fetch_etf_kline(codes, td, td)
            saved = batch_upsert_kline(db, rows)
            db.close()
            return {'rows': saved, '_source': source.name}
    try:
        r = _with_hb(log_id, rid, _run)
        rows = r.get('rows', 0)
        src = r.get('_source', '?')
        if rows == 0:
            write_node_log(log_id=log_id, status='failed', detail='失败: 无数据返回')
        else:
            write_node_log(log_id=log_id, status='success', rows=rows, detail=f'(来源:{src})')
        return r
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e))
        raise

def dag_task_fund(trade_date=None, **kw):
    td = str(kw.get('trade_date', '')) or (trade_date or ''); rid = _rid(kw)
    if not td: from datetime import date as _dd; td = str(_dd.today())
    log_id = (kw.get('_node_log_ids', {}) or {}).get('fund')
    force = (kw.get('_node_force', {}) or {}).get('fund', kw.get('force', False))
    progress = {'count': 0}
    def progress_cb(n):
        progress['count'] = n
        update_node_progress(log_id=log_id, rows=n, detail=f'处理中 ({n}只)')
    write_node_log(log_id=log_id, status='running', detail='采集中')
    def _run():
        from crawler.adapters import get_data_source_manager
        manager = get_data_source_manager()
        source = manager.get_source()

        if source.name == "baostock":
            from crawler.baostock_crawler import BaostockCrawler
            c = BaostockCrawler()
            r = c.download_fundamentals(force=force, progress_cb=progress_cb)
            c.logout()
            if isinstance(r, dict):
                r['_source'] = 'baostock'
            return r
        else:
            from app.db.connection import get_sync_db
            from crawler.writers import batch_upsert_fundamentals
            from sqlalchemy import text as _text
            db = get_sync_db()
            codes = [r[0] for r in db.execute(_text(
                "SELECT stock_code FROM stock_master WHERE status='N'"
            )).fetchall()]
            rows = source.fetch_fundamentals(codes)
            saved = batch_upsert_fundamentals(db, rows)
            db.close()
            return {'rows': saved, '_source': source.name}
    try:
        r = _with_hb(log_id, rid, _run)
        rows = r.get('rows', 0) if isinstance(r, dict) else r
        src = r.get('_source', '?') if isinstance(r, dict) else '?'
        if rows == 0:
            write_node_log(log_id=log_id, status='failed', detail='失败: 无数据返回')
        else:
            write_node_log(log_id=log_id, status='success', rows=rows, detail=f'完成 {rows} 只 (来源:{src})')
        return r
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e))
        raise

def dag_task_treemap(trade_date=None, **kw):
    from datetime import date; td = trade_date or str(date.today()); rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('treemap')
    write_node_log(log_id=log_id, status='running', detail='生成中')
    def _run():
        for i, m in enumerate(['mcap', 'volume', 'amount', 'pe']):
            update_node_progress(log_id=log_id, rows=i, detail=f'生成{i+1}/4: {m}')
            generate_treemap(td, m)
        return 4
    try:
        r = _with_hb(log_id, rid, _run)
        write_node_log(log_id=log_id, status='success', rows=r or 4)
        return r
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e))
        raise

def dag_task_indicator_full(trade_date=None, **kw):
    """全量初始化 6 张指标表。遍历全部历史 K 线，并行计算写入。"""
    from datetime import date, timedelta
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    from strategy.indicators import bollinger_bands, macd, rsi as calc_rsi, atr, sma, obv
    import pandas as pd
    import numpy as np

    td = trade_date or str(date.today())
    rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('indicator_full')
    write_node_log(log_id=log_id, status='running', detail='全量初始化指标…')

    try:
        db = get_sync_db()
        # 获取全部 stock_code
        codes = db.execute(text("SELECT stock_code FROM stock_master WHERE status='N' AND stock_type='stock'")).fetchall()
        codes = [r[0] for r in codes]
        total = len(codes)
        update_node_progress(log_id=log_id, rows=0, detail=f'共 {total} 只股票')

        errors = 0
        # 逐只计算指标并写入 6 张表
        for idx, code in enumerate(codes):
            try:
                rows = db.execute(text("""
                    SELECT trade_date, open, high, low, close_hfq as close, volume
                    FROM daily_quote WHERE stock_code=:c ORDER BY trade_date ASC
                """), {"c": code}).fetchall()
                if len(rows) < 20:
                    continue

                df = pd.DataFrame(rows, columns=['trade_date','open','high','low','close','volume'])
                # PSQL NUMERIC → Python Decimal，需转为 float 避免与 numpy 运算冲突
                for col in ['open','high','low','close','volume']:
                    df[col] = df[col].astype(float)
                closes = df['close']

                # BOLL
                mid, upper, lower, bw = bollinger_bands(closes)
                # MACD
                dif, dea, macd_hist = macd(closes)
                # RSI
                rsi_vals = calc_rsi(closes)
                # ATR
                atr_vals = atr(df)
                # MA
                ma5 = sma(closes, 5); ma20 = sma(closes, 20); ma60 = sma(closes, 60); ma250 = sma(closes, 250)
                # Volume
                vol_ma5 = sma(df['volume'], 5)
                vol_ratio = df['volume'] / vol_ma5.replace(0, np.nan)
                obv_vals = obv(df)
                obv_ma5 = sma(obv_vals, 5); obv_ma10 = sma(obv_vals, 10)

                for i, r in enumerate(rows):
                    d = str(r[0])
                    if not pd.isna(mid.iloc[i]):
                        db.execute(text("""INSERT INTO stock_indicators_boll (stock_code,trade_date,upper,mid,lower,pct_b,width)
                            VALUES (:c,:d,:u,:m,:l,:p,:w) ON CONFLICT (stock_code,trade_date) DO UPDATE SET
                            upper=EXCLUDED.upper,mid=EXCLUDED.mid,lower=EXCLUDED.lower,pct_b=EXCLUDED.pct_b,width=EXCLUDED.width"""),
                            {"c":code,"d":d,"u":float(upper.iloc[i]),"m":float(mid.iloc[i]),"l":float(lower.iloc[i]),
                             "p":float((closes.iloc[i]-lower.iloc[i])/(upper.iloc[i]-lower.iloc[i])) if upper.iloc[i]!=lower.iloc[i] else 0,
                             "w":float(bw.iloc[i]) if not pd.isna(bw.iloc[i]) else 0})
                    if not pd.isna(dif.iloc[i]):
                        db.execute(text("""INSERT INTO stock_indicators_macd (stock_code,trade_date,dif,dea,hist)
                            VALUES (:c,:d,:df,:de,:h) ON CONFLICT (stock_code,trade_date) DO UPDATE SET
                            dif=EXCLUDED.dif,dea=EXCLUDED.dea,hist=EXCLUDED.hist"""),
                            {"c":code,"d":d,"df":float(dif.iloc[i]),"de":float(dea.iloc[i]),"h":float(macd_hist.iloc[i])})
                    if not pd.isna(rsi_vals.iloc[i]):
                        db.execute(text("""INSERT INTO stock_indicators_rsi (stock_code,trade_date,rsi)
                            VALUES (:c,:d,:r) ON CONFLICT (stock_code,trade_date) DO UPDATE SET rsi=EXCLUDED.rsi"""),
                            {"c":code,"d":d,"r":float(rsi_vals.iloc[i])})
                    if not pd.isna(atr_vals.iloc[i]):
                        db.execute(text("""INSERT INTO stock_indicators_atr (stock_code,trade_date,atr)
                            VALUES (:c,:d,:a) ON CONFLICT (stock_code,trade_date) DO UPDATE SET atr=EXCLUDED.atr"""),
                            {"c":code,"d":d,"a":float(atr_vals.iloc[i])})
                    if not pd.isna(ma5.iloc[i]):
                        db.execute(text("""INSERT INTO stock_indicators_ma (stock_code,trade_date,ma5,ma20,ma60,ma250)
                            VALUES (:c,:d,:m5,:m20,:m60,:m250) ON CONFLICT (stock_code,trade_date) DO UPDATE SET
                            ma5=EXCLUDED.ma5,ma20=EXCLUDED.ma20,ma60=EXCLUDED.ma60,ma250=EXCLUDED.ma250"""),
                            {"c":code,"d":d,"m5":float(ma5.iloc[i]),"m20":float(ma20.iloc[i]),"m60":float(ma60.iloc[i]),"m250":float(ma250.iloc[i])})
                    if not pd.isna(vol_ma5.iloc[i]):
                        db.execute(text("""INSERT INTO stock_indicators_volume (stock_code,trade_date,vol_ma5,vol_ratio,obv,obv_ma5,obv_ma10)
                            VALUES (:c,:d,:v5,:vr,:o,:o5,:o10) ON CONFLICT (stock_code,trade_date) DO UPDATE SET
                            vol_ma5=EXCLUDED.vol_ma5,vol_ratio=EXCLUDED.vol_ratio,obv=EXCLUDED.obv,obv_ma5=EXCLUDED.obv_ma5,obv_ma10=EXCLUDED.obv_ma10"""),
                            {"c":code,"d":d,"v5":float(vol_ma5.iloc[i]),"vr":float(vol_ratio.iloc[i]) if not pd.isna(vol_ratio.iloc[i]) else 0,
                             "o":float(obv_vals.iloc[i]) if not pd.isna(obv_vals.iloc[i]) else 0,
                             "o5":float(obv_ma5.iloc[i]) if not pd.isna(obv_ma5.iloc[i]) else 0,
                             "o10":float(obv_ma10.iloc[i]) if not pd.isna(obv_ma10.iloc[i]) else 0})

            except Exception as e:
                errors += 1
                db.rollback()
                logger.warning(f"[indicator_full] {code} 失败: {e}")
                continue

            if (idx + 1) % 500 == 0:
                db.commit()
                update_node_progress(log_id=log_id, rows=idx+1, detail=f'{idx+1}/{total}')
            # 每 100 只刷新 session + GC 释放 DataFrame 内存
            if (idx + 1) % 100 == 0:
                import gc as _gc
                _gc.collect()
                db.commit()
                try: db.close()
                except: pass
                db = get_sync_db()
        db.commit()
        db.close()
        write_node_log(log_id=log_id, status='success', rows=total, detail=f'{total} 只 ({errors} 错误)')
        return total
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e)[:200])

def dag_task_indicator_incr(trade_date=None, **kw):
    """增量更新 6 张指标表（今日 + 前 260 日回溯）。"""
    from datetime import date, timedelta
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    from strategy.indicators import bollinger_bands, macd, rsi as calc_rsi, atr, sma, obv
    import pandas as pd
    import numpy as np

    td = trade_date or str(date.today())
    rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('indicator_incr')
    write_node_log(log_id=log_id, status='running', detail='增量更新指标…')

    try:
        min_date = (date.fromisoformat(td) - timedelta(days=300)).isoformat()
        db = get_sync_db()
        codes = db.execute(text("SELECT stock_code FROM stock_master WHERE status='N' AND stock_type='stock'")).fetchall()
        codes = [r[0] for r in codes]
        total = len(codes)
        errors = 0

        for idx, code in enumerate(codes):
            try:
                rows = db.execute(text("""
                    SELECT trade_date, open, high, low, close_hfq as close, volume
                    FROM daily_quote WHERE stock_code=:c AND trade_date >= :md ORDER BY trade_date ASC
                """), {"c": code, "md": min_date}).fetchall()
                if len(rows) < 20:
                    continue

                df = pd.DataFrame(rows, columns=['trade_date','open','high','low','close','volume'])
                for col in ['open','high','low','close','volume']:
                    df[col] = df[col].astype(float)
                closes = df['close']
                last_row = rows[-1]
                d = str(last_row[0])

                mid, upper, lower, bw = bollinger_bands(closes)
                dif, dea, macd_hist = macd(closes)
                rsi_vals = calc_rsi(closes)
                atr_vals = atr(df)
                ma5 = sma(closes,5); ma20 = sma(closes,20); ma60 = sma(closes,60); ma250 = sma(closes,250)
                vol_ma5 = sma(df['volume'],5)
                vol_ratio = df['volume'] / vol_ma5.replace(0, np.nan)
                obv_vals = obv(df)

                i = -1  # 只取最后一天
                if not pd.isna(mid.iloc[i]):
                    db.execute(text("""INSERT INTO stock_indicators_boll (stock_code,trade_date,upper,mid,lower,pct_b,width)
                        VALUES (:c,:d,:u,:m,:l,:p,:w) ON CONFLICT (stock_code,trade_date) DO UPDATE SET
                        upper=EXCLUDED.upper,mid=EXCLUDED.mid,lower=EXCLUDED.lower,pct_b=EXCLUDED.pct_b,width=EXCLUDED.width"""),
                        {"c":code,"d":d,"u":float(upper.iloc[i]),"m":float(mid.iloc[i]),"l":float(lower.iloc[i]),
                         "p":float((closes.iloc[i]-lower.iloc[i])/(upper.iloc[i]-lower.iloc[i])) if upper.iloc[i]!=lower.iloc[i] else 0,
                         "w":float(bw.iloc[i]) if not pd.isna(bw.iloc[i]) else 0})
                if not pd.isna(dif.iloc[i]):
                    db.execute(text("""INSERT INTO stock_indicators_macd (stock_code,trade_date,dif,dea,hist)
                        VALUES (:c,:d,:df,:de,:h) ON CONFLICT (stock_code,trade_date) DO UPDATE SET
                        dif=EXCLUDED.dif,dea=EXCLUDED.dea,hist=EXCLUDED.hist"""),
                        {"c":code,"d":d,"df":float(dif.iloc[i]),"de":float(dea.iloc[i]),"h":float(macd_hist.iloc[i])})
                if not pd.isna(rsi_vals.iloc[i]):
                    db.execute(text("""INSERT INTO stock_indicators_rsi (stock_code,trade_date,rsi)
                        VALUES (:c,:d,:r) ON CONFLICT (stock_code,trade_date) DO UPDATE SET rsi=EXCLUDED.rsi"""),
                        {"c":code,"d":d,"r":float(rsi_vals.iloc[i])})
                if not pd.isna(atr_vals.iloc[i]):
                    db.execute(text("""INSERT INTO stock_indicators_atr (stock_code,trade_date,atr)
                        VALUES (:c,:d,:a) ON CONFLICT (stock_code,trade_date) DO UPDATE SET atr=EXCLUDED.atr"""),
                        {"c":code,"d":d,"a":float(atr_vals.iloc[i])})
                if not pd.isna(ma5.iloc[i]):
                    db.execute(text("""INSERT INTO stock_indicators_ma (stock_code,trade_date,ma5,ma20,ma60,ma250)
                        VALUES (:c,:d,:m5,:m20,:m60,:m250) ON CONFLICT (stock_code,trade_date) DO UPDATE SET
                        ma5=EXCLUDED.ma5,ma20=EXCLUDED.ma20,ma60=EXCLUDED.ma60,ma250=EXCLUDED.ma250"""),
                        {"c":code,"d":d,"m5":float(ma5.iloc[i]),"m20":float(ma20.iloc[i]),"m60":float(ma60.iloc[i]),"m250":float(ma250.iloc[i])})
                if not pd.isna(vol_ma5.iloc[i]):
                    obv_ma5_incr = sma(obv_vals, 5); obv_ma10_incr = sma(obv_vals, 10)
                    db.execute(text("""INSERT INTO stock_indicators_volume (stock_code,trade_date,vol_ma5,vol_ratio,obv,obv_ma5,obv_ma10)
                        VALUES (:c,:d,:v5,:vr,:o,:o5,:o10) ON CONFLICT (stock_code,trade_date) DO UPDATE SET
                        vol_ma5=EXCLUDED.vol_ma5,vol_ratio=EXCLUDED.vol_ratio,obv=EXCLUDED.obv,obv_ma5=EXCLUDED.obv_ma5,obv_ma10=EXCLUDED.obv_ma10"""),
                        {"c":code,"d":d,"v5":float(vol_ma5.iloc[i]),"vr":float(vol_ratio.iloc[i]) if not pd.isna(vol_ratio.iloc[i]) else 0,
                         "o":float(obv_vals.iloc[i]) if not pd.isna(obv_vals.iloc[i]) else 0,
                         "o5":float(obv_ma5_incr.iloc[i]) if not pd.isna(obv_ma5_incr.iloc[i]) else 0,
                         "o10":float(obv_ma10_incr.iloc[i]) if not pd.isna(obv_ma10_incr.iloc[i]) else 0})

            except Exception as e:
                errors += 1
                db.rollback()
                logger.warning(f"[indicator_incr] {code} 失败: {e}")
                continue

            if (idx + 1) % 500 == 0:
                db.commit()
        db.commit()
        db.close()
        write_node_log(log_id=log_id, status='success', rows=total, detail=f'{total} 只 ({errors} 错误)')
        return total
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e)[:200])
        raise

def dag_task_model_train(trade_date=None, **kw):
    """XGBoost 模型训练：指标 → 特征 → 训练 3 个回归器 → 存储 best_params。"""
    from datetime import date as _date, timedelta as _td
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    import json as _json
    import pandas as pd
    import numpy as np

    td = trade_date or str(_date.today())
    rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('model_train')
    write_node_log(log_id=log_id, status='running', detail='XGBoost 训练中…')

    try:
        db = get_sync_db()
        ver = db.execute(text("SELECT version FROM model_versions WHERE status='DRAFT' ORDER BY created_at DESC LIMIT 1")).scalar()
        if not ver:
            ver = "v1.0"
            db.execute(text("INSERT INTO model_versions (version, model_name, status) VALUES (:v, :n, 'DRAFT') ON CONFLICT DO NOTHING"),
                       {"v": ver, "n": "自动训练模型"})
            db.commit()
        db.execute(text("UPDATE model_versions SET status='TRAINING', trained_at=CURRENT_TIMESTAMP WHERE version=:v"), {"v": ver})
        db.commit()

        # ── 1. 加载数据 ──
        update_node_progress(log_id=log_id, rows=0, detail='加载指标…')
        end_date = (_date.today() - _td(days=30)).isoformat()  # 留 30 天做验证
        rows = db.execute(text("""
            SELECT b.trade_date, b.stock_code, b.pct_b, b.width,
                   m.dif, m.dea, m.hist, r.rsi, a.atr,
                   ma.ma5, ma.ma20, v.vol_ratio, v.obv
            FROM stock_indicators_boll b
            JOIN stock_indicators_macd m USING (stock_code, trade_date)
            JOIN stock_indicators_rsi r USING (stock_code, trade_date)
            JOIN stock_indicators_atr a USING (stock_code, trade_date)
            JOIN stock_indicators_ma ma USING (stock_code, trade_date)
            JOIN stock_indicators_volume v USING (stock_code, trade_date)
            WHERE b.trade_date >= '2021-01-01' AND b.trade_date <= :ed
            ORDER BY b.trade_date ASC
        """), {"ed": end_date}).fetchall()
        if len(rows) < 5000:
            write_node_log(log_id=log_id, status='failed', detail=f'指标数据不足({len(rows)}行)')
            db.close(); return 0

        df = pd.DataFrame(rows, columns=['trade_date','stock_code','pct_b','width','dif','dea','hist','rsi','atr','ma5','ma20','vol_ratio','obv'])
        for c in ['pct_b','width','dif','dea','hist','rsi','atr','ma5','ma20','vol_ratio','obv']:
            df[c] = df[c].astype(float)
        update_node_progress(log_id=log_id, rows=len(df), detail=f'{len(df)} 行')

        # ── 2. 特征工程 ──
        df['bias_5_20'] = df['ma5'] / df['ma20'] - 1                    # 短期乖离率
        df['vol_ratio_3d'] = df.groupby('stock_code')['vol_ratio'].transform(lambda x: x.rolling(3).mean())  # 3日均量比
        df['obv_slope_7d'] = df.groupby('stock_code')['obv'].transform(lambda x: (x - x.shift(7)) / (x.shift(7).abs() + 1))  # OBV 7日斜率

        FEATURES = ['pct_b','width','dif','dea','hist','rsi','atr','ma5','ma20','vol_ratio','obv','bias_5_20','vol_ratio_3d','obv_slope_7d']
        df = df.dropna(subset=FEATURES + ['ma20'])

        # ── 3. 标签：forward N 日收益 ──
        # 从 daily_quote 批量查每只股票 N 日后的 close_hfq
        logger.info("[train] 计算 forward 标签…")
        labels_5, labels_10, labels_20 = [], [], []
        for _, row in df.iterrows():
            price_today = row['ma20']  # 用 ma20 近似当日价格（或 JOIN daily_quote）
            for days, lst in [(5, labels_5), (10, labels_10), (20, labels_20)]:
                fwd = (_date.fromisoformat(str(row['trade_date'])[:10]) + _td(days=days)).isoformat()
                fwd_price = db.execute(text(
                    "SELECT close_hfq FROM daily_quote WHERE stock_code=:c AND trade_date <= :d ORDER BY trade_date DESC LIMIT 1"
                ), {"c": row['stock_code'], "d": fwd}).scalar()
                if fwd_price:
                    lst.append(float(fwd_price) / float(price_today) - 1)
                else:
                    lst.append(None)

        df['target_5d'] = labels_5; df['target_10d'] = labels_10; df['target_20d'] = labels_20
        df = df.dropna(subset=['target_5d', 'target_10d', 'target_20d'])

        # ── 4. 训练/验证按时间切分 ──
        split_date = sorted(df['trade_date'].unique())[-int(len(df['trade_date'].unique())*0.2)]
        train_mask = df['trade_date'] < split_date
        X_train, Y5_train = df[train_mask][FEATURES], df[train_mask]['target_5d']
        X_val,   Y5_val   = df[~train_mask][FEATURES], df[~train_mask]['target_5d']

        if len(X_train) < 1000 or len(X_val) < 100:
            write_node_log(log_id=log_id, status='failed', detail=f'数据量不足(train={len(X_train)},val={len(X_val)})')
            db.close(); return 0

        # ── 5. 训练 GBDT（sklearn，无需额外依赖）──
        from sklearn.ensemble import GradientBoostingRegressor
        params = {'learning_rate': 0.05, 'max_depth': 5, 'n_estimators': 200,
                  'subsample': 0.8, 'max_features': 0.8, 'random_state': 42}
        models = {}
        import pickle as _pkl
        for label, tname in [('5d', 'target_5d'), ('10d', 'target_10d'), ('20d', 'target_20d')]:
            model = GradientBoostingRegressor(**params)
            model.fit(X_train, df[train_mask][tname])
            r2 = model.score(X_val, df[~train_mask][tname])
            models[label] = {'model': model, 'r2': round(r2, 4)}
            logger.info(f"[train] {label}: R²={r2:.4f}")

        # ── 6. 存储结果 ──
        best_params = _json.dumps({k: {'params': params, 'r2': m['r2'], 'pickle': _pkl.dumps(m['model']).hex()} for k, m in models.items()})
        r2_avg = np.mean([m['r2'] for m in models.values()])
        db.execute(text("UPDATE model_versions SET status='PENDING', best_params=:bp, evaluation_report=:rep WHERE version=:v"), {
            "v": ver, "bp": best_params,
            "rep": _json.dumps({"r2_5d": models['5d']['r2'], "r2_10d": models['10d']['r2'], "r2_20d": models['20d']['r2'], "r2_avg": round(r2_avg, 4)}),
        })
        db.execute(text("INSERT INTO training_trials (version, trial_number, params, score) VALUES (:v,1,:p,:s) ON CONFLICT (version, trial_number) DO UPDATE SET score=EXCLUDED.score"),
                   {"v": ver, "p": _json.dumps(params), "s": round(r2_avg, 4)})
        db.commit(); db.close()
        write_node_log(log_id=log_id, status='success', detail=f'XGBoost 训练完成 R²avg={r2_avg:.4f}')
        return len(df)
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e))
        raise

def _get_preference_thresholds(db) -> dict:
    """读取全局偏好设置，返回信号生成阈值字典。"""
    pref = db.execute(text(
        "SELECT params FROM strategy_config WHERE strategy_name='global_preference'"
    )).scalar()
    import json
    try:
        mode = json.loads(pref).get('mode', 'balanced') if pref else 'balanced'
    except Exception:
        mode = 'balanced'

    thresholds = {
        'left': {
            'buy_score_min': 1, 'boll_lower': 0.25, 'rsi_oversold': 40,
            'sell_boll_upper': 0.75, 'sell_rsi_overbought': 60,
            'stop_loss_pct': 0.10, 'signal_timeout_days': 30,
        },
        'balanced': {
            'buy_score_min': 2, 'boll_lower': 0.20, 'rsi_oversold': 35,
            'sell_boll_upper': 0.80, 'sell_rsi_overbought': 65,
            'stop_loss_pct': 0.08, 'signal_timeout_days': 20,
        },
        'right': {
            'buy_score_min': 3, 'boll_lower': 0.15, 'rsi_oversold': 30,
            'sell_boll_upper': 0.85, 'sell_rsi_overbought': 70,
            'stop_loss_pct': 0.05, 'signal_timeout_days': 10,
        },
    }
    return thresholds.get(mode, thresholds['balanced']), mode


def dag_task_model_signal(trade_date=None, **kw):
    """模型信号生成：读 ACTIVE 模型 + 全局偏好 → 评分 → 写入 signal_history。"""
    from datetime import date as _date, timedelta
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    import json as _json

    td = trade_date or str(_date.today())
    rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('model_signal')
    write_node_log(log_id=log_id, status='running', detail='生成模型信号…')

    try:
        db = get_sync_db()
        # 获取 ACTIVE 模型 + 全局偏好
        ver = db.execute(text("SELECT version FROM model_versions WHERE status='ACTIVE' LIMIT 1")).scalar()
        if not ver:
            write_node_log(log_id=log_id, status='failed', detail='无 ACTIVE 模型')
            db.close()
            return 0

        # 模型配置快照（写入每条信号的 params_snapshot）
        model_cfg = db.execute(text("SELECT config FROM model_versions WHERE version=:v"), {"v": ver}).scalar()
        model_snapshot = _json.dumps({
            "model_version": ver,
            "model_config": model_cfg if isinstance(model_cfg, dict) else (_json.loads(model_cfg) if model_cfg else {}),
            "preference": "",
        }) if model_cfg else "{}"

        t, pref_mode = _get_preference_thresholds(db)

        # ── 尝试加载预测模型 ──
        model_cfg = db.execute(text("SELECT best_params FROM model_versions WHERE version=:v"), {"v": ver}).scalar()
        use_predict = False
        xgb_models = {}
        try:
            if model_cfg:
                bp = _json.loads(model_cfg) if isinstance(model_cfg, str) else model_cfg
                import pickle as _pkl
                for label in ['5d', '10d', '20d']:
                    if label in bp and 'pickle' in bp[label]:
                        xgb_models[label] = _pkl.loads(bytes.fromhex(bp[label]['pickle']))
                use_predict = len(xgb_models) == 3
                write_node_log(log_id=log_id, status='running', detail=f"预测模式 (R²avg={bp.get('5d',{}).get('r2','?'):.3f})" if use_predict else f"规则模式 (偏好:{pref_mode})")
        except Exception as e:
            logger.warning(f"[model_signal] 预测模型加载失败: {e}，回退规则模式")

        if not use_predict:
            write_node_log(log_id=log_id, status='running', detail=f"规则模式 (偏好:{pref_mode}, buy>={t['buy_score_min']})")

        # 读取今日指标（JOIN 6 表获取全部 14 特征）
        rows = db.execute(text("""
            SELECT b.stock_code, b.pct_b, b.width, r.rsi,
                   m.dif, m.dea, m.hist, a.atr,
                   ma.ma5, ma.ma20,
                   v.vol_ratio, v.obv,
                   dq.close_hfq, sm.stock_name
            FROM stock_indicators_boll b
            JOIN stock_indicators_rsi r USING (stock_code, trade_date)
            JOIN stock_indicators_macd m USING (stock_code, trade_date)
            JOIN stock_indicators_atr a USING (stock_code, trade_date)
            JOIN stock_indicators_ma ma USING (stock_code, trade_date)
            JOIN stock_indicators_volume v USING (stock_code, trade_date)
            JOIN daily_quote dq ON dq.stock_code=b.stock_code AND dq.trade_date=b.trade_date
            JOIN stock_master sm ON sm.stock_code=b.stock_code
            WHERE b.trade_date = :d
        """), {"d": td}).fetchall()
        if not rows:
            write_node_log(log_id=log_id, status='failed', detail=f'{td} 无指标数据')
            db.close()
            return 0

        # 先删旧信号再插新（按版本精确清理）
        db.execute(text("DELETE FROM signal_history WHERE signal_date=:d AND strategy_name='model_signal' AND model_version=:v"), {"d": td, "v": ver})
        # ── 收集特征用于批量预测 ──
        valid_idx, valid_features = [], []
        FEATURES = ['pct_b','width','dif','dea','hist','rsi','atr','ma5','ma20','vol_ratio','obv','bias_5_20','vol_ratio_3d','obv_slope_7d']
        if use_predict:
            for i, r in enumerate(rows):
                try:
                    _, pct_b, width, rsi_val, dif, dea, hist, atr_val, ma5, ma20, vol_ratio, obv_val, price, name = r
                    f = [float(x if x else 0) for x in [pct_b, width, dif, dea, hist, rsi_val, atr_val, ma5, ma20, vol_ratio, obv_val]]
                    f.append((float(ma5)/float(ma20)-1) if ma20 and float(ma20)!=0 else 0)
                    f.append(float(vol_ratio) if vol_ratio else 0)
                    f.append(0)
                    valid_features.append(f); valid_idx.append(i)
                except (ValueError, TypeError, IndexError):
                    pass
            if valid_features:
                import pandas as pd
                X_pred = pd.DataFrame(valid_features, columns=FEATURES)
                preds = {}; scores = []
                for label in ['5d','10d','20d']:
                    if label in xgb_models:
                        preds[label] = xgb_models[label].predict(X_pred)
                if preds:
                    scores = [0.5*preds['5d'][j]+0.3*preds['10d'][j]+0.2*preds['20d'][j] for j in range(len(valid_features))]
                    ranked = sorted(zip(range(len(valid_features)), scores), key=lambda x:x[1], reverse=True)
                    top_n = {valid_idx[i] for i, _ in ranked[:200]}

        saved = 0
        for i, r in enumerate(rows):
            code, pct_b, width, rsi_val, dif, dea, hist, atr_val, ma5, ma20, vol_ratio, obv_val, price, name = r
            direction, strength, reason = None, 0, ""
            predict_5d, predict_10d, predict_20d, predict_score = None, None, None, None

            if use_predict and i in valid_idx and preds:
                j = valid_idx.index(i)
                predict_5d = round(float(preds['5d'][j]), 4) if '5d' in preds else None
                predict_10d = round(float(preds['10d'][j]), 4) if '10d' in preds else None
                predict_20d = round(float(preds['20d'][j]), 4) if '20d' in preds else None
                predict_score = round(float(scores[j]), 4) if scores else None
                if i in top_n:
                    direction = 'buy'; strength = min(max(int(predict_score*100) if predict_score else 1, 1), 3)
                    reason = f"预测5d={predict_5d:.2%} 10d={predict_10d:.2%} 20d={predict_20d:.2%}"
            else:
                # ── 规则模式 ──
                buy_score = 0
                if pct_b is not None and float(pct_b) < t['boll_lower']:
                    buy_score += 1; reason += "BOLL下轨; "
                if rsi_val is not None and float(rsi_val) < t['rsi_oversold']:
                    buy_score += 1; reason += "RSI超卖; "
                if dif is not None and hist is not None and float(dif) > float(hist):
                    buy_score += 1; reason += "MACD正柱; "
                if buy_score >= t['buy_score_min']:
                    direction = 'buy'; strength = min(buy_score, 3)
                elif pct_b is not None and float(pct_b) > t['sell_boll_upper'] and rsi_val is not None and float(rsi_val) > t['sell_rsi_overbought']:
                    direction = 'sell'; strength = 2; reason = "BOLL上轨+RSI超买"

            if direction:
                db.execute(text("""
                    INSERT INTO signal_history (signal_date,stock_code,stock_name,direction,strength,strategy_name,reason,price,suggested_action,combined_signal,source_strategies,preference,model_version,params_snapshot,predict_5d_return,predict_10d_return,predict_20d_return,predict_score)
                    VALUES (:d,:c,:n,:dir,:st,'model_signal',:r,:p,:sa,true,:ss,:pref,:mv,:snap,:p5,:p10,:p20,:ps)
                """), {
                    "d": td, "c": code, "n": name or code, "dir": direction, "st": strength,
                    "r": reason, "p": float(price) if price else 0,
                    "sa": "关注建仓" if direction == 'buy' else "考虑减仓",
                    "ss": _json.dumps(["model_signal"]), "mv": ver, "pref": pref_mode,
                    "snap": model_snapshot.replace('"preference": ""', f'"preference": "{pref_mode}"'),
                    "p5": predict_5d, "p10": predict_10d, "p20": predict_20d, "ps": predict_score,
                })
                saved += 1

        db.commit()
        db.close()
        write_node_log(log_id=log_id, status='success', rows=saved, detail=f'{saved} 个信号 (偏好:{pref_mode})')
        return saved
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e))
        raise

def dag_task_model_health(trade_date=None, **kw):
    """模型健康度评估：回填 forward 收益 + 了结信号 + 5 维度评估。"""
    from datetime import date as _date, timedelta
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    import json as _json

    td = trade_date or str(_date.today())
    rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('model_health')
    write_node_log(log_id=log_id, status='running', detail='健康度评估…')

    try:
        db = get_sync_db()
        ver = db.execute(text("SELECT version FROM model_versions WHERE status='ACTIVE' LIMIT 1")).scalar()
        if not ver:
            write_node_log(log_id=log_id, status='failed', detail='无 ACTIVE 模型')
            db.close()
            return 0

        # 1. 回填 forward 收益（5/10/20 日前的信号，所有版本+方向）
        missed = 0
        for days in [5, 10, 20]:
            col = f"forward_{days}d_return"
            target_date = (_date.today() - timedelta(days=days)).isoformat()
            signals = db.execute(text(f"""
                SELECT id, stock_code, signal_date, price, direction FROM signal_history
                WHERE signal_date = :d AND strategy_name = 'model_signal'
            """), {"d": target_date}).fetchall()
            for sig in signals:
                close = db.execute(text(
                    "SELECT close_hfq FROM daily_quote WHERE stock_code=:c AND trade_date=:d"
                ), {"c": sig.stock_code, "d": td}).scalar()
                if close and sig.price and float(sig.price) > 0:
                    ret = (float(close) - float(sig.price)) / float(sig.price)
                    db.execute(text(f"UPDATE signal_history SET {col}=:r WHERE id=:id"),
                               {"r": round(ret, 4), "id": sig.id})
                else:
                    missed += 1
        if missed:
            logger.warning(f"[model_health] forward 收益回填: {missed} 条无 K 线数据(停牌/缺失)，下周期重试")

        # 2. 信号了结检查（按信号自身偏好确定止损线）
        # 预加载偏好的止损映射
        sp_map = {'left': 0.10, 'balanced': 0.08, 'right': 0.05}
        open_sigs = db.execute(text("""
            SELECT id, stock_code, signal_date, price, direction, preference FROM signal_history
            WHERE strategy_name='model_signal' AND status IS NULL
        """)).fetchall()
        for sig in open_sigs:
            close_price = db.execute(text(
                "SELECT close_hfq FROM daily_quote WHERE stock_code=:c AND trade_date=:d"
            ), {"c": sig.stock_code, "d": td}).scalar()
            reason = None
            actual_ret = None
            if close_price and sig.price and float(sig.price) > 0:
                pnl = (float(close_price) - float(sig.price)) / float(sig.price)
                stop = sp_map.get(sig.preference, 0.08)  # 用信号自身偏好
                if sig.direction == 'buy' and pnl < -stop:
                    reason = 'stop_loss'
                    actual_ret = pnl
                elif sig.direction == 'sell':
                    # 卖出信号：价格上涨超过止损线→了结
                    if pnl > stop:
                        reason = 'stop_loss'
                        actual_ret = pnl
            if reason:
                db.execute(text("UPDATE signal_history SET status='closed', actual_return=:r, close_reason=:c, closed_at=CURRENT_DATE WHERE id=:id"),
                           {"r": round(actual_ret, 4) if actual_ret else None, "c": reason, "id": sig.id})

        # 3. 5 维度评估（按版本过滤）
        total = db.execute(text("SELECT COUNT(*) FROM signal_history WHERE strategy_name='model_signal' AND model_version=:v"), {"v": ver}).scalar() or 0
        closed = db.execute(text("SELECT COUNT(*) FROM signal_history WHERE strategy_name='model_signal' AND status='closed' AND model_version=:v"), {"v": ver}).scalar() or 0
        wins = db.execute(text("SELECT COUNT(*) FROM signal_history WHERE strategy_name='model_signal' AND actual_return > 0 AND model_version=:v"), {"v": ver}).scalar() or 0
        win_rate = wins / max(closed, 1)
        avg_f5 = db.execute(text("SELECT AVG(forward_5d_return) FROM signal_history WHERE strategy_name='model_signal' AND forward_5d_return IS NOT NULL AND model_version=:v"), {"v": ver}).scalar() or 0

        health = 'HEALTHY'
        if win_rate < 0.3:
            health = 'CRITICAL'
        elif win_rate < 0.45:
            health = 'WARNING'
        elif win_rate < 0.5:
            health = 'CAUTION'

        db.execute(text("""
            INSERT INTO model_health (version, check_date, health_status, live_win_rate, signal_count, avg_forward_5d, detail)
            VALUES (:v, :d, :h, :wr, :sc, :af, :dt)
            ON CONFLICT (version, check_date) DO UPDATE SET
                health_status=EXCLUDED.health_status, live_win_rate=EXCLUDED.live_win_rate,
                signal_count=EXCLUDED.signal_count, avg_forward_5d=EXCLUDED.avg_forward_5d, detail=EXCLUDED.detail
        """), {
            "v": ver, "d": td, "h": health, "wr": round(win_rate, 4),
            "sc": total, "af": round(float(avg_f5), 4) if avg_f5 else 0,
            "dt": _json.dumps({"closed": closed, "wins": wins, "forward_5d_avg": float(avg_f5) if avg_f5 else 0}),
        })

        db.commit()
        db.close()
        write_node_log(log_id=log_id, status='success', rows=total,
                       detail=f'{health}: 胜率{win_rate:.0%} {total}信号 {closed}了结 (偏好:{pref_mode})')
        return total
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e))
        raise

def dag_task_stats(trade_date=None, **kw):
    from datetime import date; td = trade_date or str(date.today()); rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('stats')
    write_node_log(log_id=log_id, status='running', detail='统计中')
    try:
        update_node_progress(log_id=log_id, rows=0, detail='全库统计…')
        r = generate_stats()
        update_node_progress(log_id=log_id, rows=r, detail=f'完成，{r}项')
        write_node_log(log_id=log_id, status='success', rows=r or 7)
        return r
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e))
        raise

def dag_task_completeness(trade_date=None, **kw):
    """计算当日数据完整度并写入 daily_completeness 表。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    from datetime import date as _dd; td = trade_date or str(_dd.today()); rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('daily_completeness')
    write_node_log(log_id=log_id, status='running', detail='计算中')
    try:
        db = get_sync_db()
        update_node_progress(log_id=log_id, rows=0, detail='查询个股数…')
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
        write_node_log(log_id=log_id, status='success', rows=4, detail='完成')
        return 4
    except Exception as e:
        db.rollback(); db.close()
        write_node_log(log_id=log_id, status='failed', detail=str(e))
        raise


dag = DagExecutor()

def _node_enter(name, status, **ctx):
    td = str(ctx.get('trade_date', '')) or ''
    rid = ctx.get('run_id', '') or ''
    if not td:
        from datetime import date as _dd; td = str(_dd.today())
    return write_node_log(td, name, status, 0, '', rid)
dag.on_node_enter = _node_enter

def dag_task_cron(trade_date=None, **kw):
    """cron 节点 — 纯标记节点，无实际操作。"""
    rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('cron')
    try:
        write_node_log(log_id=log_id, status='running', detail='定时触发')
        write_node_log(log_id=log_id, status='success', detail='触发完成')
        return True
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e))
        raise

def dag_task_daily_update(trade_date=None, **kw):
    rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('daily_update')
    try:
        write_node_log(log_id=log_id, status='running', detail='启动中')
        write_node_log(log_id=log_id, status='success', detail='启动完成')
        return True
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e))
        raise

# 节点名 → 执行函数映射表（dag_config 表定义拓扑，本表只绑定函数）
NODE_FN_MAP = {
    'cron':               dag_task_cron,
    'daily_update':       dag_task_daily_update,
    'kline':              dag_task_kline,
    'index':              dag_task_index,
    'etf':                dag_task_etf,
    'fund':               dag_task_fund,
    'treemap':            dag_task_treemap,
    'stats':              dag_task_stats,
    'daily_completeness': dag_task_completeness,
    'indicator_full':     dag_task_indicator_full,
    'indicator_incr':     dag_task_indicator_incr,
    'model_train':        dag_task_model_train,
    'model_signal':       dag_task_model_signal,
    'model_health':       dag_task_model_health,
}

# 从 dag_config 表动态加载拓扑（唯一来源），绑定 fn_map 中的函数
# try/except 处理首次部署时 dag_config 表尚未创建的情况（init_db 在 lifespan 中创建）
try:
    from app.db.connection import get_sync_db as _get_sync_db
    _db = _get_sync_db()
    try:
        dag.load_from_db(_db, NODE_FN_MAP)
    finally:
        _db.close()
except Exception:
    pass  # 表不存在时跳过，lifespan 中 init_db 后会重新加载


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
