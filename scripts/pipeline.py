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
                db.rollback()
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
        ('ETF日K线', None,
         ("SELECT MIN(trade_date)::text FROM daily_quote WHERE LEFT(stock_code,2)='15' OR LEFT(stock_code,1)='5'",
          "SELECT MAX(trade_date)::text FROM daily_quote WHERE LEFT(stock_code,2)='15' OR LEFT(stock_code,1)='5'")),  # 下面单独处理 rows
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
    except Exception as e:
        logger.error(f"[pipeline] write_node_log 失败: {e}")


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
    except Exception as e:
        logger.error(f"[pipeline] update_node_progress 失败: {e}")


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

# indicator_incr / indicator_full 已下线（v2.6），函数保留为 stub 以兼容旧引用
def dag_task_indicator_full(trade_date=None, **kw):
    return True

def dag_task_indicator_incr(trade_date=None, **kw):
    return True

def _OLD_dag_task_indicator_full(trade_date=None, progress_cb=None, cancel_cb=None,
                             codes=None, **kw):
    """[已下线] 初始化 6 张指标表。遍历全部历史 K 线，并行计算写入。

    Args:
        codes: 股票代码列表，默认从 stock_master 全量获取
        progress_cb: 可选回调 cb(done, total) 每 100 只调用一次
        cancel_cb: 可选回调 cb() → bool，返回 True 表示取消
    """
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

    # 参数快照（记录本次计算使用的参数，写入 indicator_calc_log）
    import json as _json
    params_snapshot = _json.dumps({
        "boll": {"period": 20, "std_mult": 2.0},
        "macd": {"fast": 12, "slow": 26, "signal": 9},
        "rsi": {"period": 14},
        "atr": {"period": 14},
        "ma": {"periods": [5, 20, 60, 250]},
        "volume": {"vol_ma_period": 5},
    })

    try:
        db = get_sync_db()
        # 获取股票代码（外部传入优先，否则全量）
        if codes is None:
            codes = db.execute(text("SELECT stock_code FROM stock_master WHERE status='N' AND stock_type='stock'")).fetchall()
            codes = [r[0] for r in codes]
        total = len(codes)
        update_node_progress(log_id=log_id, rows=0, detail=f'共 {total} 只股票')

        errors = 0
        # 逐只计算指标并写入 6 张表
        for idx, code in enumerate(codes):
            if cancel_cb and cancel_cb():
                logger.info("[indicator_full] 收到取消信号，已处理 {}/{}", idx, total)
                write_node_log(log_id=log_id, status='cancelled', detail=f'用户取消 ({idx}/{total})')
                db.close()
                return 0
            # 每只开始时强制回滚，确保干净事务（防止上一只的异常残留）
            try: db.rollback()
            except: pass
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

                # 记录参数快照
                db.execute(text("""
                    INSERT INTO indicator_calc_log (stock_code, calc_date, params_snapshot, row_count, status)
                    VALUES (:c, CURRENT_DATE, CAST(:ps AS jsonb), :rc, 'success')
                    ON CONFLICT (stock_code, calc_date) DO UPDATE SET
                        params_snapshot=EXCLUDED.params_snapshot, row_count=EXCLUDED.row_count,
                        status='success', error_detail=NULL
                """), {"c": code, "ps": params_snapshot, "rc": len(rows)})

            except Exception as e:
                errors += 1
                db.rollback()
                logger.warning(f"[indicator_full] {code} 失败: {e}")
                try:
                    db.execute(text("""
                        INSERT INTO indicator_calc_log (stock_code, calc_date, params_snapshot, row_count, status, error_detail)
                        VALUES (:c, CURRENT_DATE, CAST(:ps AS jsonb), 0, 'failed', :err)
                        ON CONFLICT (stock_code, calc_date) DO UPDATE SET status='failed', error_detail=:err
                    """), {"c": code, "ps": params_snapshot, "err": str(e)[:500]})
                except Exception:
                    pass
                continue

            if (idx + 1) % 500 == 0:
                db.commit()
                update_node_progress(log_id=log_id, rows=idx+1, detail=f'{idx+1}/{total}')
            # 每 100 只回调 + 刷新 session + GC
            if (idx + 1) % 100 == 0:
                if cancel_cb and cancel_cb():
                    logger.info("[indicator_full] 收到取消信号，已处理 {}/{}", idx + 1, total)
                    write_node_log(log_id=log_id, status='cancelled', detail=f'用户取消 ({idx+1}/{total})')
                    db.close()
                    return 0
                if progress_cb:
                    progress_cb(idx + 1, total)
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

def _OLD_dag_task_indicator_incr(trade_date=None, **kw):
    """[已下线] 增量更新 6 张指标表（今日 + 前 260 日回溯）。"""
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

    import json as _json
    params_snapshot = _json.dumps({
        "boll": {"period": 20, "std_mult": 2.0},
        "macd": {"fast": 12, "slow": 26, "signal": 9},
        "rsi": {"period": 14},
        "atr": {"period": 14},
        "ma": {"periods": [5, 20, 60, 250]},
        "volume": {"vol_ma_period": 5},
    })

    try:
        min_date = (date.fromisoformat(td) - timedelta(days=300)).isoformat()
        db = get_sync_db()
        codes = db.execute(text("SELECT stock_code FROM stock_master WHERE status='N' AND stock_type='stock'")).fetchall()
        codes = [r[0] for r in codes]
        total = len(codes)
        errors = 0

        for idx, code in enumerate(codes):
            try: db.rollback()
            except: pass
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

                # 记录参数快照
                db.execute(text("""
                    INSERT INTO indicator_calc_log (stock_code, calc_date, params_snapshot, row_count, status)
                    VALUES (:c, CURRENT_DATE, CAST(:ps AS jsonb), :rc, 'success')
                    ON CONFLICT (stock_code, calc_date) DO UPDATE SET
                        params_snapshot=EXCLUDED.params_snapshot, row_count=EXCLUDED.row_count,
                        status='success', error_detail=NULL
                """), {"c": code, "ps": params_snapshot, "rc": len(rows)})

            except Exception as e:
                errors += 1
                db.rollback()
                logger.warning(f"[indicator_incr] {code} 失败: {e}")
                try:
                    db.execute(text("""
                        INSERT INTO indicator_calc_log (stock_code, calc_date, params_snapshot, row_count, status, error_detail)
                        VALUES (:c, CURRENT_DATE, CAST(:ps AS jsonb), 0, 'failed', :err)
                        ON CONFLICT (stock_code, calc_date) DO UPDATE SET status='failed', error_detail=:err
                    """), {"c": code, "ps": params_snapshot, "err": str(e)[:500]})
                except Exception:
                    pass
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

# ── 宽表构建（v2.6 — 从 feature_values 长格式 PIVOT 为模型输入宽表）──

def build_feature_wide_table(db, feature_names: list, start_date: str, end_date: str,
                              entity: str = 'stock') -> 'pd.DataFrame':
    """从 feature_values 表构建模型训练/预测用宽表。

    Args:
        db: SQLAlchemy sync session
        feature_names: ['ma_5', 'rsi_14', 'boll_pct_b', ...]
        start_date/end_date: 日期范围
        entity: stock/etf/index

    Returns:
        DataFrame with columns [trade_date, stock_code, {feature_names}..., close, volume]
        缺失特征值填 NaN
    """
    import pandas as pd

    if not feature_names:
        return pd.DataFrame()

    # 1. 批量拉取 feature_values
    rows = db.execute(text("""
        SELECT stock_code, trade_date, feature_name, value
        FROM feature_values
        WHERE feature_name = ANY(:names)
          AND trade_date BETWEEN :sd AND :ed
        ORDER BY stock_code, trade_date
    """), {"names": feature_names, "sd": start_date, "ed": end_date}).fetchall()

    if not rows:
        return pd.DataFrame()

    df_fv = pd.DataFrame(rows, columns=['stock_code', 'trade_date', 'feature_name', 'value'])
    df_fv['trade_date'] = pd.to_datetime(df_fv['trade_date'])

    # 2. PIVOT: 长格式 → 宽表
    df_wide = df_fv.pivot_table(
        index=['stock_code', 'trade_date'],
        columns='feature_name',
        values='value',
        aggfunc='first'
    ).reset_index()

    # 补齐缺失的特征列
    for fn in feature_names:
        if fn not in df_wide.columns:
            df_wide[fn] = None

    # 3. JOIN daily_quote 获取 close/volume
    table = 'daily_quote'
    code_col = 'stock_code'
    ent_filter = "AND exchange IN ('SSE','SZSE')"
    if entity == 'index':
        table = 'index_daily_quote'
        code_col = 'index_code'
        ent_filter = ''
    elif entity == 'etf':
        ent_filter = "AND (LEFT(stock_code,2)='15' OR LEFT(stock_code,1)='5')"

    quotes = db.execute(text(f"""
        SELECT {code_col} as stock_code, trade_date, close_hfq as close, volume
        FROM {table}
        WHERE trade_date BETWEEN :sd AND :ed {ent_filter}
        ORDER BY stock_code, trade_date
    """), {"sd": start_date, "ed": end_date}).fetchall()

    df_q = pd.DataFrame(quotes, columns=['stock_code', 'trade_date', 'close', 'volume'])
    df_q['trade_date'] = pd.to_datetime(df_q['trade_date'])

    # 4. LEFT JOIN 行情
    df_merged = df_wide.merge(df_q, on=['stock_code', 'trade_date'], how='left')

    # 按日期排序
    df_merged = df_merged.sort_values(['trade_date', 'stock_code']).reset_index(drop=True)

    return df_merged


# ── 归因分析引擎（v2.7 — 基准锚定法）──

def run_attribution(db, version: str, df, feature_names: list, val_start: str, val_end: str,
                    initial_cash: float = 1_000_000, max_pos: int = 5,
                    stop_loss: float = 0.08, take_profit: float = 0.15, hold_days: int = 10):
    """三基线归因分析。

    Returns:
        {
            'ideal': {sharpe, total_return, max_dd, win_rate, ...},
            'random': {sharpe, ...},
            'real': {sharpe, ...},
            'matrix': 'execution_loss'|'beta_amplifier'|'dual_driver'|'double_misjudge',
            'brinson': {model_contribution, strategy_contribution, interaction}
        }
    """
    import numpy as np
    from datetime import date as _date

    val_mask = (df['trade_date'] >= val_start) & (df['trade_date'] <= val_end)
    if not val_mask.any():
        return {'error': '验证集无数据'}

    val_df = df[val_mask].copy()
    dates = val_df['trade_date'].values
    codes = val_df['stock_code'].values
    close = val_df['close'].values
    volume = val_df['volume'].values
    X = val_df[feature_names].values if feature_names else np.zeros((len(val_df), 1))

    # 1. 理想化回测：模型预测 + 无摩擦 + 无止盈止损
    try:
        model_path = f"data/models/{version}/xgb_10d.pkl"
        import pickle as _pkl, os as _os
        if _os.path.exists(model_path):
            with open(model_path, 'rb') as f:
                model = _pkl.load(f)
            y_pred = model.predict(X)
        else:
            y_pred = np.random.randn(len(val_df)) * 0.01
    except Exception:
        y_pred = np.random.randn(len(val_df)) * 0.01

    ideal = _backtest(
        val_df['close'].values * 0 + 0.01, y_pred, dates, codes, close, volume,
        hold_days=hold_days, stop_loss=0.99, take_profit=99.0,   # 无止盈止损
        commission=0, stamp_tax=0, slippage=0,                     # 无摩擦
        bt_ver=version, bt_label='ideal'
    )

    # 2. 随机信号：随机预测 + 真实止盈止损
    random_pred = np.random.randn(len(val_df)) * 0.01
    random = _backtest(
        val_df['close'].values * 0, random_pred, dates, codes, close, volume,
        hold_days=hold_days, stop_loss=stop_loss, take_profit=take_profit,
        bt_ver=version, bt_label='random'
    )

    # 3. 真实策略：模型预测 + 真实止盈止损
    real = _backtest(
        val_df['close'].values * 0, y_pred, dates, codes, close, volume,
        hold_days=hold_days, stop_loss=stop_loss, take_profit=take_profit,
        bt_ver=version, bt_label='real'
    )

    # 4. 基准收益（沪深300 同期）
    benchmark_return = 0
    try:
        bm = db.execute(text(
            "SELECT close FROM index_daily_quote WHERE index_code='000300' AND trade_date BETWEEN :s AND :e ORDER BY trade_date"
        ), {"s": val_start, "e": val_end}).fetchall()
        if len(bm) >= 2:
            benchmark_return = (float(bm[-1][0]) / float(bm[0][0]) - 1) if float(bm[0][0]) > 0 else 0
    except Exception:
        pass

    # 5. 归因矩阵
    id_high = ideal.get('sharpe', 0) > 1.0
    rd_high = random.get('sharpe', 0) > 0.5
    if id_high and rd_high:    matrix = 'dual_driver'
    elif id_high and not rd_high: matrix = 'execution_loss'
    elif not id_high and rd_high: matrix = 'beta_amplifier'
    else: matrix = 'double_misjudge'

    # 6. Brinson 归因
    real_ret = real.get('total_return', 0)
    ideal_ret = ideal.get('total_return', 0)
    model_contribution = ideal_ret - benchmark_return
    strategy_contribution = real_ret - ideal_ret
    interaction = real_ret - model_contribution - strategy_contribution - benchmark_return

    return {
        'ideal': {'sharpe': ideal.get('sharpe',0), 'total_return': ideal.get('total_return',0),
                  'win_rate': ideal.get('win_rate',0), 'max_dd': ideal.get('max_dd',0)},
        'random': {'sharpe': random.get('sharpe',0), 'total_return': random.get('total_return',0),
                   'win_rate': random.get('win_rate',0), 'max_dd': random.get('max_dd',0)},
        'real': {'sharpe': real.get('sharpe',0), 'total_return': real.get('total_return',0),
                 'win_rate': real.get('win_rate',0), 'max_dd': real.get('max_dd',0)},
        'benchmark_return': round(benchmark_return, 4),
        'matrix': matrix,
        'brinson': {
            'model_contribution': round(model_contribution, 4),
            'strategy_contribution': round(strategy_contribution, 4),
            'interaction': round(interaction, 4),
        }
    }


def dag_task_model_train(trade_date=None, **kw):
    """Optuna 超参数搜索 + XGBoost 训练 + 逐轮回测 → 存储最优模型。"""
    from datetime import date as _date, timedelta as _td
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    import json as _json
    import pandas as pd
    import numpy as np
    import pickle as _pkl
    import os as _os

    td = trade_date or str(_date.today())
    rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('model_train')
    write_node_log(log_id=log_id, status='running', detail='Optuna 训练中…')

    try:
        db = get_sync_db()
        ver = db.execute(text("SELECT version FROM model_versions WHERE status='DRAFT' ORDER BY created_at DESC LIMIT 1")).scalar()
        if not ver:
            ver = "v1.0"
            db.execute(text("INSERT INTO model_versions (version, model_name, status, config) VALUES (:v, :n, 'DRAFT', :cfg) ON CONFLICT DO NOTHING"),
                       {"v": ver, "n": "自动训练模型", "cfg": _json.dumps({
                           "features": ["boll","macd","rsi","atr","ma","volume"],
                           "ml_enabled": False, "model_type": "xgboost",
                           "train_start": "2021-01-01", "train_end": "2025-12-31",
                           "test_start": "2026-01-01", "test_end": None,
                           "risk": {"stop_loss_pct": 8, "signal_timeout_days": 20},
                       })})
            db.commit()
        db.execute(text("UPDATE model_versions SET status='TRAINING', trained_at=CURRENT_TIMESTAMP WHERE version=:v"), {"v": ver})
        db.commit()

        # ── 1. 加载数据（v2.6: 从 feature_values 宽表读取，替代旧 indicator JOIN）──
        update_node_progress(log_id=log_id, rows=0, detail='加载特征宽表…')
        cfg_row = db.execute(text("SELECT config FROM model_versions WHERE version=:v"), {"v": ver}).scalar()
        cfg = _json.loads(cfg_row) if isinstance(cfg_row, str) else (cfg_row or {})
        data_start = cfg.get('train_start', '2024-01-01')
        end_date = (_date.today() - _td(days=2)).isoformat()

        # 特征列表：优先用 feature_names（v2.6），回退 features 指标组映射
        feature_names = cfg.get('feature_names', [])
        if not feature_names:
            write_node_log(log_id=log_id, status='failed', detail='未配置 feature_names，请在模型配置中选择特征')
            db.execute(text("UPDATE model_versions SET status='DRAFT' WHERE version=:v"), {"v": ver})
            db.commit()
            db.close(); return 0

        df = build_feature_wide_table(db, feature_names, data_start, end_date, 'stock')
        if len(df) < 5000:
            write_node_log(log_id=log_id, status='failed', detail=f'特征数据不足({len(df)}行)，请先执行特征计算')
            db.execute(text("UPDATE model_versions SET status='DRAFT' WHERE version=:v"), {"v": ver})
            db.commit()
            db.close(); return 0

        # 只保留数值列
        feature_cols = [c for c in feature_names if c in df.columns]
        df = df[['trade_date','stock_code'] + feature_cols + ['close','volume']].copy()
        for c in feature_cols + ['close','volume']:
            if c in df.columns:
                df[c] = df[c].astype(float)

        # M1-3: 数据新鲜度断言（不允许包含今天或昨天的未收盘数据）
        max_d = str(df['trade_date'].max())[:10]
        cutoff = (_date.today() - _td(days=2)).isoformat()
        if max_d > cutoff:
            write_node_log(log_id=log_id, status='failed', detail=f'数据新鲜度异常: max={max_d} > cutoff={cutoff}')
            db.execute(text("UPDATE model_versions SET status='DRAFT' WHERE version=:v"), {"v": ver})
            db.commit()
            db.close(); return 0

        # M1-1 扩展: 指标对齐质量检查
        for tname, tlabel in [('boll','BOLL'),('macd','MACD'),('rsi','RSI'),('atr','ATR'),('ma','MA'),('volume','成交量')]:
            tbl = f"stock_indicators_{tname}"
            tcnt = db.execute(text(f"SELECT COUNT(*) FROM {tbl} WHERE trade_date BETWEEN :ds AND :ed"),
                              {"ds": data_start, "ed": end_date}).scalar() or 0
            df_tcnt = len(df)
            if tcnt > 0 and df_tcnt > 0 and abs(tcnt/6 - df_tcnt) > df_tcnt * 0.01:
                logger.warning(f"[train] {tlabel}行数({tcnt})与JOIN后行数({df_tcnt})偏差>1%，可能存在对齐缺口")

        update_node_progress(log_id=log_id, rows=1, detail='步骤1:加载特征')

        # ── 2. 特征工程（v2.6: 使用 feature_names，不再依赖旧指标列名）──
        update_node_progress(log_id=log_id, rows=2, detail='步骤2:特征工程')
        FEATURES = list(feature_names)

        # 派生特征（仅当宽表包含所需列时才计算）
        if 'ma_5' in df.columns and 'ma_20' in df.columns:
            df['bias_5_20'] = df['ma_5'] / df['ma_20'] - 1
            FEATURES.append('bias_5_20')
        if 'vol_ratio' in df.columns:
            df['vol_ratio_3d'] = df.groupby('stock_code')['vol_ratio'].transform(lambda x: x.rolling(3).mean())
            FEATURES.append('vol_ratio_3d')

        # 市场过滤器：沪深300 20日动量
        idx_rows = db.execute(text(
            "SELECT trade_date, close FROM index_daily_quote WHERE index_code='000300' AND trade_date BETWEEN :ds AND :ed ORDER BY trade_date"
        ), {"ds": data_start, "ed": end_date}).fetchall()
        if idx_rows:
            idx_df = pd.DataFrame(idx_rows, columns=['trade_date','idx_close'])
            idx_df['trade_date'] = idx_df['trade_date'].astype(str)
            idx_df['idx_ret_20d'] = idx_df['idx_close'].pct_change(20)
            df = df.merge(idx_df[['trade_date','idx_ret_20d']], on='trade_date', how='left')
            df['idx_ret_20d'] = df['idx_ret_20d'].fillna(0)
            FEATURES.append('idx_ret_20d')

        # 只保留有效特征列 + close/volume
        valid_features = [f for f in FEATURES if f in df.columns]
        df = df.dropna(subset=valid_features)

        # ── 3. 标签：forward N 日收益 ──
        # 从 daily_quote 批量查每只股票 N 日后的 close_hfq
        logger.info("[train] 计算 Triple Barrier 标签…")
        # 参数：止盈 +10%，止损 -5%，时间屏障 = 周期天数
        TAKE_PROFIT = 0.10
        STOP_LOSS = -0.05
        labels_10, labels_20 = [], []
        for _, row in df.iterrows():
            price_today = float(row['close'])
            code = row['stock_code']
            tdate = str(row['trade_date'])[:10]
            for days, lst in [(10, labels_10), (20, labels_20)]:
                # 查询未来的每日收盘价
                fwd_rows = db.execute(text(
                    "SELECT trade_date, close_hfq FROM daily_quote "
                    "WHERE stock_code=:c AND trade_date > :d ORDER BY trade_date ASC LIMIT :n"
                ), {"c": code, "d": tdate, "n": days + 5}).fetchall()
                if not fwd_rows or price_today <= 0:
                    lst.append(None)
                    continue
                label = None
                for fr in fwd_rows:
                    ret = float(fr[1]) / price_today - 1
                    if ret >= TAKE_PROFIT:
                        label = TAKE_PROFIT  # 触及止盈
                        break
                    elif ret <= STOP_LOSS:
                        label = STOP_LOSS   # 触及止损
                        break
                if label is None and len(fwd_rows) >= days:
                    # 未触及任何屏障，按时间到期价算
                    label = float(fwd_rows[min(days-1, len(fwd_rows)-1)][1]) / price_today - 1
                lst.append(label)

        df['target_10d'] = labels_10; df['target_20d'] = labels_20
        df = df.dropna(subset=['target_10d', 'target_20d'])

        # M2-5: winsorize 标签（1%~99%缩尾）
        try:
            from scipy.stats.mstats import winsorize
            for col in ['target_10d','target_20d']:
                df[col] = winsorize(df[col].values, limits=(0.01, 0.01))
        except ImportError:
            pass

        # ── 3. 标签 + 切分 ──
        update_node_progress(log_id=log_id, rows=3, detail='步骤3:标签计算')

        # ── 4. 三重时间切分: train(60%) / val(20%) / test(20%) ──
        dates = sorted(df['trade_date'].unique())
        n = len(dates)
        train_cut = dates[int(n * 0.6)]
        test_cut  = dates[int(n * 0.8)]
        train_mask = df['trade_date'] < train_cut
        val_mask   = (df['trade_date'] >= train_cut) & (df['trade_date'] < test_cut)
        test_mask  = df['trade_date'] >= test_cut

        X_train, Y10_train = df[train_mask][FEATURES], df[train_mask]['target_10d']
        X_val,   Y10_val   = df[val_mask][FEATURES],   df[val_mask]['target_10d']

        if len(X_train) < 1000 or len(X_val) < 100:
            write_node_log(log_id=log_id, status='failed', detail=f'数据量不足(train={len(X_train)},val={len(X_val)})')
            db.execute(text("UPDATE model_versions SET status='DRAFT' WHERE version=:v"), {"v": ver})
            db.commit()
            db.close(); return 0

        # ═══════════════════════════════════════
        # ── Optuna 超参数搜索 + 逐轮回测 ──
        # ═══════════════════════════════════════
        from xgboost import XGBRegressor

        # 读取搜索空间（cfg 已在数据加载阶段获取）
        ss = cfg.get('search_space', {})
        n_trials = cfg.get('optuna_trials', 20)  # XGBoost 多核并行，20 轮足够收敛
        update_node_progress(log_id=log_id, rows=3, detail=f'Optuna实验:0/{n_trials} 开始搜索')

        # ── 回测引擎（模块四：资金管理 + 持仓 + 止损 + T+1）──
        initial_cash = cfg.get('initial_cash', 1000000)
        max_pos = cfg.get('max_positions', 5)
        stop_loss = cfg.get('risk', {}).get('stop_loss_pct', 8) / 100.0
        stamp_tax = cfg.get('stamp_tax', 0.001)
        commission = cfg.get('commission', 0.00025)
        slippage = cfg.get('slippage', 0.001)

        def _backtest(y_true, y_pred, dates, codes, close_prices, volumes, hold_days,
                       bt_ver='', bt_label='', stop_loss=None, take_profit=None,
                       commission=None, stamp_tax=None, slippage=None):
            """回测引擎：资金约束 + 流动性约束 + 整数手约束。

            Args:
                y_true: 实际未来收益率 (hold_days 天后)
                y_pred: 模型预测值
                dates, codes: 对应日期和代码
                close_prices: 当日收盘价
                volumes: 当日成交量（股），用于流动性约束
                hold_days: 持仓天数 (5/10/20)
                bt_ver: 模型版本号
                bt_label: 标签名
                stop_loss: 止损阈值（默认取配置值）
                take_profit: 止盈阈值（默认取配置值 × 2）
                commission/stamp_tax/slippage: 成本参数（默认取配置值）
            """
            sl_val = stop_loss if stop_loss is not None else 0.08
            tp_val = take_profit if take_profit is not None else 0.15
            comm_val = commission if commission is not None else 0.00025
            st_val = stamp_tax if stamp_tax is not None else 0.001
            slip_val = slippage if slippage is not None else 0.001
            val_df = pd.DataFrame({
                'date': dates, 'code': codes, 'pred': y_pred, 'true': y_true,
                'price': close_prices, 'volume': volumes
            })
            sorted_dates = sorted(val_df['date'].unique())

            equity = float(initial_cash)
            cash = float(initial_cash)
            holdings = []  # [{code, buy_price, buy_date, shares}]
            equity_curve = [equity]
            trade_count = 0
            win_count = 0
            trade_log = []  # M6-18: 记录每笔交易明细

            for di, d in enumerate(sorted_dates):
                # ── 1. 平仓：到期或止损 ──
                surviving = []
                for h in holdings:
                    hold_dur = (d - h['buy_date']).days
                    # 获取当前价（用最近一日价格近似）
                    day_data = val_df[(val_df['date'] == d) & (val_df['code'] == h['code'])]
                    if day_data.empty:
                        surviving.append(h)
                        continue
                    cur_price = float(day_data['price'].iloc[0])
                    sell_price = cur_price
                    should_sell = False

                    # 止损时卖价 ≈ 止损价
                    if cur_price <= h['buy_price'] * (1 - sl_val):
                        sell_price = h['buy_price'] * (1 - sl_val)
                        should_sell = True
                    # 止盈
                    if tp_val and cur_price >= h['buy_price'] * (1 + tp_val):
                        sell_price = h['buy_price'] * (1 + tp_val)
                        should_sell = True
                    # 到期平仓
                    if hold_dur >= hold_days:
                        should_sell = True

                    if should_sell:
                        gross = h['shares'] * sell_price
                        sell_cost = gross * (comm_val + st_val) + max(gross * slip_val, 0)
                        net_sell = max(gross - sell_cost, 0)
                        cash_before = cash
                        cash += net_sell
                        buy_gross = h['shares'] * h['buy_price']
                        buy_cost = buy_gross * comm_val + max(buy_gross * (slip_val / 2), 0)
                        pnl = net_sell - (buy_gross + buy_cost)
                        pnl_pct = pnl / (buy_gross + buy_cost) if (buy_gross + buy_cost) > 0 else 0

                        # 分录：卖出记录
                        trade_id = h.get('trade_id', '')
                        cumulative_pnl = sum(t.get('pnl', 0) for t in trade_log) + pnl
                        cumulative_return = cumulative_pnl / initial_cash if initial_cash > 0 else 0

                        pos_value = 0
                        for hh in holdings:
                            if hh['code'] != h['code']:
                                hday = val_df[(val_df['date'] == d) & (val_df['code'] == hh['code'])]
                                if not hday.empty:
                                    pos_value += hh['shares'] * float(hday['price'].iloc[0])
                        market_value = cash + pos_value

                        trade_log.append({
                            'trade_id': trade_id,
                            'action': 'SELL',
                            'date': str(d)[:10],
                            'code': h['code'],
                            'price': round(sell_price, 2),
                            'shares': h['shares'],
                            'amount': round(net_sell, 2),
                            'commission_tax': round(sell_cost, 2),
                            'cash_before': round(cash_before, 2),
                            'cash_after': round(cash, 2),
                            'market_value': round(market_value, 2),
                            'pnl': round(pnl, 2),
                            'pnl_pct': round(pnl_pct, 4),
                            'cumulative_pnl': round(cumulative_pnl, 2),
                            'cumulative_return': round(cumulative_return, 6),
                            'reason': 'stop_loss' if cur_price <= h['buy_price'] * (1 - sl_val)
          else 'take_profit' if tp_val and cur_price >= h['buy_price'] * (1 + tp_val)
          else 'hold_expire',
                            'model_version': bt_ver,
                            'signal_label': bt_label,
                            'hold_days': (d - h['buy_date']).days,
                            'buy_trade_id': trade_id,
                        })
                        trade_count += 1
                        if sell_price > h['buy_price']:
                            win_count += 1
                    else:
                        surviving.append(h)
                holdings = surviving

                # ── 2. 开仓：预测最高 N 只未持仓股票 ──
                day = val_df[val_df['date'] == d].copy()
                # 排除已持仓
                held_codes = {h['code'] for h in holdings}
                day = day[~day['code'].isin(held_codes)]
                if len(day) == 0:
                    # 更新权益（持仓市值 + 现金）
                    equity = cash + sum(h['shares'] * float(
                        val_df[(val_df['date'] == d) & (val_df['code'] == h['code'])]['price'].iloc[0]
                    ) if not val_df[(val_df['date'] == d) & (val_df['code'] == h['code'])].empty else 0 for h in holdings)
                    equity_curve.append(equity)
                    continue

                slots = max_pos - len(holdings)
                if slots <= 0:
                    equity = cash + sum(h['shares'] * float(
                        val_df[(val_df['date'] == d) & (val_df['code'] == h['code'])]['price'].iloc[0]
                    ) if not val_df[(val_df['date'] == d) & (val_df['code'] == h['code'])].empty else 0 for h in holdings)
                    equity_curve.append(equity)
                    continue

                # 只买入预测收益 > min_threshold 的股票（默认 0 = 正收益预期）
                min_threshold = 0.0
                candidates = day[day['pred'] > min_threshold]
                top = candidates.nlargest(slots, 'pred')
                # 资金约束：总资产 × 单票仓位上限 / 买入价
                position_pct = 1.0 / max_pos  # 每只股票占总资产比例
                for _, r in top.iterrows():
                    price = float(r['price'])
                    if price <= 0:
                        continue
                    # 约束1: 资金约束 — 当前总资产 × 仓位上限
                    budget = equity * position_pct
                    buy_price = price * (1 + slip_val / 2)
                    if buy_price <= 0 or budget <= 0:
                        continue
                    shares = int(budget // buy_price // 100) * 100
                    if shares <= 0:
                        continue
                    # 约束2: 流动性约束 — ≤ 当日成交量 × 10%
                    daily_vol = float(r.get('volume', 0) or 0)
                    if daily_vol > 0:
                        shares = min(shares, int(daily_vol * 0.1 // 100) * 100)
                    if shares <= 0:
                        continue
                    # 约束3: 整数手 — 已由 `// 100 * 100` 保证
                    gross = shares * buy_price
                    buy_cost = gross * comm_val + max(gross * (slip_val / 2), 0)
                    total_cost = gross + buy_cost
                    if total_cost > cash:
                        continue
                    cash_before = cash
                    cash -= total_cost

                    trade_id = f"T{len(trade_log)+1:04d}"
                    pos_value = total_cost
                    for hh in holdings:
                        hday = val_df[(val_df['date'] == d) & (val_df['code'] == hh['code'])]
                        if not hday.empty:
                            pos_value += hh['shares'] * float(hday['price'].iloc[0])
                    market_value = cash + pos_value
                    cumulative_pnl = sum(t.get('pnl', 0) for t in trade_log)
                    cumulative_return = cumulative_pnl / initial_cash if initial_cash > 0 else 0

                    # 分录：买入记录
                    trade_log.append({
                        'trade_id': trade_id,
                        'action': 'BUY',
                        'date': str(d)[:10],
                        'code': r['code'],
                        'price': round(buy_price, 2),
                        'shares': shares,
                        'amount': round(total_cost, 2),
                        'commission': round(buy_cost, 4),
                        'cash_before': round(cash_before, 2),
                        'cash_after': round(cash, 2),
                        'market_value': round(market_value, 2),
                        'position_pct': round(total_cost / max(equity, 1), 4),
                        'model_version': bt_ver,
                        'signal_label': bt_label,
                        'signal_reason': f'pred_rank_top{max_pos}',
                    })

                    holdings.append({
                        'code': r['code'],
                        'buy_price': price,
                        'buy_date': d,
                        'shares': shares,
                        'trade_id': trade_id,
                        'signal_source': str(hdays) + 'd',
                    })

                # ── 3. 记录当日权益 ──
                equity = cash
                for h in holdings:
                    hday = val_df[(val_df['date'] == d) & (val_df['code'] == h['code'])]
                    if not hday.empty:
                        equity += h['shares'] * float(hday['price'].iloc[0])
                equity_curve.append(equity)

            # 最终清仓
            last_date = sorted_dates[-1]
            for h in holdings:
                hday = val_df[(val_df['date'] == last_date) & (val_df['code'] == h['code'])]
                if not hday.empty:
                    cash += h['shares'] * float(hday['price'].iloc[0])
                    trade_count += 1
                    if float(hday['price'].iloc[0]) > h['buy_price']:
                        win_count += 1

            total_return = (cash / initial_cash - 1) if initial_cash > 0 else 0
            # 日收益率序列
            eq_arr = np.array(equity_curve)
            daily_rets = eq_arr[1:] / eq_arr[:-1] - 1 if len(eq_arr) > 1 else np.array([0])
            mean_ret = float(np.mean(daily_rets)) if len(daily_rets) > 0 else 0
            std_ret = float(np.std(daily_rets)) if len(daily_rets) > 1 else 1e-6
            sharpe = mean_ret / std_ret * np.sqrt(252) if std_ret > 0 else 0
            win_rate = win_count / trade_count if trade_count > 0 else 0
            cumulative = np.cumprod(1 + daily_rets)
            peak = np.maximum.accumulate(cumulative) if len(cumulative) > 0 else np.array([1])
            max_dd = float(np.min((cumulative - peak) / peak)) if len(cumulative) > 1 else 0
            return {
                'sharpe': round(sharpe, 4), 'win_rate': round(win_rate, 4),
                'max_dd': round(max_dd, 4), 'total_return': round(total_return, 4),
                'total_trades': trade_count, 'equity_curve': [round(e, 2) for e in equity_curve[-50:]],
                'trades': trade_log[-20:]  # 最近20笔交易明细
            }

        best_models = {}
        best_params_store = {}
        best_score = -999
        trial_records = []
        val_dates = df[val_mask]['trade_date'].values
        val_codes = df[val_mask]['stock_code'].values
        val_close = df[val_mask]['close'].values
        val_volume = df[val_mask]['volume'].values
        TARGETS = [('10d','target_10d',10), ('20d','target_20d',20)]

        try:
            import optuna
            from optuna.samplers import TPESampler
            def objective(trial):
                nonlocal best_models, best_params_store, best_score
                # 检查终止信号
                stop_event = kw.get('_stop_event')
                if stop_event and stop_event.is_set():
                    raise optuna.TrialPruned("用户终止训练")
                # 检查模型是否被删除
                r = db.execute(text("SELECT status FROM model_versions WHERE version=:v"), {"v": ver}).fetchone()
                if not r or r[0] != 'TRAINING':
                    raise optuna.TrialPruned("模型已被删除或状态变更")
                lr  = trial.suggest_float('learning_rate', ss.get('learning_rate',[0.01])[0], ss.get('learning_rate',[0.01,0.3])[-1], log=True)
                md  = trial.suggest_int('max_depth', ss.get('max_depth',[3])[0], ss.get('max_depth',[3,10])[-1])
                ne  = trial.suggest_int('n_estimators', ss.get('n_estimators',[100])[0], min(ss.get('n_estimators',[100,300])[-1], 300))
                sub = trial.suggest_float('subsample', 0.6, 1.0)
                cs  = trial.suggest_float('colsample_bytree', 0.5, 1.0)
                ra  = trial.suggest_float('reg_alpha', 1e-8, 10.0, log=True)
                rl  = trial.suggest_float('reg_lambda', 1e-8, 10.0, log=True)
                params = {'learning_rate': lr, 'max_depth': md, 'n_estimators': ne,
                          'subsample': sub, 'colsample_bytree': cs,
                          'reg_alpha': ra, 'reg_lambda': rl,
                          'n_jobs': -1, 'random_state': 42, 'verbosity': 0}
                models = {}
                # 从 train 集再切 10% 做 early stopping
                train_n = int(len(X_train) * 0.9)
                X_tr, X_es = X_train[:train_n], X_train[train_n:]
                Y_tr = {tname: df[train_mask][tname].values[:train_n] for _, tname, _ in TARGETS}
                Y_es = {tname: df[train_mask][tname].values[train_n:] for _, tname, _ in TARGETS}

                total_r2 = 0
                for label, tname, hdays in TARGETS:
                    model = XGBRegressor(**params, early_stopping_rounds=20)
                    model.fit(X_tr, Y_tr[tname], eval_set=[(X_es, Y_es[tname])], verbose=False)
                    r2 = round(float(model.score(X_val, df[val_mask][tname])), 4)
                    models[label] = {'model': model, 'r2': r2}
                    total_r2 += r2
                avg_r2 = total_r2 / 3
                trial_records.append({'trial': len(trial_records)+1, 'params': params, 'r2': round(avg_r2, 4)})
                update_node_progress(log_id=log_id, rows=len(trial_records), detail=f'Optuna实验:{len(trial_records)}/{n_trials} r²={avg_r2:.4f}')
                if avg_r2 > best_score:
                    best_score = avg_r2
                    best_params_store = {k: {'params': params, 'r2': m['r2']} for k, m in models.items()}
                    best_models = {k: m['model'] for k, m in models.items()}
                db.execute(text("INSERT INTO training_trials (version, trial_number, params, score) VALUES (:v,:n,:p,:s) ON CONFLICT (version, trial_number) DO UPDATE SET params=EXCLUDED.params, score=EXCLUDED.score"),
                           {"v": ver, "n": trial.number + 1, "p": _json.dumps(params), "s": round(float(avg_sharpe), 4)})
                db.commit()
                return avg_sharpe
            study = optuna.create_study(direction='maximize', sampler=TPESampler(seed=42))
            study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
        except ImportError:
            # Optuna 未安装时回退单次训练（XGBoost）
            params = {'learning_rate': 0.05, 'max_depth': 5, 'n_estimators': 200,
                      'subsample': 0.8, 'colsample_bytree': 0.8,
                      'n_jobs': -1, 'random_state': 42, 'verbosity': 0}
            for label, tname, hdays in TARGETS:
                model = XGBRegressor(**params)
                model.fit(X_train, df[train_mask][tname])
                r2 = round(float(model.score(X_val, df[val_mask][tname])), 4)
                best_models[label] = model
                best_params_store[label] = {'params': params, 'r2': r2}
            update_node_progress(log_id=log_id, rows=1, detail='训练完成(无Optuna)')

        # ── 最终评估：测试集（Optuna从未见过的数据）──
        update_node_progress(log_id=log_id, rows=4, detail='测试集评估…')
        test_dates = df[test_mask]['trade_date'].values
        test_codes = df[test_mask]['stock_code'].values
        test_close = df[test_mask]['close'].values
        test_volume = df[test_mask]['volume'].values
        test_idx_ret = df[test_mask]['idx_ret_20d'].values
        test_results = {}
        for label, tname, hdays in TARGETS:
            if label in best_models:
                y_pred = best_models[label].predict(df[test_mask][FEATURES])
                bt = _backtest(df[test_mask][tname].values, y_pred, test_dates, test_codes, test_close, test_volume,
                               hdays, bt_ver=ver, bt_label=label,
                               stop_loss=stop_loss, take_profit=stop_loss*2,
                               commission=commission, stamp_tax=stamp_tax, slippage=slippage)
                test_results[label] = bt

        # ── 存储最优模型文件 ──
        update_node_progress(log_id=log_id, rows=5, detail='存储最优模型')
        model_dir = f"data/models/{ver}"
        _os.makedirs(model_dir, exist_ok=True)
        for label in ['10d','20d']:
            if label in best_models:
                path = f"{model_dir}/xgb_{label}.pkl"
                with open(path, 'wb') as f:
                    _pkl.dump(best_models[label], f)
                best_params_store[label]['model_path'] = path

        # ── 汇总回测指标并写入 DB ──
        # 合并 3 个周期的交易明细
        all_trades = []
        for label in ['10d','20d']:
            for t in test_results.get(label, {}).get('trades', []):
                t['horizon'] = label
                all_trades.append(t)
        # 去重：同股票同买入日只保留一笔（不同周期可能重复买入）
        seen = set()
        deduped = []
        for t in all_trades:
            key = (t['code'], t['buy_date'])
            if key not in seen:
                seen.add(key)
                deduped.append(t)
        all_trades = deduped
        all_trades.sort(key=lambda t: t['sell_date'], reverse=True)

        # 过拟合检测：对比 val R² 和 test sharpe（val上高R² + test上低sharpe = 过拟合）
        val_r2s = [best_params_store.get(l,{}).get('r2',0) for l in ['10d','20d']]
        test_sharpes = [test_results.get(l,{}).get('sharpe',0) for l in ['10d','20d']]
        val_r2_avg = float(np.mean(val_r2s)) if val_r2s else 0
        test_sharpe_avg = float(np.mean(test_sharpes)) if test_sharpes else 0
        overfit_warning = val_r2_avg > 0.3 and test_sharpe_avg < 0.5  # 高R²低实盘 = 过拟合信号

        # 基准对比：沪深300 同期收益（用 000300 指数数据）
        benchmark_return = 0
        try:
            bm = db.execute(text(
                "SELECT close FROM index_daily_quote WHERE index_code='000300' AND trade_date BETWEEN :s AND :e ORDER BY trade_date"
            ), {"s": str(test_dates[0])[:10] if len(test_dates) > 0 else '2026-01-01',
                "e": str(test_dates[-1])[:10] if len(test_dates) > 0 else '2026-06-01'}).fetchall()
            if len(bm) >= 2:
                benchmark_return = (float(bm[-1][0]) / float(bm[0][0]) - 1) if float(bm[0][0]) > 0 else 0
        except Exception:
            pass

        # 质量指标：从交易明细计算
        all_pnls = [t['pnl'] for t in all_trades]
        all_pnl_pcts = [t['pnl_pct'] for t in all_trades]
        wins = [p for p in all_pnls if p > 0]
        losses = [abs(p) for p in all_pnls if p < 0]
        profit_factor = sum(wins) / sum(losses) if losses else (999 if wins else 0)
        avg_win = float(np.mean(wins)) if wins else 0
        avg_loss = float(np.mean(losses)) if losses else 0
        max_dd_avg = float(np.mean([test_results.get(l,{}).get('max_dd',0) for l in ['10d','20d']]))

        # 集中度分析：前 3 笔最大盈利占总收益的比例
        sorted_pnls = sorted([t['pnl'] for t in all_trades if t['pnl'] > 0], reverse=True)
        total_profit = sum(sorted_pnls)
        top3_pct = sum(sorted_pnls[:3]) / total_profit * 100 if total_profit > 0 else 0

        bt_summary = {
            'trials': trial_records[-10:] if trial_records else [],
            'trades': all_trades,
            'trade_count': len(all_trades),
            'top3_concentration': round(top3_pct, 1),
            'val_sharpe': round(val_avg, 4),
            'test_sharpe': round(test_avg, 4),
            'overfit_gap': round(overfit_gap, 4),
            'profit_factor': round(profit_factor, 2),
            'avg_win': round(avg_win, 2),
            'avg_loss': round(avg_loss, 2),
            'benchmark_return': round(benchmark_return, 4),
            'sharpe_10d':  test_results.get('10d',{}).get('sharpe',0),
            'sharpe_20d':  test_results.get('20d',{}).get('sharpe',0),
            'win_rate_10d':test_results.get('10d',{}).get('win_rate',0),
            'win_rate_20d':test_results.get('20d',{}).get('win_rate',0),
        }
        avg_sharpe = float(np.mean([bt_summary['sharpe_10d'], bt_summary['sharpe_20d']]))
        avg_win = float(np.mean([bt_summary['win_rate_10d'], bt_summary['win_rate_20d']]))
        best_params = best_params_store

        db.execute(text("UPDATE model_versions SET status='PENDING', best_params=:bp, evaluation_report=:rep, sharpe=:sh, win_rate=:wr, max_drawdown=:md, annual_return=:ar WHERE version=:v"), {
            "v": ver,
            "bp": _json.dumps(best_params),
            "rep": _json.dumps(bt_summary),
            "sh": round(avg_sharpe, 4),
            "wr": round(avg_win, 4),
            "md": round(abs(max_dd_avg), 4),
            "ar": round(avg_sharpe * 0.15, 4),
        })
        db.commit(); db.close()
        write_node_log(log_id=log_id, status='success', detail=f'训练完成: sharpe={avg_sharpe:.3f} win={avg_win:.1%} trials={len(trial_records)}')
        return len(df)

    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e))
        try:
            db.rollback()
            if ver:
                db.execute(text("UPDATE model_versions SET status='DRAFT' WHERE version=:v"), {"v": ver})
                db.commit()
        except Exception:
            try: db.rollback()
            except: pass
        try: db.close()
        except: pass
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
                import pickle as _pkl, os as _os
                for label in ['5d', '10d', '20d']:
                    if label in bp and 'model_path' in bp[label]:
                        path = bp[label]['model_path']
                        if _os.path.exists(path):
                            with open(path, 'rb') as f:
                                xgb_models[label] = _pkl.load(f)
                use_predict = len(xgb_models) == 3
                write_node_log(log_id=log_id, status='running', detail=f"预测模式 (R²avg={bp.get('5d',{}).get('r2','?'):.3f})" if use_predict else f"规则模式 (偏好:{pref_mode})")
        except Exception as e:
            logger.warning(f"[model_signal] 预测模型加载失败: {e}，回退规则模式")

        if not use_predict:
            write_node_log(log_id=log_id, status='running', detail=f"规则模式 (偏好:{pref_mode}, buy>={t['buy_score_min']})")

        # 读取今日特征（v2.6: feature_values 宽表替代旧 indicator JOIN）
        today_str = str(_date.today())
        model_cfg_json = db.execute(text(
            "SELECT config FROM model_versions WHERE version=:v"
        ), {"v": active_ver}).scalar()
        model_cfg_obj = _json.loads(model_cfg_json) if isinstance(model_cfg_json, str) else (model_cfg_json or {})
        feature_names = model_cfg_obj.get('feature_names', [])
        if not feature_names:
            write_node_log(log_id=log_id, status='failed', detail='未配置 feature_names')
            db.close(); return 0

        df_today = build_feature_wide_table(db, feature_names, today_str, today_str, 'stock')
        if df_today.empty:
            write_node_log(log_id=log_id, status='success', rows=0, detail='今日无特征数据')
            db.close(); return 0

        # 准备 stock_name
        codes = df_today['stock_code'].unique().tolist()
        names_map = {}
        if codes:
            nr = db.execute(text("SELECT stock_code, stock_name FROM stock_master WHERE stock_code = ANY(:c)"),
                           {"c": codes}).fetchall()
            names_map = {r[0]: r[1] for r in nr}

        rows = []
        for _, r in df_today.iterrows():
            d = {'stock_code': r['stock_code'], 'stock_name': names_map.get(r['stock_code'], '')}
            for fn in feature_names:
                d[fn] = float(r[fn]) if fn in r and pd.notna(r[fn]) else 0
            d['close'] = float(r.get('close', 0)) if pd.notna(r.get('close', 0)) else 0
            rows.append(type('Row', (), d)())

        # 先删旧信号再插新
        db.execute(text("DELETE FROM signal_history WHERE signal_date=:d AND strategy_name='model_signal' AND model_version=:v"), {"d": td, "v": ver})

        # 评分信号生成（规则模式）
        buy_count = 0
        for r in rows:
            code = r.stock_code
            name = r.stock_name
            price = r.close
            if not price or price == 0:
                continue
            # 规则评分
            score = 0
            reasons = []
            pct_b = getattr(r, 'boll_pct_b', 0) or 0
            rsi_val = getattr(r, 'rsi_14', 50) or 50
            dif = getattr(r, 'macd_dif', 0) or 0
            hist = getattr(r, 'macd_hist', 0) or 0

            if pct_b < t['boll_lower']: score += 1; reasons.append('BOLL超卖')
            if rsi_val < t['rsi_oversold']: score += 1; reasons.append('RSI超卖')
            if dif > hist: score += 1; reasons.append('MACD正柱')

            direction = 'buy' if score >= t['buy_score_min'] else ''
            if direction:
                db.execute(text("""
                    INSERT INTO signal_history (signal_date, stock_code, stock_name, direction, strength, price, strategy_name, reason, combined_signal, model_version, params_snapshot, preference)
                    VALUES (:d,:c,:n,:dir,:s,:p,'model_signal',:r,true,:v,:sn,:pref)
                """), {"d": td, "c": code, "n": name, "dir": direction, "s": score, "p": price, "r": ';'.join(reasons), "v": ver, "sn": model_snapshot, "pref": pref_mode})
                buy_count += 1

        db.commit()
        write_node_log(log_id=log_id, status='success', rows=buy_count, detail=f'{buy_count} 买入 {len(rows)} 扫描')
        db.close()
        return buy_count
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e)[:200])
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
    """计算当日数据完整度（含分母 baseline）并写入 daily_completeness 表。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    from datetime import date as _dd; td = trade_date or str(_dd.today()); rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('daily_completeness')
    write_node_log(log_id=log_id, status='running', detail='计算中')
    try:
        db = get_sync_db()
        # 分子
        stock = db.execute(text("SELECT COUNT(*) FROM daily_quote WHERE trade_date=:d AND NOT (LEFT(stock_code,2)='15' OR LEFT(stock_code,1)='5')"), {"d": td}).scalar() or 0
        idx_r = db.execute(text("SELECT COUNT(*) FROM index_daily_quote WHERE trade_date=:d"), {"d": td}).scalar() or 0
        etf   = db.execute(text("SELECT COUNT(*) FROM daily_quote WHERE trade_date=:d AND (LEFT(stock_code,2)='15' OR LEFT(stock_code,1)='5')"), {"d": td}).scalar() or 0
        fund  = db.execute(text("SELECT COUNT(*) FROM stock_fundamentals WHERE updated_at::date=:d"), {"d": td}).scalar() or 0
        # 分母
        stock_bl = db.execute(text("SELECT COUNT(*) FROM stock_master WHERE stock_type='stock' AND status='N' AND ipo_date <= :d"), {"d": td}).scalar() or 0
        index_bl = db.execute(text("SELECT COUNT(*) FROM stock_master WHERE stock_type='index' AND ipo_date <= :d"), {"d": td}).scalar() or 0
        etf_bl   = db.execute(text("SELECT COUNT(*) FROM stock_master WHERE stock_type='etf' AND ipo_date <= :d"), {"d": td}).scalar() or 0

        db.execute(text("""
            INSERT INTO daily_completeness (trade_date, stock_rows, index_rows, etf_rows, fund_rows,
                stock_baseline, index_baseline, etf_baseline, fund_baseline)
            VALUES (:d, :sr, :ir, :er, :fr, :sb, :ib, :eb, :fb)
            ON CONFLICT (trade_date) DO UPDATE SET
                stock_rows=EXCLUDED.stock_rows, index_rows=EXCLUDED.index_rows,
                etf_rows=EXCLUDED.etf_rows, fund_rows=EXCLUDED.fund_rows,
                stock_baseline=EXCLUDED.stock_baseline, index_baseline=EXCLUDED.index_baseline,
                etf_baseline=EXCLUDED.etf_baseline, fund_baseline=EXCLUDED.fund_baseline,
                updated_at=CURRENT_TIMESTAMP
        """), {"d": td, "sr": stock, "ir": idx_r, "er": etf, "fr": fund,
               "sb": stock_bl, "ib": index_bl, "eb": etf_bl, "fb": stock_bl})
        db.commit(); db.close()
        write_node_log(log_id=log_id, status='success', rows=4, detail=f'stock={stock} idx={idx_r} etf={etf}')
        return 4
    except Exception as e:
        try: db.rollback(); db.close()
        except: pass
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

def dag_task_feature_backfill(log_id: int, trade_date: str, force: bool = False, **kwargs) -> bool:
    """DAG 节点：历史特征补数（3.4）。"""
    from app.db.connection import get_sync_db
    from app.db.schema import write_node_log
    from scripts.feature_compute import compute_all_features

    write_node_log(log_id=log_id, status='running', detail='启动历史特征补数')
    db = get_sync_db()
    try:
        result = compute_all_features(db, target_entity="stock", start_date="2020-01-01")
        msg = f"全量补数完成: {result['features']}个特征, {result['rows']}行"
        if result.get("errors"):
            msg += f", {len(result['errors'])}个失败"
        write_node_log(log_id=log_id, status='success', detail=msg, rows=result.get("rows", 0))
        return True
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e))
        raise
    finally:
        db.close()


def dag_task_feature_compute(log_id: int, trade_date: str, force: bool = False, **kwargs) -> bool:
    """DAG 节点：计算所有已启用特征值。"""
    from app.db.connection import get_sync_db
    from app.db.schema import write_node_log
    from scripts.feature_compute import compute_all_features
    from datetime import date as dt, timedelta

    write_node_log(log_id=log_id, status='running', detail='启动特征计算')
    db = get_sync_db()
    try:
        today = dt.today().strftime("%Y-%m-%d")
        # 增量：仅计算最近 10 天（首次运行可改为全量）
        start = force and "2020-01-01" or (dt.today() - timedelta(days=10)).strftime("%Y-%m-%d")
        result = compute_all_features(db, target_entity="stock", start_date=start, end_date=today)
        msg = f"完成: {result['features']}个特征, {result['rows']}行"
        if result.get("errors"):
            msg += f", {len(result['errors'])}个失败"
        write_node_log(log_id=log_id, status='success', detail=msg, rows=result.get("rows", 0))
        return True
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e))
        raise
    finally:
        db.close()

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
    'model_train':        dag_task_model_train,
    'model_signal':       dag_task_model_signal,
    'model_health':       dag_task_model_health,
    'feature_compute':    dag_task_feature_compute,
    'feature_backfill':  dag_task_feature_backfill,
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
