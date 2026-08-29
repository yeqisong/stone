#!/usr/bin/env python3
"""
DAG 流水线 —— 有向无环图任务调度。
每个节点声明依赖，调度器按拓扑排序执行 + 手工触发自动传播下游。

手动运行:
  python3 scripts/pipeline.py 2026-06-08        全量执行
  python3 scripts/pipeline.py 2026-06-08 kline  只触发 kline 链路
"""
import sys, os, re
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
        # 行业改用 stock_master 申万行业（l1/l2），此前用 f.industry 基本面行业已全空 → 全部落到"其他"
        query_col = {"mcap": "d.close, sm.industry_l1, sm.industry_l2, f.market_cap, f.total_shares",
                     "volume": "d.close, sm.industry_l1, sm.industry_l2, d.volume",
                     "amount": "d.close, sm.industry_l1, sm.industry_l2, d.amount",
                     "pe": "d.close, sm.industry_l1, sm.industry_l2, NULL"}.get(metric, "d.close, sm.industry_l1, sm.industry_l2, f.market_cap, f.total_shares")
        rows = db.execute(text(f"""
            SELECT d.stock_code, sm.stock_name, {query_col}, d.trade_date
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

        l1_map, l1_names = {}, {'U':'其他'}
        for r in rows:
            code, name, price_str = r[0], r[1], float(r[2]) if r[2] else 0
            price = price_str
            try:
                if metric == 'mcap':
                    mcap_raw = float(r[5]) if len(r) > 5 and r[5] else None
                    shares = float(r[6]) if len(r) > 6 and r[6] else None
                    val = mcap_raw or (shares * price if shares else price * 100000000)
                elif metric == 'volume':
                    val = float(r[5]) if len(r) > 5 and r[5] else 0
                elif metric == 'pe':
                    val = 50
                    try:
                        pe_rows = db.execute(text("SELECT pe_ttm FROM stock_fundamentals_history WHERE stock_code=:c AND report_date>=:start ORDER BY report_date ASC"), {"c": code, "start": f"{int(trade_date[:4])-1}-{trade_date[5:]}"}).fetchall()
                        pes = [float(rr[0]) for rr in pe_rows if rr[0]]
                        if len(pes) >= 4:
                            import numpy as np
                            pct = np.sum(np.array(pes) <= pes[-1]) / len(pes) * 100
                            # np.float64 无法被 psycopg2 绑定（会插成字面量报 schema np 不存在）→ 转 float
                            val = float(100 - pct)
                    except: pass
                else:  # amount
                    val = float(r[5]) if len(r) > 5 and r[5] else 0
            except:
                db.rollback()
                val = 0

            chg = 0
            prev = db.execute(text("SELECT close FROM daily_quote WHERE stock_code=:c AND trade_date<:d ORDER BY trade_date DESC LIMIT 1"), {"c": code, "d": trade_date}).fetchone()
            if prev and prev[0]: chg = (price - float(prev[0])) / float(prev[0]) * 100

            # 申万行业：r[3]=一级(r[4]=二级)；无行业归"U 其他"
            ind_l1 = str(r[3]) if len(r) > 3 and r[3] else ''
            ind_l2 = str(r[4]) if len(r) > 4 and r[4] else ''
            if ind_l1:
                l1 = ind_l1
                l2 = ind_l2 or (ind_l1 + '—')   # 无二级时一级名兜底
                l2_name = ind_l2 or ind_l1
            else:
                l1 = 'U'; l2 = 'U00'; l2_name = '其他'

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
    # 一次 GROUP BY 同时拿到上交所和深交所行数（剔除 15/5 开头 ETF 行，ETF 单列统计）
    dq_counts = {}
    try:
        rows = db.execute(text("SELECT exchange, COUNT(*) FROM daily_quote WHERE NOT (LEFT(stock_code,2)='15' OR LEFT(stock_code,1)='5') GROUP BY exchange")).fetchall()
        for r in rows: dq_counts[r[0]] = r[1]
    except: db.rollback()

    tables = [
        ('上交所A股', lambda: (dq_counts.get('SSE', 0), q("SELECT COUNT(*) FROM stock_master WHERE exchange='SSE' AND status='N' AND stock_type='stock'")),
         ("SELECT MIN(trade_date)::text FROM daily_quote WHERE exchange='SSE' AND NOT (LEFT(stock_code,2)='15' OR LEFT(stock_code,1)='5')", "SELECT MAX(trade_date)::text FROM daily_quote WHERE exchange='SSE' AND NOT (LEFT(stock_code,2)='15' OR LEFT(stock_code,1)='5')")),
        ('深交所A股', lambda: (dq_counts.get('SZSE', 0), q("SELECT COUNT(*) FROM stock_master WHERE exchange='SZSE' AND status='N' AND stock_type='stock'")),
         ("SELECT MIN(trade_date)::text FROM daily_quote WHERE exchange='SZSE' AND NOT (LEFT(stock_code,2)='15' OR LEFT(stock_code,1)='5')", "SELECT MAX(trade_date)::text FROM daily_quote WHERE exchange='SZSE' AND NOT (LEFT(stock_code,2)='15' OR LEFT(stock_code,1)='5')")),
        ('指数日K线', lambda: (q("SELECT COALESCE((SELECT reltuples::bigint FROM pg_class WHERE relname='index_daily_quote'),0)"), q("SELECT COUNT(*) FROM stock_master WHERE stock_type='index'")),
         ("SELECT MIN(trade_date)::text FROM index_daily_quote", "SELECT MAX(trade_date)::text FROM index_daily_quote")),
        ('ETF日K线', None,
         ("SELECT MIN(trade_date)::text FROM daily_quote WHERE LEFT(stock_code,2)='15' OR LEFT(stock_code,1)='5'",
          "SELECT MAX(trade_date)::text FROM daily_quote WHERE LEFT(stock_code,2)='15' OR LEFT(stock_code,1)='5'")),  # 下面单独处理 rows
        ('基本面', lambda: (q("SELECT COUNT(*) FROM stock_fundamentals_history"), q("SELECT COUNT(DISTINCT stock_code) FROM stock_fundamentals_history")),
         ("SELECT MIN(report_date)::text FROM stock_fundamentals_history", "SELECT MAX(report_date)::text FROM stock_fundamentals_history")),  # 历史口径（快照表无日期跨度，updated_at 最小值只是停更快照残留）
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
    sig_buy = q("SELECT COUNT(*) FROM signal_history WHERE direction='buy'") or 0
    sig_sell = q("SELECT COUNT(*) FROM signal_history WHERE direction='sell'") or 0
    for s in stats:
        if s['label'] == '交易信号': s['detail'] = f'买{sig_buy} 卖{sig_sell}'
        if s['label'] in ('上交所A股', '深交所A股'): s['detail'] = '不含ETF'

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


DAG_STRUCTURE = []  # 已废弃，节点结构由 dag_flows 表动态管理

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
        # v3.2 单路径：tushare 按交易日全市场拉取（覆盖退市股历史，停牌自然缺失）
        from crawler.adapters import get_data_source_manager
        from app.db.connection import get_sync_db
        from crawler.writers import batch_upsert_kline
        manager = get_data_source_manager()
        source = manager.get_source()
        db = get_sync_db()
        rows = source.fetch_stock_kline([], td, td)
        saved = batch_upsert_kline(db, rows)
        db.close()
        return {'rows': saved, '_source': source.name}
    try:
        r = _with_hb(log_id, rid, _run)
        rows = r.get('rows', 0)
        fatal = r.get('fatal', '')
        src = r.get('_source', '?')
        if fatal:
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
        # v3.2 单路径：tushare index_daily 按交易日全市场
        from crawler.adapters import get_data_source_manager
        from app.db.connection import get_sync_db
        from crawler.writers import batch_upsert_index_kline
        manager = get_data_source_manager()
        source = manager.get_source()
        db = get_sync_db()
        rows = source.fetch_index_kline([], td, td)
        saved = batch_upsert_index_kline(db, rows)
        db.close()
        return {'rows': saved, '_source': source.name}
    try:
        r = _with_hb(log_id, rid, _run)
        rows = r.get('rows', 0) if isinstance(r, dict) else r
        src = r.get('_source', '?') if isinstance(r, dict) else '?'
        if rows == 0:
            write_node_log(log_id=log_id, status='success', detail='无数据(跳过)')
            return 0
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
        # v3.2 单路径：tushare fund_daily 按交易日全市场；
        # ETF 后复权由 baostock 补充器补齐（tushare fund_adj 需高积分）
        from crawler.adapters import get_data_source_manager
        from app.db.connection import get_sync_db
        from crawler.writers import batch_upsert_kline
        manager = get_data_source_manager()
        source = manager.get_source()
        db = get_sync_db()
        rows = source.fetch_etf_kline([], td, td)
        # ETF 后复权由 baostock 补充器提供（tushare fund_adj 需高积分）。
        # DAG 节点不做同步补充（baostock 串行逐只，全市场需 15+ 分钟，会阻塞流程）；
        # 缺口在补数场景由用户主动触发补充（状态页补数 ETF）。
        if rows:
            logger.info("[etf] close_hfq=close（ETF 复权请在补数场景触发 baostock 补充）")
        saved = batch_upsert_kline(db, rows)
        db.close()
        return {'rows': saved, '_source': source.name}
    try:
        r = _with_hb(log_id, rid, _run)
        rows = r.get('rows', 0)
        src = r.get('_source', '?')
        if rows == 0:
            write_node_log(log_id=log_id, status='success', detail='无数据(跳过)')
            return 0
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
    write_node_log(log_id=log_id, status='running', detail='采集中')
    def _run():
        from crawler.adapters import get_data_source_manager
        from app.db.connection import get_sync_db
        from crawler.writers import batch_upsert_fundamentals, append_fundamentals_history
        manager = get_data_source_manager()
        source = manager.get_source()
        db = get_sync_db()
        rows = source.fetch_fundamentals([])
        saved = batch_upsert_fundamentals(db, rows)
        # 日度 PE/PB 追加到 history（PE 历史走势图数据源）
        hist = append_fundamentals_history(db, rows)
        # baostock 补 ROE/营收/净利仅发生在补数场景（串行逐只较慢，DAG 节点不做同步补充）
        # DAG 每日流程：tushare 主字段入库；ROE 等缺口由状态页补数触发补充
        if rows:
            logger.info(f"[fund] DAG 节点写 tushare 主字段+日度PE({hist}行)，ROE 等由补数场景补充")
        db.close()
        return {'rows': saved, '_source': source.name}
    try:
        r = _with_hb(log_id, rid, _run)
        rows = r.get('rows', 0) if isinstance(r, dict) else r
        src = r.get('_source', '?') if isinstance(r, dict) else '?'
        if rows == 0:
            write_node_log(log_id=log_id, status='success', detail='无数据(跳过)')
            return 0
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

# indicator_incr / indicator_full 已下线（v2.6，已删除）。
# 所有指标计算统一走 /api/features/{id}/compute-range → app/api/features.py
# 底层使用 strategy/indicators.py 向量化函数 + feature_values 长格式表。

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
    from sqlalchemy import text
    import pandas as pd

    if not feature_names:
        return pd.DataFrame()

    # 容错：空 end_date 解释为今天（前端/接口层可能传空）
    if not end_date:
        from datetime import date as _date
        end_date = str(_date.today())

    # 列名白名单（防注入 + 非法标识符），特征名来自 features 表
    safe_names = [fn for fn in feature_names if re.fullmatch(r'[A-Za-z0-9_]+', fn)]
    if not safe_names:
        return pd.DataFrame()

    # SQL 端 PIVOT（FILTER 聚合）：长格式逐行拉取在 23 特征 × 多年时高达数千万行，
    # 仅 fetchall + pandas 副本即可吃满数十 GB 内存；FILTER 聚合由 PostgreSQL 完成
    # 长转宽，只返回 ~360 万行 × (2+N) 列的结果集（约 1-2GB），内存降低一个数量级
    selects = ",\n".join(
        f"MAX(value) FILTER (WHERE feature_name = '{fn}') AS \"{fn}\"" for fn in safe_names)
    pivoted = f"""
        SELECT stock_code, trade_date, {selects}
        FROM feature_values
        WHERE feature_name = ANY(:names) AND trade_date BETWEEN :sd AND :ed
        GROUP BY stock_code, trade_date
    """
    table = 'daily_quote'
    code_col = 'stock_code'
    ent_filter = "AND exchange IN ('SSE','SZSE')"
    if entity == 'index':
        table = 'index_daily_quote'
        code_col = 'index_code'
        ent_filter = ''
    elif entity == 'etf':
        ent_filter = "AND (LEFT(stock_code,2)='15' OR LEFT(stock_code,1)='5')"
    quotes = f"""
        SELECT {code_col} as stock_code, trade_date, close_hfq as close, volume
        FROM {table}
        WHERE trade_date BETWEEN :sd AND :ed {ent_filter}
    """
    sql = f"""
        SELECT f.stock_code, to_char(f.trade_date, 'YYYY-MM-DD') AS trade_date,
               {", ".join(f'f."{fn}"' for fn in safe_names)},
               q.close, q.volume
        FROM ({pivoted}) f
        LEFT JOIN ({quotes}) q
          ON q.stock_code = f.stock_code AND q.trade_date = f.trade_date
    """
    df = pd.DataFrame(db.execute(text(sql), {
        "names": safe_names, "sd": start_date, "ed": end_date,
    }).fetchall(), columns=['stock_code', 'trade_date'] + safe_names + ['close', 'volume'])

    # 补齐缺失的特征列
    for fn in feature_names:
        if fn not in df.columns:
            df[fn] = None

    # 按日期排序（与旧实现一致）
    return df.sort_values(['trade_date', 'stock_code']).reset_index(drop=True)


# ── 归因分析引擎（v2.7 — 基准锚定法）──

def _simple_backtest(df, pred=None, val_start='', val_end='', hold_days=10,
                     stop_loss=0.08, take_profit=0.15, trailing=0.0, max_pos=5,
                     seed=None, ideal=False, compound=True):
    """统一回测引擎 v2（供 run_attribution 和 strategy-scan 共用）。

    选股依据三选一（互斥）：
      - pred: 与 df 行索引对齐的预测 Series（真模型打分由外部传入，引擎不做特征均值）
      - ideal=True: 完美预知，每日买未来 hold_days 实际涨幅最高且为正者（理论上限）
      - seed: 固定种子随机选股（随机基线，可复现）
    执行约束（A 股）：涨停(≥9.8%/19.8%)不买、跌停不卖、T+1（卖出检查只针对隔夜仓，
    当日买入的仓位次日才进入检查）；trailing>0 时按持仓期峰值回撤触发卖出；
    止损/止盈按触发价成交（忽略跳空）。
    成本：佣金 0.00025（双边）、印花税 0.001（卖出）、滑点 0.001（买卖各半摊入成交价）。
    compound=False 时按初始资金定额仓位（不复利）——ideal 基线用它，避免完美预知
    逐日复利爆炸出天文数字污染 Brinson 分解。
    """
    import numpy as np
    import pandas as pd
    empty = {'sharpe': 0, 'max_dd': 0, 'win_rate': 0, 'total_return': 0,
             'total_trades': 0, 'total_cost': 0}
    # 稳定排序：groupby shift（理想基线前瞻价 / 涨跌停前收盘）依赖组内日期升序
    df = df.sort_values(['trade_date', 'stock_code'], kind='stable').copy()
    df['close'] = df['close'].astype(float)  # SQL NUMERIC 为 Decimal，除法会抛 DivisionUndefined
    val_mask = (df['trade_date'] >= val_start) & (df['trade_date'] <= val_end)
    if not val_mask.any():
        return empty
    vdf = df[val_mask].copy()
    vdf['trade_date'] = pd.to_datetime(vdf['trade_date'])  # 持仓期计算需要日期相减

    # ── 选股得分 ──
    if ideal:
        # 未来 hold_days 个交易行实际涨幅：在全表上 groupby shift，验证区间尾部也能取到窗外价格
        gfull = df.groupby('stock_code')['close']
        fwd = gfull.shift(-hold_days) / df['close'] - 1
        vdf['_score'] = fwd[val_mask]
    elif pred is not None:
        if not isinstance(pred, pd.Series):
            raise ValueError('pred 必须是与 df 行索引对齐的 pd.Series')
        vdf['_score'] = pred.loc[vdf.index].astype(float)
    else:
        rng = np.random.default_rng(seed if seed is not None else 42)
        vdf['_score'] = 0.0

    # ── 涨跌停标记：对前收盘的涨跌幅（close 为后复权价，除权日略有近似）──
    prev_close = vdf.groupby('stock_code')['close'].shift(1)
    pct = vdf['close'] / prev_close - 1
    is_20 = vdf['stock_code'].astype(str).str.startswith(('30', '68'))  # 创业板/科创板 20cm
    lim = pd.Series(np.where(is_20, 0.198, 0.098), index=vdf.index)
    vdf['_limit_up'] = pct >= lim       # 区间首日 pct 为 NaN → False，允许交易
    vdf['_limit_down'] = pct <= -lim

    dates_unique = sorted(vdf['trade_date'].unique())
    equity = 1_000_000; cash = 1_000_000
    holdings = []; equity_curve = [equity]
    trade_count = win_count = 0
    cost_total = 0.0
    comm = 0.00025; st_tax = 0.001; slip = 0.001

    for d in dates_unique:
        day = vdf[vdf['trade_date'] == d]
        if day.empty: continue

        # ── 卖出检查（先卖后买；只检查隔夜仓 → T+1 天然成立）──
        surviving = []
        for h in holdings:
            hday = day[day['stock_code'] == h['code']]
            if hday.empty: surviving.append(h); continue
            cur_p = float(hday['close'].iloc[0])
            h['peak'] = max(h.get('peak', h['buy_price']), cur_p)
            sell_p = None
            if cur_p <= h['buy_price'] * (1 - stop_loss):
                sell_p = h['buy_price'] * (1 - stop_loss)       # 止损按触发价成交
            elif cur_p >= h['buy_price'] * (1 + take_profit):
                sell_p = h['buy_price'] * (1 + take_profit)     # 止盈按触发价成交
            elif trailing > 0 and cur_p <= h['peak'] * (1 - trailing):
                sell_p = cur_p                                  # 峰值回撤卖出
            elif (d - h['buy_date']).days >= hold_days:
                sell_p = cur_p
            if sell_p is None or bool(hday['_limit_down'].iloc[0]):
                surviving.append(h); continue                   # 跌停不可卖
            gross = h['shares'] * sell_p
            cost = gross * (comm + st_tax) + max(gross * slip, 0)
            cost_total += cost
            cash += max(gross - cost, 0); trade_count += 1
            if sell_p > h['buy_price']: win_count += 1
        holdings = surviving

        # ── 买入：候选剔除已持/涨停，按得分取 top slots ──
        held = {h['code'] for h in holdings}
        candidates = day[~day['stock_code'].isin(held) & (day['close'] > 0) & (~day['_limit_up'])]
        slots = max_pos - len(holdings)
        picks = []
        if slots > 0 and not candidates.empty:
            if pred is None and not ideal:
                n = min(slots, len(candidates))
                picks = list(rng.choice(candidates.index.to_numpy(), size=n, replace=False))
            else:
                scored = candidates[candidates['_score'].notna() & (candidates['_score'] > 0)]
                if not scored.empty:
                    picks = list(scored['_score'].sort_values(ascending=False).index[:slots])
        for ci in picks:
            r = candidates.loc[ci]
            price = float(r['close']) * (1 + slip / 2)
            size_base = equity if compound else 1_000_000  # 非复利模式按初始资金定额仓位
            shares = int((size_base / max_pos) // price // 100) * 100
            if shares < 100: continue
            gross2 = shares * price
            if gross2 + gross2 * comm > cash: continue
            cash -= gross2 + gross2 * comm
            cost_total += gross2 * comm
            holdings.append({'code': r['stock_code'], 'buy_price': price,
                             'buy_date': d, 'shares': shares, 'peak': price})

        # ── 收盘估值（无候选/无空位也要记账，保证净值曲线逐日连续）──
        pos_val = 0.0
        for h in holdings:
            hday = day[day['stock_code'] == h['code']]
            if not hday.empty:
                pos_val += h['shares'] * float(hday['close'].iloc[0])
        equity = cash + pos_val
        equity_curve.append(equity)

    eq = np.array(equity_curve); rets = eq[1:]/eq[:-1] - 1 if len(eq) > 1 else np.array([0])
    sharpe = float(np.mean(rets)/np.std(rets)*np.sqrt(252)) if np.std(rets) > 0 else 0
    total_ret = equity/1_000_000 - 1
    peak = np.maximum.accumulate(eq); dd = np.min((eq-peak)/peak) if len(eq) > 1 else 0
    return {'sharpe': round(sharpe,4), 'max_dd': round(float(dd),4),
            'win_rate': round(win_count/max(trade_count,1),4),
            'total_return': round(float(total_ret),4), 'total_trades': trade_count,
            'total_cost': round(cost_total, 2)}


def predict_for_version(db, version: str, df, val_start: str, val_end: str, horizon: int = 10):
    """加载 xgb_{horizon}d 模型并对验证区间逐行预测（归因/扫描共用的唯一预测入口）。

    模型含训练期派生特征（bias_5_20 / idx_ret_20d）而宽表没有时现场派生对齐；
    idx_ret_20d 查询起点向前扩 90 自然日，保证 pct_change(20) 有预热。
    Returns: (pred Series|None, err|None)，pred 与 df 验证区间行索引对齐。
    """
    import pickle as _pkl, os as _os
    import pandas as _pd
    from datetime import date as _d, timedelta as _td
    from sqlalchemy import text as _text

    model_path = f"data/models/{version}/xgb_{horizon}d.pkl"
    if not _os.path.exists(model_path):
        return None, f'模型文件不存在: {model_path}'
    val_mask = (df['trade_date'] >= val_start) & (df['trade_date'] <= val_end)
    if not val_mask.any():
        return None, '验证集无数据'
    val_df = df[val_mask].copy()
    pred_index = val_df.index
    try:
        with open(model_path, 'rb') as f:
            model = _pkl.load(f)
        mf = list(getattr(model, 'feature_names_in_', []))
        if not mf:
            return None, '模型缺少 feature_names_in_，请重新训练'
        # 特征对齐：模型含训练时派生的特征，宽表没有则现场派生（与 dag_task_model_train 一致）
        if 'bias_5_20' in mf and 'bias_5_20' not in val_df.columns \
                and 'ma_5' in val_df.columns and 'ma_20' in val_df.columns:
            # SQL NUMERIC 为 Decimal，0/0 会抛 DivisionUndefined，先转 float
            val_df['bias_5_20'] = val_df['ma_5'].astype(float) / val_df['ma_20'].astype(float) - 1
        if 'idx_ret_20d' in mf and 'idx_ret_20d' not in val_df.columns:
            _s = (_d.fromisoformat(str(val_start)[:10]) - _td(days=90)).isoformat()
            idx_rows = db.execute(_text(
                "SELECT trade_date, close FROM index_daily_quote WHERE index_code='000300' "
                "AND trade_date BETWEEN :ds AND :ed ORDER BY trade_date"
            ), {"ds": _s, "ed": val_end}).fetchall()
            if idx_rows:
                idx_df = _pd.DataFrame(idx_rows, columns=['trade_date', 'idx_close'])
                idx_df['trade_date'] = idx_df['trade_date'].astype(str)
                idx_df['idx_close'] = idx_df['idx_close'].astype(float)
                idx_df['idx_ret_20d'] = idx_df['idx_close'].pct_change(20)
                # 左连接保持行序；索引被 merge 重置，预测后按 pred_index 回填
                val_df = val_df.reset_index(drop=True).merge(
                    idx_df[['trade_date', 'idx_ret_20d']], on='trade_date', how='left')
                val_df['idx_ret_20d'] = val_df['idx_ret_20d'].fillna(0).astype(float)
        cols = [c for c in mf if c in val_df.columns]
        if len(cols) != len(mf):
            missing = [c for c in mf if c not in val_df.columns]
            return None, f'宽表缺少模型特征: {missing[:5]}'
        y = model.predict(val_df[cols].fillna(0).values)
        return _pd.Series(y, index=pred_index), None
    except Exception:
        import traceback as _tb
        return None, f'预测失败: {_tb.format_exc()[-300:]}'


def run_attribution(db, version: str, df, val_start: str, val_end: str,
                    initial_cash: float = 1_000_000, max_pos: int = 5,
                    stop_loss: float = 0.08, take_profit: float = 0.15, hold_days: int = 10):
    """三基线归因分析（评估器 v2）。

    ideal:  完美预知基线（每日买未来 hold_days 实际涨幅最高者，无止损止盈）— 策略上限
    random: 固定种子随机基线（与 real 同一止损止盈参数）— 无信息下限
    real:   真模型预测基线（xgb_{horizon}d，horizon 由 hold_days 映射）
    合理形态应为 ideal > real ≈/> random；real 长期低于 random 说明模型负贡献。

    Returns:
        {
            'ideal'/'random'/'real': {sharpe, total_return, max_dd, win_rate},
            'benchmark_return': 沪深300 同期收益,
            'matrix': 'execution_loss'|'beta_amplifier'|'dual_driver'|'double_misjudge',
            'brinson': {model_contribution, strategy_contribution, interaction}
        }
    """
    horizon = 5 if hold_days <= 5 else (20 if hold_days > 10 else 10)
    pred, err = predict_for_version(db, version, df, val_start, val_end, horizon)
    if pred is None:
        logger.warning(f"[attribution] {version} 预测不可用: {err}")
        zero = {'sharpe': 0, 'total_return': 0, 'win_rate': 0, 'max_dd': 0}
        return {
            'ideal': dict(zero), 'random': dict(zero), 'real': dict(zero),
            'benchmark_return': 0, 'matrix': 'double_misjudge',
            'brinson': {'model_contribution': 0, 'strategy_contribution': 0, 'interaction': 0},
            'error': err,
        }

    # 三基线回测（同一引擎同一约束，仅选股依据不同）
    real = _simple_backtest(df, pred, val_start, val_end, hold_days,
                            stop_loss, take_profit, max_pos=max_pos)
    ideal = _simple_backtest(df, None, val_start, val_end, hold_days,
                             0.99, 99.0, max_pos=max_pos, ideal=True, compound=False)
    random = _simple_backtest(df, None, val_start, val_end, hold_days,
                              stop_loss, take_profit, max_pos=max_pos, seed=42)

    # 4. 基准收益（沪深300 同期）
    benchmark_return = 0
    try:
        from sqlalchemy import text as _text
        bm = db.execute(_text(
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



def _get_preference_thresholds(db) -> dict:
    """读取全局偏好设置，返回信号生成阈值字典。"""
    from sqlalchemy import text
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
    import numpy as np
    import pandas as pd

    td = trade_date or str(_date.today())
    rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('model_signal')
    write_node_log(log_id=log_id, status='running', detail='生成模型信号…')

    try:
        db = get_sync_db()
        # 获取 ACTIVE 模型 + 全局偏好（多实体并存时取最近激活者，避免无 ORDER BY 的不确定行）
        ver = db.execute(text(
            "SELECT version FROM model_versions WHERE status='ACTIVE' "
            "ORDER BY activated_at DESC NULLS LAST, created_at DESC LIMIT 1"
        )).scalar()
        if not ver:
            write_node_log(log_id=log_id, status='success', rows=0, detail='无 ACTIVE 模型，跳过')
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
        ml_threshold = 0.5
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
                # A3: 有 ≥2 个周期模型即启用 ML 预测（原 ==3 全齐才启用过严）
                use_predict = len(xgb_models) >= 2
                cfg_obj = db.execute(text("SELECT config FROM model_versions WHERE version=:v"), {"v": ver}).scalar()
                cfg_parsed = _json.loads(cfg_obj) if isinstance(cfg_obj, str) else (cfg_obj or {})
                sig_cfg = cfg_parsed.get('signal', {})
                # 买入阈值模式：quantile=当日预测分布分位数（默认，自适应模型能力）；absolute=绝对预测收益率
                ml_mode = sig_cfg.get('threshold_mode', 'quantile')
                _mt = sig_cfg.get('ml_confidence_threshold')
                ml_threshold = _mt if _mt is not None else 0.5  # 不能 or 0.5：0.0 是合法配置（全量买入）
                buy_top_pct = sig_cfg.get('buy_top_pct', 0.05)  # quantile 模式：预测 top 5% 触发买入
                r2_avg = float(np.mean([bp[l].get('r2', 0) for l in xgb_models])) if xgb_models else 0
                mode_desc = (f"预测top{buy_top_pct:.0%}" if ml_mode == 'quantile' else f"绝对阈值{ml_threshold:.2%}")
                write_node_log(log_id=log_id, status='running',
                               detail=f"预测模式 ({len(xgb_models)}模型 R²avg={r2_avg:.3f}, {mode_desc})" if use_predict
                               else f"规则模式 (偏好:{pref_mode})")
        except Exception as e:
            logger.warning(f"[model_signal] 预测模型加载失败: {e}，回退规则模式")

        if not use_predict:
            write_node_log(log_id=log_id, status='running', detail=f"规则模式 (偏好:{pref_mode}, buy>={t['buy_score_min']})")

        # 读取目标交易日特征（v2.6: feature_values 宽表替代旧 indicator JOIN）
        today_str = td
        model_cfg_json = db.execute(text(
            "SELECT config FROM model_versions WHERE version=:v"
        ), {"v": ver}).scalar()
        model_cfg_obj = _json.loads(model_cfg_json) if isinstance(model_cfg_json, str) else (model_cfg_json or {})
        feature_names = model_cfg_obj.get('feature_names', [])
        if not feature_names:
            write_node_log(log_id=log_id, status='failed', detail='未配置 feature_names')
            db.close(); return 0

        df_today = build_feature_wide_table(db, feature_names, today_str, today_str, 'stock')
        if df_today.empty:
            write_node_log(log_id=log_id, status='success', rows=0, detail='今日无特征数据')
            db.close(); return 0

        # 派生特征与训练端保持一致（bias_5_20/vol_ratio_3d/idx_ret_20d）：
        # 训练模型 feature_names_in_ 含这些列，信号侧缺列会导致 ML 预测逐股回退规则模式
        if 'ma_5' in df_today.columns and 'ma_20' in df_today.columns:
            df_today['bias_5_20'] = df_today['ma_5'] / df_today['ma_20'] - 1
        if 'vol_ratio' in df_today.columns:
            df_today['vol_ratio_3d'] = df_today.groupby('stock_code')['vol_ratio'].transform(lambda x: x.rolling(3).mean())
        idx_rows = db.execute(text(
            "SELECT close FROM index_daily_quote WHERE index_code='000300' AND trade_date <= :d ORDER BY trade_date DESC LIMIT 21"
        ), {"d": today_str}).fetchall()
        if len(idx_rows) >= 21:
            closes = [float(x[0]) for x in reversed(idx_rows)]
            df_today['idx_ret_20d'] = closes[-1] / closes[0] - 1

        # 准备 stock_name
        codes = df_today['stock_code'].unique().tolist()
        names_map = {}
        if codes:
            nr = db.execute(text("SELECT stock_code, stock_name FROM stock_master WHERE stock_code = ANY(:c)"),
                           {"c": codes}).fetchall()
            names_map = {r[0]: r[1] for r in nr}

        rows = []
        # 模型特征 = config 特征 + 派生特征（bias_5_20/vol_ratio_3d/idx_ret_20d）
        extra_cols = [c for c in ('bias_5_20', 'vol_ratio_3d', 'idx_ret_20d') if c in df_today.columns]
        for _, r in df_today.iterrows():
            d = {'stock_code': r['stock_code'], 'stock_name': names_map.get(r['stock_code'], '')}
            for fn in feature_names + extra_cols:
                # 缺失/NaN 特征传 NaN（XGBoost 原生缺失分支），不要用 0 伪装观测值
                d[fn] = float(r[fn]) if fn in r and pd.notna(r[fn]) else float('nan')
            d['close'] = float(r.get('close', 0)) if pd.notna(r.get('close', 0)) else 0
            rows.append(type('Row', (), d)())

        # 先删旧信号再插新
        db.execute(text("DELETE FROM signal_history WHERE signal_date=:d AND strategy_name='model_signal' AND model_version=:v"), {"d": td, "v": ver})

        # 评分信号生成（ML 预测优先，规则评分兜底）
        buy_count = 0
        ml_fallback = 0

        def _rule_score(row):
            """规则评分（ML 回退 / 无模型时使用）。"""
            score = 0
            reasons = []
            pct_b = getattr(row, 'boll_pct_b', 0) or 0
            rsi_val = getattr(row, 'rsi_14', 50) or 50
            dif = getattr(row, 'macd_dif', 0) or 0
            hist = getattr(row, 'macd_hist', 0) or 0
            if pct_b < t['boll_lower']: score += 1; reasons.append('BOLL超卖')
            if rsi_val < t['rsi_oversold']: score += 1; reasons.append('RSI超卖')
            if dif > hist: score += 1; reasons.append('MACD正柱')
            return score, reasons

        def _insert_buy(row, strength, reason):
            db.execute(text("""
                INSERT INTO signal_history (signal_date, stock_code, stock_name, direction, strength, price, strategy_name, reason, combined_signal, model_version, params_snapshot, preference)
                VALUES (:d,:c,:n,'buy',:s,:p,'model_signal',:r,true,:v,:sn,:pref)
            """), {"d": td, "c": row.stock_code, "n": row.stock_name, "s": strength, "p": row.close,
                   "r": reason, "v": ver, "sn": model_snapshot, "pref": pref_mode})

        if use_predict and xgb_models:
            # 第一遍：全量预测，得到当日预测分布（阈值模式需要完整分布才能取分位数）
            preds_map = {}      # stock_code -> 预测收益率均值
            fallback_rows = []  # 预测失败（特征缺失/模型异常）→ 回退规则评分
            for r in rows:
                if not r.close or r.close == 0:
                    continue
                try:
                    preds = []
                    for label in sorted(xgb_models.keys()):
                        mdl = xgb_models[label]
                        mf = list(getattr(mdl, 'feature_names_in_', []))
                        if not mf:
                            mf = [f for f in feature_names if hasattr(r, f)]
                        vec = []
                        for fn in mf:
                            v = getattr(r, fn, None)
                            if v is None:
                                raise ValueError(f'缺少特征 {fn}')
                            vec.append(float(v))
                        preds.append(float(mdl.predict(np.array([vec]))[0]))
                    preds_map[r.stock_code] = float(np.mean(preds))
                except Exception:
                    ml_fallback += 1
                    fallback_rows.append(r)

            # 阈值：quantile=当日预测 top N%（精确截断，模型无区分度时比例依然固定）；absolute=绝对预测收益率
            if preds_map:
                codes = np.array(list(preds_map.keys()))
                vals = np.array(list(preds_map.values()))
                if ml_mode == 'quantile':
                    # top-K 截断而非分位值比较：预测值离散/并列时 `>= thr` 会放大买入比例（实测 5%→34%）
                    k = max(int(len(vals) * min(max(buy_top_pct, 0.001), 1.0)), 1)
                    order = np.argsort(vals)[::-1][:k]
                    buy_codes = set(codes[order])
                    thr = float(vals[order[-1]])
                else:
                    top_pct = None
                    buy_codes = set(codes[vals >= float(ml_threshold)])
                    thr = float(ml_threshold)
                logger.info(f"[model_signal] 买入阈值({ml_mode})={thr:.4f} 预测分布: p50={np.median(vals):.4f} max={vals.max():.4f}")

                for r in rows:
                    if r.stock_code not in buy_codes:
                        continue
                    p = preds_map[r.stock_code]
                    # 当日分布分位（并列均分：严格小于 + 一半并列），0-1
                    rank = float((vals < p).mean() + 0.5 * (vals == p).mean())
                    # 强度：分位映射 0-3（与规则评分同量纲），SMALLINT 取整
                    strength = min(max(round(rank * 3), 0), 3)
                    reason = (f'ML预测{p*100:.2f}%(top{buy_top_pct*100:.0f}%档)'
                              if ml_mode == 'quantile' else f'ML预测{p*100:.2f}%(≥阈值{thr*100:.2f}%)')
                    _insert_buy(r, strength, reason)
                    buy_count += 1

            # 预测失败回退规则评分
            for r in fallback_rows:
                score, reasons = _rule_score(r)
                if score >= t['buy_score_min']:
                    _insert_buy(r, score, ';'.join(reasons) or '规则评分')
                    buy_count += 1
        else:
            # 规则评分
            for r in rows:
                if not r.close or r.close == 0:
                    continue
                score, reasons = _rule_score(r)
                if score >= t['buy_score_min']:
                    _insert_buy(r, score, ';'.join(reasons))
                    buy_count += 1

        db.commit()
        if ml_fallback:
            logger.warning(f"[model_signal] {ml_fallback}/{len(rows)} 只股票 ML 预测失败回退规则模式")
        mode_tag = 'ML' if (use_predict and xgb_models) else '规则'
        write_node_log(log_id=log_id, status='success', rows=buy_count,
                       detail=f'{mode_tag}模式 {buy_count} 买入 {len(rows)} 扫描')
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
        # 多实体并存时取最近激活的 ACTIVE（与 model_signal 同口径）
        ver = db.execute(text(
            "SELECT version FROM model_versions WHERE status='ACTIVE' "
            "ORDER BY activated_at DESC NULLS LAST, created_at DESC LIMIT 1"
        )).scalar()
        if not ver:
            write_node_log(log_id=log_id, status='success', rows=0, detail='无 ACTIVE 模型，跳过')
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
                       detail=f'{health}: 胜率{win_rate:.0%} {total}信号 {closed}了结')
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
        write_node_log(log_id=log_id, status='success', rows=r, detail=f'完成，{r}项')
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
        fund  = db.execute(text("SELECT COUNT(*) FROM stock_fundamentals sf JOIN stock_master sm ON sm.stock_code=sf.stock_code AND sm.stock_type='stock' AND sm.status='N'")).scalar() or 0  # 快照：活跃A股有基本面数
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


def dag_task_entity_stats(trade_date=None, **kw):
    """每日增量更新 entity_stats 表 — 各实体类型的 total_cells。

    首次运行：JOIN 计算基线（~25s per entity type）。
    后续运行：增量更新 — 新增交易日 × active_count + 新股交易天数。
    """
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    from datetime import date as _dd, timedelta
    td = trade_date or str(_dd.today())
    rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('entity_stats')
    write_node_log(log_id=log_id, status='running', detail='更新实体统计')
    try:
        db = get_sync_db()

        # 检查是否是交易日
        is_td = db.execute(text(
            "SELECT is_trade_day FROM trade_calendar WHERE cal_date=:d AND exchange='SSE'"
        ), {"d": td}).scalar()
        if not is_td:
            db.close()
            write_node_log(log_id=log_id, status='success', rows=0, detail='非交易日，跳过')
            return 0

        entity_configs = [
            ("stock",  "AND sm.stock_type='stock' AND sm.exchange IN ('SSE','SZSE')"),
            ("index",  "AND sm.stock_type='index'"),
            ("etf",    "AND sm.stock_type='etf'"),
            ("global", ""),
        ]

        updated = 0
        for etype, ent_filter in entity_configs:
            # 读取当前状态
            stat = db.execute(text(
                "SELECT total_cells, active_count, base_date FROM entity_stats WHERE entity_type=:et"
            ), {"et": etype}).fetchone()
            if not stat:
                continue
            total_cells, active_count, base_date = stat[0], stat[1], stat[2]

            if etype == "global":
                # global total_cells = 自 2000-01-01 以来的交易日总数
                new_total = db.execute(text(
                    "SELECT COUNT(*) FROM trade_calendar WHERE cal_date >= '2000-01-01' AND cal_date <= :d AND is_trade_day = true"
                ), {"d": td}).scalar() or 0
                new_active = 1
            else:
                # 活跃实体数
                new_active = db.execute(text(f"""
                    SELECT COUNT(*) FROM stock_master sm
                    WHERE sm.status = 'N' AND sm.ipo_date <= :d {ent_filter}
                """), {"d": td}).scalar() or 0

                if total_cells == 0:
                    # 首次运行：JOIN 计算基线（SSE 日历为交易日基准，避免每日期 3 行×3）
                    new_total = db.execute(text(f"""
                        SELECT COUNT(*)
                        FROM stock_master sm
                        JOIN trade_calendar tc ON tc.cal_date BETWEEN COALESCE(sm.ipo_date, '2000-01-01') AND :d
                        WHERE sm.status = 'N' {ent_filter}
                          AND tc.is_trade_day = true
                          AND tc.exchange = 'SSE'
                    """), {"d": td}).scalar() or 0
                else:
                    # 增量：新增的交易日 × active_count（近似，忽略个股粒度差异）
                    new_days = db.execute(text("""
                        SELECT COUNT(*) FROM trade_calendar
                        WHERE cal_date > :bd AND cal_date <= :d AND is_trade_day = true AND exchange = 'SSE'
                    """), {"bd": base_date, "d": td}).scalar() or 0
                    new_total = total_cells + new_days * active_count

                    # 处理新股上市：base_date 之后上市的新股
                    new_listings = db.execute(text(f"""
                        SELECT sm.stock_code, sm.ipo_date FROM stock_master sm
                        WHERE sm.status = 'N' {ent_filter}
                          AND sm.ipo_date > :bd AND sm.ipo_date <= :d
                    """), {"bd": base_date, "d": td}).fetchall()
                    for nl in new_listings:
                        code, ipo = nl[0], nl[1]
                        td_cnt = db.execute(text("""
                            SELECT COUNT(*) FROM trade_calendar
                            WHERE cal_date BETWEEN :ipo AND :d AND is_trade_day = true AND exchange = 'SSE'
                        """), {"ipo": ipo, "d": td}).scalar() or 0
                        new_total += td_cnt

            # 更新
            db.execute(text("""
                UPDATE entity_stats SET total_cells=:tc, active_count=:ac,
                    base_date=:bd, updated_at=CURRENT_TIMESTAMP
                WHERE entity_type=:et
            """), {"tc": new_total, "ac": new_active, "bd": td, "et": etype})
            updated += 1

        db.commit()
        db.close()
        write_node_log(log_id=log_id, status='success', rows=updated,
                       detail=f'更新 {updated} 种实体类型')
        return updated
    except Exception as e:
        try: db.rollback(); db.close()
        except: pass
        write_node_log(log_id=log_id, status='failed', detail=str(e))
        raise


def _init_entity_stats(db):
    """启动时初始化 entity_stats 基线（不走 DAG 日志，直接 JOIN 计算）。

    仅当 entity_stats 表为空（total_cells=0）时调用，计算各实体类型的总格子数。
    """
    from sqlalchemy import text
    from datetime import date as _dd
    td = str(_dd.today())

    entity_configs = [
        ("stock",  "AND sm.stock_type='stock' AND sm.exchange IN ('SSE','SZSE')"),
        ("index",  "AND sm.stock_type='index'"),
        ("etf",    "AND sm.stock_type='etf'"),
        ("global", ""),
    ]

    for etype, ent_filter in entity_configs:
        if etype == "global":
            total = db.execute(text(
                "SELECT COUNT(*) FROM trade_calendar WHERE cal_date >= '2000-01-01' AND cal_date <= :d AND is_trade_day = true"
            ), {"d": td}).scalar() or 0
            active = 1
        else:
            total = db.execute(text(f"""
                SELECT COUNT(*)
                FROM stock_master sm
                JOIN trade_calendar tc ON tc.cal_date BETWEEN COALESCE(sm.ipo_date, '2000-01-01') AND :d
                WHERE sm.status = 'N' {ent_filter} AND tc.is_trade_day = true
            """), {"d": td}).scalar() or 0
            active = db.execute(text(f"""
                SELECT COUNT(*) FROM stock_master sm
                WHERE sm.status = 'N' AND sm.ipo_date <= :d {ent_filter}
            """), {"d": td}).scalar() or 0

        db.execute(text("""
            INSERT INTO entity_stats (entity_type, total_cells, active_count, base_date, updated_at)
            VALUES (:et, :tc, :ac, :bd, CURRENT_TIMESTAMP)
            ON CONFLICT (entity_type) DO UPDATE SET
                total_cells=EXCLUDED.total_cells, active_count=EXCLUDED.active_count,
                base_date=EXCLUDED.base_date, updated_at=CURRENT_TIMESTAMP
        """), {"et": etype, "tc": total, "ac": active, "bd": td})


dag = DagExecutor()

def _node_enter(name, status, **ctx):
    td = str(ctx.get('trade_date', '')) or ''
    rid = ctx.get('run_id', '') or ''
    if not td:
        from datetime import date as _dd; td = str(_dd.today())
    return write_node_log(td, name, status, 0, '', rid)
dag.on_node_enter = _node_enter

def _ensure_module_dag_loaded():
    """惰性初始化模块级 dag：从 dag_config 读拓扑（过滤已移除节点）。

    供 dag_trigger / sync_date 等旧接口使用；dag_flows 动态流程用临时 executor，不受影响。
    """
    if dag._nodes:
        return
    from sqlalchemy import text
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        rows = db.execute(text("SELECT node_name, deps FROM dag_config ORDER BY sort_order")).fetchall()
        added = 0
        for name, deps in rows:
            if name not in NODE_FN_MAP:
                continue  # 旧节点（daily_update/model_train 等）已从 NODE_FN_MAP 移除
            valid_deps = [d for d in (deps or []) if d in NODE_FN_MAP]
            dag.add(DagNode(name, deps=valid_deps, fn=NODE_FN_MAP[name]))
            added += 1
        logger.info(f"[pipeline] 模块级 DAG 加载完成: {added} 个节点")
    except Exception as e:
        logger.warning(f"[pipeline] 模块级 DAG 加载失败（dag_trigger 将不可用）: {e}")
    finally:
        db.close()

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

def dag_task_feature_backfill(trade_date=None, **kw):
    """DAG 节点：历史特征补数（3.4）。"""
    from app.db.connection import get_sync_db
    from scripts.feature_compute import compute_all_features
    log_id = (kw.get('_node_log_ids', {}) or {}).get('feature_backfill')

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


def dag_task_feature_compute(trade_date=None, **kw):
    """DAG 节点：计算所有已启用特征值。"""
    from app.db.connection import get_sync_db
    from scripts.feature_compute import compute_all_features
    from datetime import date as dt, timedelta
    log_id = (kw.get('_node_log_ids', {}) or {}).get('feature_compute')

    write_node_log(log_id=log_id, status='running', detail='启动特征计算')
    db = get_sync_db()
    try:
        today = dt.today().strftime("%Y-%m-%d")
        # 增量：仅计算最近 10 天（首次运行可改为全量）；force 时全量重算
        force = (kw.get('_node_force', {}) or {}).get('feature_compute', kw.get('force', False))
        start = "2020-01-01" if force else (dt.today() - timedelta(days=10)).strftime("%Y-%m-%d")
        result = compute_all_features(db, target_entity="stock", start_date=start, end_date=today)
        # 计算完成后回写特征统计（完整度/总格子/缺失格），与 API 触发的计算路径
        # 保持一致；否则 features 表诊断列停留在 0，页面完整度显示不准确
        from app.api.features import _update_feature_stats_after_compute
        from sqlalchemy import text as _sql
        fids = db.execute(_sql(
            "SELECT id FROM features WHERE target_entity='stock' AND status='enabled' ORDER BY id"
        )).fetchall()
        for (fid,) in fids:
            _update_feature_stats_after_compute(fid, force_recompute=True)
        msg = f"完成: {result['features']}个特征, {result['rows']}行, 统计回写{len(fids)}个"
        if result.get("errors"):
            msg += f", {len(result['errors'])}个失败"
        write_node_log(log_id=log_id, status='success', detail=msg, rows=result.get("rows", 0))
        return True
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e))
        raise
    finally:
        db.close()



def dag_task_model_train(trade_date=None, version=None, **kw):
    """Optuna 超参数搜索 + XGBoost 训练 + 逐轮回测 → 存储最优模型。

    version 参数优先（页面训练路径显式传入）；无 version 时回退 DAG 流程路径
    （取最新 DRAFT，无则自动创建 v1.0）。
    """
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
        ver = version or kw.get('version')
        if not ver:
            # DAG 流程路径：取最新 DRAFT；没有则自动创建 v1.0
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
        else:
            # 页面训练路径：校验版本存在且状态允许训练（TRAINING=API 已置位；DRAFT=DAG 直调）
            cur = db.execute(text("SELECT status FROM model_versions WHERE version=:v"), {"v": ver}).fetchone()
            if not cur:
                write_node_log(log_id=log_id, status='failed', detail=f'版本 {ver} 不存在')
                db.close(); return 0
            if cur[0] not in ('TRAINING', 'DRAFT'):
                write_node_log(log_id=log_id, status='failed', detail=f'版本 {ver} 状态为 {cur[0]}，不允许训练')
                db.close(); return 0
        db.execute(text("UPDATE model_versions SET status='TRAINING', trained_at=CURRENT_TIMESTAMP WHERE version=:v"), {"v": ver})
        db.commit()

        # 终止信号：页面训练注入 threading.Event，DAG 流程由 dag.py 注入
        _stop_event = kw.get('_stop_event')

        def _aborted():
            return _stop_event is not None and _stop_event.is_set()

        def _abort(reason):
            """终止收尾：模型回 DRAFT，清空训练产物。"""
            try:
                db.execute(text(
                    "UPDATE model_versions SET status='DRAFT', best_params=NULL, evaluation_report=NULL, "
                    "sharpe=NULL, win_rate=NULL, max_drawdown=NULL, annual_return=NULL WHERE version=:v"
                ), {"v": ver})
                db.commit()
            except Exception:
                try: db.rollback()
                except Exception: pass
            write_node_log(log_id=log_id, status='failed', detail=reason)

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
            idx_df['idx_close'] = idx_df['idx_close'].astype(float)  # SQL NUMERIC 为 Decimal，需转 float
            idx_df['idx_ret_20d'] = idx_df['idx_close'].pct_change(20)
            df = df.merge(idx_df[['trade_date','idx_ret_20d']], on='trade_date', how='left')
            df['idx_ret_20d'] = df['idx_ret_20d'].fillna(0).astype(float)
            FEATURES.append('idx_ret_20d')

        # 只保留有效特征列 + close/volume
        valid_features = [f for f in FEATURES if f in df.columns]
        df = df.dropna(subset=valid_features)

        # ── 3. 标签：forward N 日收益 ──
        # 批量查询样本区间（+60 自然日缓冲）内全部日线，内存中按股票分组计算，
        # 替代原先逐行 N+1 查询（A2 优化）
        logger.info("[train] 计算 Triple Barrier 标签…")
        # 参数：止盈 +10%，止损 -5%，时间屏障 = 周期天数
        TAKE_PROFIT = 0.10
        STOP_LOSS = -0.05
        LABEL_DAYS = [5, 10, 20]  # 与 TARGETS 保持一致（A3: 新增 5d 周期）
        _s_min = str(df['trade_date'].min())[:10]
        _s_max = str(df['trade_date'].max())[:10]
        _buf_end = (_date.fromisoformat(_s_max) + _td(days=60)).isoformat()
        codes = df['stock_code'].unique().tolist()
        fwd_rows = db.execute(text(
            "SELECT stock_code, trade_date, close_hfq FROM daily_quote "
            "WHERE stock_code = ANY(:c) AND trade_date > :s AND trade_date <= :e "
            "ORDER BY stock_code, trade_date"
        ), {"c": codes, "s": _s_min, "e": _buf_end}).fetchall()
        _fwd_by_code = {}
        _fwd_dates = {}
        for c, d, cl in fwd_rows:
            if cl is None:
                continue
            _fwd_by_code.setdefault(c, []).append(float(cl))
            _fwd_dates.setdefault(c, []).append(str(d)[:10])

        import bisect as _bisect
        labels = {days: [] for days in LABEL_DAYS}
        for _, row in df.iterrows():
            price_today = float(row['close'])
            code = row['stock_code']
            tdate = str(row['trade_date'])[:10]
            arr = _fwd_by_code.get(code)
            for days in LABEL_DAYS:
                if not arr or price_today <= 0:
                    labels[days].append(None)
                    continue
                pos = _bisect.bisect_right(_fwd_dates.get(code, []), tdate)
                fwd_win = arr[pos:pos + days + 5]  # 原 LIMIT days+5 语义
                if not fwd_win:
                    labels[days].append(None)
                    continue
                label = None
                for fr in fwd_win:
                    ret = fr / price_today - 1
                    if ret >= TAKE_PROFIT:
                        label = TAKE_PROFIT  # 触及止盈
                        break
                    elif ret <= STOP_LOSS:
                        label = STOP_LOSS   # 触及止损
                        break
                if label is None and len(fwd_win) >= days:
                    # 未触及任何屏障，按时间到期价算
                    label = fwd_win[min(days - 1, len(fwd_win) - 1)] / price_today - 1
                labels[days].append(label)

        for days, col in [(5, 'target_5d'), (10, 'target_10d'), (20, 'target_20d')]:
            df[col] = labels[days]
        df = df.dropna(subset=['target_5d', 'target_10d', 'target_20d'])

        # M2-5: winsorize 标签（1%~99%缩尾）
        try:
            from scipy.stats.mstats import winsorize
            for col in ['target_5d', 'target_10d', 'target_20d']:
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
        import xgboost as _xgb

        # ── GPU 优先：编译含 CUDA 且驱动可用时用 cuda，否则回退 cpu ──
        def _resolve_train_device():
            try:
                _bi = _xgb.build_info()
                if not (_bi.get('USE_CUDA') or _bi.get('CUDA_VERSION')):
                    return 'cpu'
                # 冒烟测试：驱动/显存异常时（如服务器无 GPU 卡）也会在这里暴露
                _probe = XGBRegressor(device='cuda', n_estimators=2, max_depth=1, n_jobs=1, verbosity=0)
                _probe.fit(np.random.rand(128, 4), np.random.rand(128))
                return 'cuda'
            except Exception as _e:
                write_node_log(log_id=log_id, detail=f'CUDA 不可用，回退 CPU 训练: {_e}')
                return 'cpu'
        TRAIN_DEVICE = _resolve_train_device()
        update_node_progress(log_id=log_id, rows=3, detail=f'训练设备: {TRAIN_DEVICE}')

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
            # 宽表日期为字符串，统一转 datetime（持仓期计算需要日期相减）
            dates = pd.to_datetime(dates)
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
                        'signal_source': str(hold_days) + 'd',
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
                'total_trades': trade_count, 'equity_curve': [round(e, 2) for e in equity_curve],
                'trades': trade_log
            }

        best_models = {}
        best_params_store = {}
        best_score = -999
        trial_records = []
        val_dates = df[val_mask]['trade_date'].values
        val_codes = df[val_mask]['stock_code'].values
        val_close = df[val_mask]['close'].values
        val_volume = df[val_mask]['volume'].values
        TARGETS = [('5d','target_5d',5), ('10d','target_10d',10), ('20d','target_20d',20)]

        try:
            import optuna
            from optuna.samplers import TPESampler
            def objective(trial):
                nonlocal best_models, best_params_store, best_score
                # 检查终止信号（A4: 页面 stop / DAG terminate）
                if _aborted():
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
                          'n_jobs': -1, 'random_state': 42, 'verbosity': 0,
                          'device': TRAIN_DEVICE}
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
                avg_r2 = total_r2 / len(TARGETS)
                trial_records.append({'trial': len(trial_records)+1, 'params': params, 'r2': round(avg_r2, 4)})
                update_node_progress(log_id=log_id, rows=len(trial_records), detail=f'Optuna实验:{len(trial_records)}/{n_trials} r²={avg_r2:.4f}')
                if avg_r2 > best_score:
                    best_score = avg_r2
                    best_params_store = {k: {'params': params, 'r2': m['r2']} for k, m in models.items()}
                    best_models = {k: m['model'] for k, m in models.items()}
                db.execute(text("INSERT INTO training_trials (version, trial_number, params, score) VALUES (:v,:n,:p,:s) ON CONFLICT (version, trial_number) DO UPDATE SET params=EXCLUDED.params, score=EXCLUDED.score"),
                           {"v": ver, "n": trial.number + 1, "p": _json.dumps(params), "s": round(float(avg_r2), 4)})
                db.commit()
                return avg_r2
            study = optuna.create_study(direction='maximize', sampler=TPESampler(seed=42))
            study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
        except optuna.exceptions.TrialPruned:
            # 全部 trial 被终止（用户停止 / 模型状态变更）→ 回 DRAFT 收尾
            try: db.close()
            except Exception: pass
            _abort('训练已终止')
            return 0
        except ImportError:
            # Optuna 未安装时回退单次训练（XGBoost）
            params = {'learning_rate': 0.05, 'max_depth': 5, 'n_estimators': 200,
                      'subsample': 0.8, 'colsample_bytree': 0.8,
                      'n_jobs': -1, 'random_state': 42, 'verbosity': 0,
                      'device': TRAIN_DEVICE}
            for label, tname, hdays in TARGETS:
                model = XGBRegressor(**params)
                model.fit(X_train, df[train_mask][tname])
                r2 = round(float(model.score(X_val, df[val_mask][tname])), 4)
                best_models[label] = model
                best_params_store[label] = {'params': params, 'r2': r2}
            update_node_progress(log_id=log_id, rows=1, detail='训练完成(无Optuna)')

        # Optuna 全部 trial 因剪枝/异常未产出模型 → 收尾
        if not best_models:
            try: db.close()
            except Exception: pass
            _abort('训练未产生有效模型（所有 trial 失败或已终止）')
            return 0

        # ── 最终评估：val + test 集回测（val 用于过拟合对比，test 为最终成绩）──
        update_node_progress(log_id=log_id, rows=4, detail='回测评估 (val+test)…')
        test_dates = df[test_mask]['trade_date'].values
        test_codes = df[test_mask]['stock_code'].values
        test_close = df[test_mask]['close'].values
        test_volume = df[test_mask]['volume'].values
        test_idx_ret = df[test_mask]['idx_ret_20d'].values

        def _run_backtests(mask):
            """对给定切分跑全部周期的回测，附带 test R²。"""
            res = {}
            dates = df[mask]['trade_date'].values
            codes = df[mask]['stock_code'].values
            closes = df[mask]['close'].values
            vols = df[mask]['volume'].values
            for label, tname, hdays in TARGETS:
                if label in best_models:
                    y_pred = best_models[label].predict(df[mask][FEATURES])
                    bt = _backtest(df[mask][tname].values, y_pred, dates, codes, closes, vols,
                                   hdays, bt_ver=ver, bt_label=label,
                                   stop_loss=stop_loss, take_profit=stop_loss*2,
                                   commission=commission, stamp_tax=stamp_tax, slippage=slippage)
                    bt['r2'] = round(float(best_models[label].score(df[mask][FEATURES], df[mask][tname])), 4)
                    res[label] = bt
            return res

        if _aborted():
            try: db.close()
            except Exception: pass
            _abort('训练已终止')
            return 0
        val_results = _run_backtests(val_mask)
        if _aborted():
            try: db.close()
            except Exception: pass
            _abort('训练已终止')
            return 0
        test_results = _run_backtests(test_mask)

        # ── 存储最优模型文件 ──
        update_node_progress(log_id=log_id, rows=5, detail='存储最优模型')
        model_dir = f"data/models/{ver}"
        _os.makedirs(model_dir, exist_ok=True)
        for label, tname, hdays in TARGETS:
            if label in best_models:
                path = f"{model_dir}/xgb_{label}.pkl"
                with open(path, 'wb') as f:
                    _pkl.dump(best_models[label], f)
                best_params_store[label]['model_path'] = path

        # ── 汇总回测指标并写入 DB ──
        # 合并全部周期的交易明细（BUY + SELL 分录）
        all_trades = []
        for label in test_results:
            for t in test_results.get(label, {}).get('trades', []):
                t['horizon'] = label
                all_trades.append(t)
        # 去重：同股票同买入日只保留一笔（不同周期可能重复买入），SELL 分录保留
        # 后续质量指标依赖 SELL 的真实逐笔盈亏，不能丢弃
        buy_trades = [t for t in all_trades if t.get('action') == 'BUY']
        sell_trades = [t for t in all_trades if t.get('action') == 'SELL']
        seen = set()
        deduped = []
        for t in buy_trades:
            key = (t['code'], t['date'])
            if key not in seen:
                seen.add(key)
                deduped.append(t)
        all_trades = deduped + sell_trades
        all_trades.sort(key=lambda t: t['date'], reverse=True)

        # 过拟合检测：val R² / test R² + val/test 回测 sharpe 双对比
        _labels = [l for l in ['5d', '10d', '20d'] if l in test_results]
        val_r2s = [best_params_store.get(l, {}).get('r2', 0) for l in _labels]
        test_r2s = [test_results.get(l, {}).get('r2', 0) for l in _labels]
        val_sharpes = [val_results.get(l, {}).get('sharpe', 0) for l in _labels]
        test_sharpes = [test_results.get(l, {}).get('sharpe', 0) for l in _labels]
        val_r2_avg = float(np.mean(val_r2s)) if val_r2s else 0
        test_r2_avg = float(np.mean(test_r2s)) if test_r2s else 0
        val_sharpe_avg = float(np.mean(val_sharpes)) if val_sharpes else 0
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

        # 质量指标：用 SELL 分录的真实逐笔盈亏（BUY 无 pnl）
        sell_pnls = [t.get('pnl', 0) for t in all_trades if t.get('action') == 'SELL' and t.get('pnl') is not None]
        wins = [p for p in sell_pnls if p > 0]
        losses = [abs(p) for p in sell_pnls if p < 0]
        profit_factor = sum(wins) / sum(losses) if losses else (999 if wins else 0)
        avg_win = float(np.mean(wins)) if wins else 0
        avg_loss = float(np.mean(losses)) if losses else 0
        max_dd_avg = float(np.mean([test_results.get(l, {}).get('max_dd', 0) for l in _labels]))

        # 集中度分析：SELL 真实盈利前 3 笔占总盈利比例
        sorted_pnls = sorted([t['pnl'] for t in all_trades if t.get('action') == 'SELL' and t.get('pnl', 0) > 0], reverse=True)
        total_profit = sum(sorted_pnls)
        top3_pct = sum(sorted_pnls[:3]) / total_profit * 100 if total_profit > 0 else 0

        bt_summary = {
            'trials': trial_records[-10:] if trial_records else [],
            'trades': all_trades,
            'trade_count': len(all_trades),
            'top3_concentration': round(top3_pct, 1),
            'val_r2': round(val_r2_avg, 4),
            'test_r2': round(test_r2_avg, 4),
            'val_sharpe': round(val_sharpe_avg, 4),
            'test_sharpe': round(test_sharpe_avg, 4),
            'overfit_gap': round(val_sharpe_avg - test_sharpe_avg, 4),
            'profit_factor': round(profit_factor, 2),
            'avg_win': round(avg_win, 2),
            'avg_loss': round(avg_loss, 2),
            'benchmark_return': round(benchmark_return, 4),
            'sharpe_5d':  test_results.get('5d', {}).get('sharpe', 0),
            'sharpe_10d': test_results.get('10d', {}).get('sharpe', 0),
            'sharpe_20d': test_results.get('20d', {}).get('sharpe', 0),
            'win_rate_5d': test_results.get('5d', {}).get('win_rate', 0),
            'win_rate_10d': test_results.get('10d', {}).get('win_rate', 0),
            'win_rate_20d': test_results.get('20d', {}).get('win_rate', 0),
        }
        avg_sharpe = float(np.mean([test_results[l]['sharpe'] for l in _labels])) if _labels else 0
        avg_win = float(np.mean([test_results[l]['win_rate'] for l in _labels])) if _labels else 0
        best_params = best_params_store

        # 年化：test 回测区间总收益 → 252 交易日年化，各周期平均（替代原拍脑袋估算）
        def _annualize(bt):
            days = len(bt.get('equity_curve', []))
            if days >= 2 and bt.get('total_return', -1) > -1:
                return (1 + bt['total_return']) ** (252 / days) - 1
            return 0
        annual_return = float(np.mean([_annualize(test_results[l]) for l in _labels])) if _labels else 0

        # B3: 回测结果落库（幂等：先删该版本旧记录）
        win_trades = len([t for t in all_trades if t.get('action') == 'SELL' and (t.get('pnl') or 0) > 0])
        test_start = str(test_dates[0])[:10] if len(test_dates) > 0 else None
        test_end = str(test_dates[-1])[:10] if len(test_dates) > 0 else None
        avg_total_return = float(np.mean([test_results[l]['total_return'] for l in _labels])) if _labels else 0
        final_equity = round(initial_cash * (1 + avg_total_return), 2)
        db.execute(text("DELETE FROM backtest_records WHERE version=:v"), {"v": ver})
        db.execute(text("DELETE FROM backtest_trades WHERE version=:v"), {"v": ver})
        db.execute(text("""
            INSERT INTO backtest_records (version, start_date, end_date, initial_cash, final_equity,
                sharpe_ratio, win_rate, max_drawdown, annual_return, total_trades, winning_trades, detail)
            VALUES (:v,:s,:e,:ic,:fe,:sh,:wr,:md,:ar,:tt,:wt,:dt)
        """), {
            "v": ver, "s": test_start, "e": test_end,
            "ic": initial_cash, "fe": final_equity,
            "sh": round(test_sharpe_avg, 4), "wr": round(avg_win, 4),
            "md": round(abs(max_dd_avg), 4), "ar": round(annual_return, 4),
            "tt": len(all_trades), "wt": win_trades,
            "dt": _json.dumps({"labels": list(test_results.keys()),
                               "label_detail": {l: {"sharpe": test_results[l].get('sharpe', 0),
                                                    "win_rate": test_results[l].get('win_rate', 0),
                                                    "total_return": test_results[l].get('total_return', 0),
                                                    "max_dd": test_results[l].get('max_dd', 0)}
                                                for l in _labels},
                               "benchmark_return": round(benchmark_return, 4),
                               "equity_tail": test_results.get('10d', {}).get('equity_curve', [])}),
        })
        for t in all_trades:
            is_buy = t.get('action') == 'BUY'
            db.execute(text("""
                INSERT INTO backtest_trades (version, stock_code, trade_date, direction, price, shares,
                    cost, profit_loss, equity_before, equity_after, reason)
                VALUES (:v,:c,:d,:dir,:p,:sh,:cost,:pnl,:eb,:ea,:rs)
            """), {
                "v": ver, "c": t.get('code', ''), "d": t.get('date'),
                "dir": t.get('action', 'BUY'), "p": t.get('price', 0), "sh": t.get('shares', 0),
                "cost": t.get('amount', 0) if is_buy else t.get('commission_tax', 0),
                "pnl": None if is_buy else t.get('pnl'),
                "eb": t.get('cash_before', 0), "ea": t.get('market_value', 0),
                "rs": (t.get('signal_reason') if is_buy else t.get('reason')) or '',
            })

        db.execute(text("UPDATE model_versions SET status='PENDING', best_params=:bp, evaluation_report=:rep, sharpe=:sh, win_rate=:wr, max_drawdown=:md, annual_return=:ar WHERE version=:v"), {
            "v": ver,
            "bp": _json.dumps(best_params),
            "rep": _json.dumps(bt_summary),
            "sh": round(avg_sharpe, 4),
            "wr": round(avg_win, 4),
            "md": round(abs(max_dd_avg), 4),
            "ar": round(annual_return, 4),
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

# ═══════════════════════════════════════════════
#  stock_master 更新
# ═══════════════════════════════════════════════

def dag_task_stock_master(trade_date=None, **kw):
    """DAG 节点：更新 stock_master（复用 BackfillManager 统一实现，与补数按钮同逻辑）。"""
    from types import SimpleNamespace
    from crawler.backfill import BackfillManager
    log_id = (kw.get('_node_log_ids', {}) or {}).get('stock_master')
    write_node_log(log_id=log_id, status='running', detail='获取股票列表…')
    bm = BackfillManager.get_instance()
    task = SimpleNamespace(
        task_id=f"dag_stock_master_{trade_date or ''}", task_type="stock_master",
        task_label="更新股票列表", status="running", start_date=None, end_date=None, force=True,
        current_batch=0, total_batches=1, stocks_done=0, stocks_total=0, rows=0, errors=0,
        failed_codes=[], started_at=None, updated_at=None, completed_at=None,
        error_message="", _stop_requested=False,
        _company_limit=300,  # dag 场景单次补齐 300 只公司信息（避免阻塞流程；完整补齐走补数按钮）
    )
    try:
        bm._run_stock_master(task)
        rows = task.rows or 0
        detail = task.error_message or f"更新 {rows} 只"
        write_node_log(log_id=log_id, status='success', rows=rows, detail=detail)
        return {'rows': rows, 'detail': detail}
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e)[:200])
        raise


def dag_task_factor_ic(trade_date=None, **kw):
    """factor_ic 节点 — 已发布 stock 特征批量重算 IC（默认近 3 年，1/5/10/20 前瞻）。

    只刷新 factor_ic_stats 数据，不覆盖 features.ic_status 用户决策。
    """
    from datetime import date as _date, timedelta as _td
    from app.db.connection import get_sync_db
    from scripts.factor_ic import compute_factor_ic

    rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('factor_ic')
    write_node_log(log_id=log_id, status='running', detail='批量因子 IC 检验…')
    try:
        db = get_sync_db()
        val_end = str(trade_date or _date.today())[:10]
        val_start = (_date.fromisoformat(val_end) - _td(days=3 * 365)).isoformat()
        feats = db.execute(text(
            "SELECT feature_name FROM features WHERE status='enabled' "
            "AND target_entity='stock' ORDER BY feature_name"
        )).fetchall()
        ok, fail = 0, []
        for (fn,) in feats:
            try:
                compute_factor_ic(db, fn, val_start, val_end)
                ok += 1
            except Exception as e:
                db.rollback()
                fail.append(f'{fn}: {str(e)[:80]}')
                logger.warning(f'[factor_ic] {fn} 检验失败: {e}')
        db.close()
        detail = f'{ok}/{len(feats)} 个因子完成（{val_start}~{val_end}）'
        if fail:
            detail += '；失败: ' + '、'.join(fail[:5])
        write_node_log(log_id=log_id, status='success', detail=detail)
        return True
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e)[:200])
        raise


NODE_FN_MAP = {
    'stock_master':      dag_task_stock_master,    'cron':               dag_task_cron,
    'kline':              dag_task_kline,
    'index':              dag_task_index,
    'etf':                dag_task_etf,
    'fund':               dag_task_fund,
    'treemap':            dag_task_treemap,
    'stats':              dag_task_stats,
    'daily_completeness': dag_task_completeness,
    'model_signal':       dag_task_model_signal,
    'model_health':       dag_task_model_health,
    'feature_compute':    dag_task_feature_compute,
    'feature_backfill':  dag_task_feature_backfill,
    'entity_stats':      dag_task_entity_stats,
    'factor_ic':         dag_task_factor_ic,
}
