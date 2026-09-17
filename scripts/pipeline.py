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

def _median(vals):
    """中位数（行业块聚合用；空列表返回 0）。"""
    vs = sorted(vals)
    n = len(vs)
    if not n: return 0
    return round((vs[n//2] if n % 2 else (vs[n//2-1] + vs[n//2]) / 2), 2)


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

        # ── 批量窗口数据（最近 21 个交易日全市场一次查询）：产出趋势/前收盘/20日涨跌/量比
        # 原 implementation 逐股 2-3 次查询 × 5000 只；现窗口查询 + Python 聚合
        from datetime import datetime, timedelta
        win = {}
        for wc, wtd, wclose, wvol in db.execute(text("""
            WITH days AS (
                SELECT DISTINCT trade_date FROM daily_quote
                WHERE trade_date <= :d ORDER BY trade_date DESC LIMIT 21
            )
            SELECT stock_code, trade_date, close, volume FROM daily_quote
            WHERE trade_date IN (SELECT trade_date FROM days)
            ORDER BY stock_code, trade_date DESC
        """), {"d": trade_date}).fetchall():
            win.setdefault(wc, []).append(
                (float(wclose) if wclose else 0, int(wvol) if wvol else 0))
        trends, prev_map, chg20_map, volr_map = {}, {}, {}, {}
        for code, seq in win.items():
            c0 = seq[0][0] if seq else 0
            prev_map[code] = seq[1][0] if len(seq) > 1 else None
            closes20 = [x[0] for x in seq[:20]]      # 当日 + 前 19（与原 trends 口径一致）
            trends[code] = (len(closes20) >= 20 and c0 >= sum(closes20) / len(closes20)) if closes20 else True
            chg20_map[code] = (c0 - seq[20][0]) / seq[20][0] * 100 if len(seq) >= 21 and seq[20][0] > 0 else None
            vols_prev = [x[1] for x in seq[1:21]]     # 前 20 日均量（不含今日）
            avg_v = sum(vols_prev) / len(vols_prev) if vols_prev else 0
            volr_map[code] = (seq[0][1] / avg_v) if avg_v > 0 else None

        # ── 当日买点信号快照（历史日期回看用；前端对当日另行实时叠加最新信号） ──
        sig_map = {}
        for sc, st, reason, mv in db.execute(text("""
            SELECT stock_code, strength, reason, model_version FROM signal_history
            WHERE signal_date = :d AND strategy_name = 'model_signal' AND direction = 'buy'
        """), {"d": trade_date}).fetchall():
            sig_map[sc] = {'strength': int(st or 0), 'reason': (reason or '')[:60],
                           'model_version': mv}

        # ── 当日换手率（一次查询，供股票 tooltip 与行业块聚合） ──
        tr_map = {tc: float(tv) for tc, tv in db.execute(text(
            "SELECT stock_code, turnover_rate FROM stock_fundamentals WHERE trade_date = :d"
        ), {"d": trade_date}).fetchall() if tv}

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

            prev = prev_map.get(code)
            chg = (price - prev) / prev * 100 if prev else 0

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
            _c20 = chg20_map.get(code)
            _vr = volr_map.get(code)
            l1_map[l1]["l2s"][l2]["stocks"].append({"code": code, "name": name, "price": round(price, 2), "chg": round(chg, 2), "val": round(val, 2), "trend_up": trends.get(code, True),
                "s20": round(_c20, 2) if _c20 is not None else None,          # 20 日涨跌%（反转视角）
                "vr": round(_vr, 2) if _vr else None,                          # 量比（今日量/前20日均量）
                "tr": round(tr_map[code], 2) if code in tr_map else None,        # 换手率 %
                **({"signal": sig_map[code]} if code in sig_map else {})})
            l1_map[l1]["l2s"][l2]["total_mcap"] += val
            l1_map[l1]["total_mcap"] += val

        upsert = "INSERT INTO stock_treemap_cache (trade_date, metric, parent, node_id, name, value, chg_pct, trend_up, node_type, detail) VALUES (:d, :m, :p, :id, :n, :v, :chg, :up, :t, :dt) ON CONFLICT (trade_date, metric, node_id) DO UPDATE SET name=EXCLUDED.name, value=EXCLUDED.value, chg_pct=EXCLUDED.chg_pct, trend_up=EXCLUDED.trend_up, detail=EXCLUDED.detail"
        db.execute(text("DELETE FROM stock_treemap_cache WHERE trade_date=:d AND metric=:m"), {"d": trade_date, "m": metric}); db.commit()

        total = 0
        for l1_code in sorted(l1_map.keys()):
            l1_data = l1_map[l1_code]
            stocks_flat = [s for l2d in l1_data["l2s"].values() for s in l2d["stocks"]]
            avg_chg = round(sum(s["chg"] for s in stocks_flat) / len(stocks_flat), 2) if stocks_flat else 0
            db.execute(text(upsert), {"d": trade_date, "m": metric, "p": "root", "id": l1_code, "n": l1_names.get(l1_code, l1_code), "v": round(l1_data["total_mcap"], 2), "chg": avg_chg, "up": True, "t": "l1", "dt": json.dumps({"count": len(stocks_flat), "sig": sum(1 for x in stocks_flat if x.get("signal")), "up_ratio": round(sum(1 for x in stocks_flat if x["chg"] > 0) / len(stocks_flat) * 100, 1) if stocks_flat else 0,
                "med_chg": _median([x["chg"] for x in stocks_flat]) if stocks_flat else 0,
                "avg_tr": round(sum(x["tr"] for x in stocks_flat if x.get("tr") is not None) / max(sum(1 for x in stocks_flat if x.get("tr") is not None), 1), 2)})}); total += 1
            for l2_code, l2_data in l1_data["l2s"].items():
                l2_avg = round(sum(s["chg"] for s in l2_data["stocks"]) / len(l2_data["stocks"]), 2) if l2_data["stocks"] else 0
                db.execute(text(upsert), {"d": trade_date, "m": metric, "p": l1_code, "id": l2_code, "n": l2_data["name"], "v": round(l2_data["total_mcap"], 2), "chg": l2_avg, "up": True, "t": "l2", "dt": json.dumps({"count": len(l2_data["stocks"]), "sig": sum(1 for x in l2_data["stocks"] if x.get("signal")), "up_ratio": round(sum(1 for x in l2_data["stocks"] if x["chg"] > 0) / len(l2_data["stocks"]) * 100, 1) if l2_data["stocks"] else 0,
                "med_chg": _median([x["chg"] for x in l2_data["stocks"]]) if l2_data["stocks"] else 0,
                "avg_tr": round(sum(x["tr"] for x in l2_data["stocks"] if x.get("tr") is not None) / max(sum(1 for x in l2_data["stocks"] if x.get("tr") is not None), 1), 2)})}); total += 1
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
    # 拓展数据表卡片（v3.8 tushare 2000 积分拓展）：表名→日频主表为行数口径，事件类为事件数口径
    ext_cards = [
        ('个股资金流', 'stock_moneyflow', 'trade_date'),
        ('两融明细', 'stock_margin_detail', 'trade_date'),
        ('龙虎榜', 'stock_top_list', 'trade_date'),
        ('大宗交易', 'block_trade', 'trade_date'),
        ('沪深港通', 'moneyflow_hsgt', 'trade_date'),
        ('指数权重', 'index_weight', 'trade_date'),
        ('限售解禁', 'stock_share_float', 'float_date'),
        ('股票回购', 'stock_repurchase', 'ann_date'),
        ('分红送配', 'stock_dividend', 'ex_date'),
        ('业绩预告', 'stock_forecast', 'ann_date'),
        ('业绩快报', 'stock_express', 'ann_date'),
        ('财务指标', 'fina_indicator', 'end_date'),
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
    # 拓展表卡片：行数精确 COUNT（万级），个股维度表带 items，市场级表（沪深港通）无 items
    for label, tbl, dcol in ext_cards:
        try:
            rows = db.execute(text(f"SELECT COUNT(*) FROM {tbl}")).scalar() or 0
            s = {'label': label, 'rows': rows, 'items': None}
            if tbl != 'moneyflow_hsgt':
                s['items'] = db.execute(text(f"SELECT COUNT(DISTINCT stock_code) FROM {tbl}")).scalar() or 0
            sr = db.execute(text(f"SELECT MIN({dcol})::text FROM {tbl}")).scalar()
            er = db.execute(text(f"SELECT MAX({dcol})::text FROM {tbl}")).scalar()
            if sr: s['start'] = str(sr)[:10]
            if er: s['end'] = str(er)[:10]
            stats.append(s)
        except Exception as e:
            logger.warning(f"[pipeline] 统计 {label} 失败: {e}")
            try: db.rollback()
            except: pass
            stats.append({'label': label, 'rows': -1, 'items': 0})
    # 特征因子卡片：行数/起止为精确查询（14.9 亿行 seq scan 实测 ~90s，仅后台统计节点承受）；
    # 特征数量走 features 注册表（COUNT(DISTINCT feature_name) 实测 9 分钟，不可用）
    try:
        n_features = q("SELECT COUNT(*) FROM features") or 0
        s = {'label': '特征因子', 'rows': q("SELECT COUNT(*) FROM feature_values") or 0,
             'sub': f'{n_features} 个特征'}
        sr = q("SELECT MIN(trade_date)::text FROM feature_values")
        er = q("SELECT MAX(trade_date)::text FROM feature_values")
        if sr: s['start'] = str(sr)[:10]
        if er: s['end'] = str(er)[:10]
        stats.append(s)
    except Exception as e:
        logger.warning(f"[pipeline] 统计 特征因子 失败: {e}")
        try: db.rollback()
        except: pass
        stats.append({'label': '特征因子', 'rows': -1})
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
            if not trade_date or not node_name:
                # 无 DAG 上下文（函数直调）：没有可精确匹配的 running 行，空串绑 DATE 列会报错
                db.close(); return
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

def _hb_thread(log_id, rid, stop, start_time, td='', nn=''):
    """心跳线程：每 25 秒更新 heartbeat + 已运行时长。超时 5 分钟无进展则标记失败。

    进展判定看 rows 和 detail 双指标：读库先于写入自身心跳 detail，心跳自身
    的写入不会被计入（last_detail 保存的是读到的值）。只看 rows 会误杀
    "长时间拉 0 行"的合法阶段——如拓展回补重走已完成日期轴时连续多日
    事件表 0 行、done 不增（2026-09-04 08:29 误杀实例）。
    """
    from app.signal import is_stop_requested, clear_stop_request
    last_rows = -1
    last_detail = None
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
            r = db.execute(_sql("SELECT rows, detail FROM dag_run_log WHERE id=:lid"), {"lid": log_id}).fetchone()
            rows_count = int(r[0]) if r and r[0] else 0
            detail_seen = (r[1] or '') if r else ''
            db.close()
        except:
            rows_count = None
            detail_seen = None
        update_node_progress(log_id=log_id, rows=rows_count, detail=detail,
                             trade_date=td, node_name=nn, run_id=rid)
        # detail 比较：排除心跳自身写入的 '采集中…'（其时长后缀每轮必变，会把
        # 无进展永久掩盖）。节点写入的其他 detail 变化均视为进展。
        own_detail = detail_seen is not None and detail_seen.startswith('采集中')
        progressed = (rows_count is None or rows_count != last_rows
                      or (detail_seen is not None and not own_detail and detail_seen != last_detail))
        if rows_count is not None and not progressed:
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
            if detail_seen is not None and not own_detail:
                last_detail = detail_seen
        stop.wait(25)

def _node_stopped(rid, log_id=None):
    """节点协作终止检查：_with_hb 注册的 stop 事件已被置位（看门狗超时/用户终止）。

    注册键带节点级 log_id：同流程并行节点共用 rid，仅用 rid 会互相读到
    对方收官时的置位事件而误判被终止。
    """
    if not rid:
        return False
    from app.signal import get_stop_event
    key = f"{rid}:{log_id}" if log_id else rid
    ev = get_stop_event(key)
    if ev:
        return ev.is_set()
    if log_id:
        ev = get_stop_event(rid)  # 兼容旧调用方（无 log_id 注册）
        return bool(ev and ev.is_set())
    return False

def _with_hb(log_id, rid, fn, td='', nn=''):
    """带心跳保护执行函数。

    stop 事件注册到 app.signal：心跳线程因看门狗超时或用户终止 set 时，
    节点内部长循环可通过 _node_stopped(rid, log_id) 协作感知并尽快退出
    （否则循环继续跑完会把 failed 覆盖回 success）。
    """
    from app.signal import set_stop_event, clear_stop_events
    stop = _t.Event()
    start_time = _time.time()
    key = f"{rid}:{log_id}" if rid else None
    if key:
        set_stop_event(key, stop)
    hb = _t.Thread(target=_hb_thread, args=(log_id, rid, stop, start_time, td, nn), daemon=True)
    hb.start()
    try:
        return fn()
    finally:
        stop.set()
        if key:
            clear_stop_events(key)

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
        # 当日 roe/营收/净利前向填充（2026-09-07 修复：此前设计依赖 baostock 补数场景，
        # 但该链路长期挂起——面板 roe 只有历史回填的脚印、新交易日永远 NULL，
        # fund_roe 族因子逐日断供。改为库内 fina_indicator 按公告日时点填充，零网络调用）
        try:
            synced = db.execute(text("""
                WITH cand AS (
                  SELECT fh.id, fi.roe, fi.or_yoy, fi.netprofit_yoy
                  FROM stock_fundamentals_history fh
                  JOIN LATERAL (
                    SELECT roe, or_yoy, netprofit_yoy FROM fina_indicator fi
                    WHERE fi.stock_code=fh.stock_code AND fi.ann_date<=fh.report_date AND fi.roe IS NOT NULL
                    ORDER BY fi.ann_date DESC LIMIT 1
                  ) fi ON true
                  WHERE fh.roe IS NULL AND fh.report_date >= CURRENT_DATE - INTERVAL '3 day'
                )
                UPDATE stock_fundamentals_history fh
                SET roe=cand.roe, revenue_yoy=cand.or_yoy, profit_yoy=cand.netprofit_yoy
                FROM cand WHERE fh.id=cand.id
            """)).rowcount
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning(f"[fund] 当日 roe 前向填充失败: {e}")
            synced = 0
        if rows:
            logger.info(f"[fund] DAG 节点写 tushare 主字段+日度PE({hist}行)，roe前向填充{synced}行")
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
    price_col = 'close_hfq'
    ent_filter = "AND exchange IN ('SSE','SZSE')"
    if entity == 'index':
        table = 'index_daily_quote'
        code_col = 'index_code'
        price_col = 'close'          # 指数表无 close_hfq 列
        ent_filter = ''
    elif entity == 'etf':
        ent_filter = "AND (LEFT(stock_code,2)='15' OR LEFT(stock_code,1)='5')"
    # 价格必须为正：停牌/零价行若进入宽表，回测估值会把持仓按 0 计价（伪回撤来源）
    quotes = f"""
        SELECT {code_col} as stock_code, trade_date, {price_col} as close, volume
        FROM {table}
        WHERE trade_date BETWEEN :sd AND :ed {ent_filter}
          AND {price_col} IS NOT NULL AND {price_col} > 0
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

def _backtest(df, pred=None, val_start='', val_end='', hold_days=10,
              stop_loss=0.08, take_profit=0.15, trailing=0.0, max_pos=5,
              seed=None, ideal=False, compound=True, return_curve=False):
    """统一回测入口（M7 起评估链路默认引擎）：pred 模式走 v2 撮合内核。

    v1 兼容：返回结构与 _simple_backtest 完全一致，指标取 v1 口径——
    total_trades 只计卖出、win_rate 按卖价>买价（gross_win）、total_cost 同式。
    M1 已验证 v2 对齐配置与 v1 逐日净值 0 差异（173 日回放）。
    ideal/seed 基线（随机选股 rng 序列 / 前瞻收益选股）保持 v1 引擎——
    这两个模式不走预测分数，v2 无对应语义且无数字回归诉求。
    """
    if pred is not None and not ideal and seed is None:
        from strategy.backtest.engine import run_backtest
        from strategy.backtest.models import TradeConfig
        from strategy.strategy import SignalStrategy
        cfg = TradeConfig(initial_cash=1_000_000, max_positions=max_pos,
                          stop_loss=stop_loss, take_profit=take_profit, trailing=trailing,
                          hold_days=hold_days, comm=0.00025, st_tax=0.001, slip=0.001,
                          min_cost=0.0, impact_cost=0.0, vol_limit=None, deal_price='close',
                          settle_delay=False, forbid_all_trade_at_limit=False,
                          trade_unit=100, downsize_buy=False)
        r = run_backtest(df, pred, SignalStrategy(cfg), cfg, val_start, val_end)
        sells = [t for t in r.trades if t['action'] == 'SELL']
        win_rate = (len([t for t in sells if t.get('gross_win')]) / len(sells)) if sells else 0
        out = {'sharpe': r.sharpe, 'max_dd': r.max_dd, 'win_rate': round(win_rate, 4),
               'total_return': r.total_return, 'total_trades': len(sells),
               'total_cost': r.total_cost, 'total_trades_all': r.total_trades,
               'engine': 'v2'}
        if return_curve:
            eq = [cfg.initial_cash] + [rec.account for rec in r.daily_records]
            turnovers = [rec.turnover for rec in r.daily_records]
            out['equity_curve'] = [round(float(x), 2) for x in eq]
            out['turnover_curve'] = turnovers
            out['trades'] = r.trades
        return out
    return _simple_backtest(df, pred, val_start, val_end, hold_days, stop_loss,
                            take_profit, trailing, max_pos, seed, ideal, compound, return_curve)


def _simple_backtest(df, pred=None, val_start='', val_end='', hold_days=10,
                     stop_loss=0.08, take_profit=0.15, trailing=0.0, max_pos=5,
                     seed=None, ideal=False, compound=True, return_curve=False):
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

    # ── 涨跌停标记（共享 limit_flags：对前收盘的涨跌幅，close 为后复权价）──
    vdf['_limit_up'], vdf['_limit_down'] = limit_flags(vdf)

    dates_unique = sorted(vdf['trade_date'].unique())
    equity = 1_000_000; cash = 1_000_000
    holdings = []; equity_curve = [equity]; turnover_curve = []
    trade_count = win_count = 0
    cost_total = 0.0
    comm = 0.00025; st_tax = 0.001; slip = 0.001

    for d in dates_unique:
        day = vdf[vdf['trade_date'] == d]
        if day.empty: continue
        day_turn = 0.0  # 当日成交额（M4 换手率）

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
            day_turn += gross
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
                    # kind='stable'：分数并列时按行序（代码升序）确定性选股，与 v2 引擎一致
                    picks = list(scored['_score'].sort_values(ascending=False, kind='stable').index[:slots])
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
            day_turn += gross2
            holdings.append({'code': r['stock_code'], 'buy_price': price,
                             'buy_date': d, 'shares': shares, 'peak': price})

        # ── 收盘估值（无候选/无空位也要记账，保证净值曲线逐日连续）──
        pos_val = 0.0
        for h in holdings:
            hday = day[day['stock_code'] == h['code']]
            # 停牌/无行情行按成本价估值（v2 引擎同语义；按 0 会让净值跳空）
            pos_val += h['shares'] * (float(hday['close'].iloc[0]) if not hday.empty else h['buy_price'])
        equity = cash + pos_val
        equity_curve.append(equity)
        turnover_curve.append(day_turn)

    eq = np.array(equity_curve); rets = eq[1:]/eq[:-1] - 1 if len(eq) > 1 else np.array([0])
    sharpe = float(np.mean(rets)/np.std(rets)*np.sqrt(252)) if np.std(rets) > 0 else 0
    total_ret = equity/1_000_000 - 1
    peak = np.maximum.accumulate(eq); dd = np.min((eq-peak)/peak) if len(eq) > 1 else 0
    return {'sharpe': round(sharpe,4), 'max_dd': round(float(dd),4),
            'win_rate': round(win_count/max(trade_count,1),4),
            'total_return': round(float(total_ret),4), 'total_trades': trade_count,
            'total_cost': round(cost_total, 2),
            **({'equity_curve': [round(float(x), 2) for x in equity_curve],
                'turnover_curve': turnover_curve} if return_curve else {})}


def predict_for_version(db, version: str, df, val_start: str, val_end: str, horizon=10):
    """加载 xgb_{horizon}d 模型并对验证区间逐行预测（归因/扫描/信号共用的唯一预测入口）。

    horizon 支持 int 或 list[int]：list 时加载多个周期模型取均值
    （模型信号用 5/10/20 日均值，与此处统一，消除内联预测漂移）。
    模型含训练期派生特征（bias_5_20 / idx_ret_20d）而宽表没有时现场派生对齐；
    idx_ret_20d 查询起点向前扩 90 自然日，保证 pct_change(20) 有预热。
    Returns: (pred Series|None, err|None)，pred 与 df 验证区间行索引对齐。
    """
    import pickle as _pkl, os as _os
    import json as _json
    import pandas as _pd
    import numpy as _np
    from datetime import date as _d, timedelta as _td
    from sqlalchemy import text as _text

    horizons = [horizon] if isinstance(horizon, int) else list(horizon)
    # 模型训练时的特征标准化方式（cs_rank → 推理端必须做同样变换）
    _cfg_row = db.execute(_text("SELECT config FROM model_versions WHERE version=:v"), {"v": version}).fetchone()
    _cfg = (_json.loads(_cfg_row[0]) if isinstance(_cfg_row[0], str) else (_cfg_row[0] or {})) if _cfg_row else {}
    feature_norm = _cfg.get('feature_norm', 'none')
    feature_neut = _cfg.get('feature_neut', False)
    val_mask = (df['trade_date'] >= val_start) & (df['trade_date'] <= val_end)
    if not val_mask.any():
        return None, '验证集无数据'
    val_df = df[val_mask].copy()
    pred_index = val_df.index
    try:
        models = []
        for h in horizons:
            model_path = f"data/models/{version}/xgb_{h}d.pkl"
            if not _os.path.exists(model_path):
                return None, f'模型文件不存在: {model_path}'
            with open(model_path, 'rb') as f:
                models.append((h, _pkl.load(f)))
        # 特征对齐基于首个模型（多周期模型共享训练特征集）
        mf = list(getattr(models[0][1], 'feature_names_in_', []))
        if not mf:
            return None, '模型缺少 feature_names_in_，请重新训练'
        # 特征对齐：模型含训练时派生的特征，宽表没有则现场派生（与 dag_task_model_train 一致）
        if 'bias_5_20' in mf and 'bias_5_20' not in val_df.columns \
                and 'ma_5' in val_df.columns and 'ma_20' in val_df.columns:
            # SQL NUMERIC 为 Decimal，0/0 会抛 DivisionUndefined，先转 float
            val_df['bias_5_20'] = val_df['ma_5'].astype(float) / val_df['ma_20'].astype(float) - 1
        if 'vol_ratio_3d' in mf and 'vol_ratio_3d' not in val_df.columns \
                and 'vol_ratio' in val_df.columns:
            # vol_ratio 在 feature_values 是逐日行，eval 区间起点处前 2 日为 NaN
            # （dropna 由调用方决定；XGBoost 走缺失分支）
            val_df = val_df.sort_values(['stock_code', 'trade_date'])
            val_df['vol_ratio_3d'] = val_df.groupby('stock_code')['vol_ratio'].transform(
                lambda x: x.astype(float).rolling(3).mean())
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
        # 推理端特征中性化：与训练端同配置（先中性化后排名）
        if feature_neut:
            val_df = merge_circ_mv_panel(val_df, db)
            val_df = neutralize_features(val_df, mf, db)
        # 推理端特征标准化：与训练端同配置（cs_rank 逐日截面排名，idx_ret_20d 等常数列归 0.5）
        if feature_norm == 'cs_rank':
            val_df = cs_rank_features(val_df, mf)
        preds = []
        for h, model in models:
            # 特征缺失行保持 NaN 交给 XGBoost 缺失分支（训练端 dropna 无缺失样本；
            # 旧信号内联同口径——fillna(0) 会把"无数据"伪装成截面最低分位，已废弃）
            # 二分类模型取正类概率：predict 返回 0/1 类标签会让排序全部并列
            if hasattr(model, 'predict_proba'):
                preds.append(model.predict_proba(val_df[cols].values)[:, 1])
            else:
                preds.append(model.predict(val_df[cols].values))
        y = _np.mean(preds, axis=0) if len(preds) > 1 else preds[0]
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

    # 三基线回测（同一引擎同一约束，仅选股依据不同）；real 带净值曲线供绩效报告
    real = _backtest(df, pred, val_start, val_end, hold_days,
                     stop_loss, take_profit, max_pos=max_pos, return_curve=True)
    ideal = _simple_backtest(df, None, val_start, val_end, hold_days,
                             0.99, 99.0, max_pos=max_pos, ideal=True, compound=False)
    random = _simple_backtest(df, None, val_start, val_end, hold_days,
                              stop_loss, take_profit, max_pos=max_pos, seed=42)

    # 4. 基准收益（沪深300 同期）+ 绩效报告（M4：IR/excess/alpha/beta/换手）
    benchmark_return = 0
    risk = {}
    try:
        from sqlalchemy import text as _text
        bm = db.execute(_text(
            "SELECT close FROM index_daily_quote WHERE index_code='000300' AND trade_date BETWEEN :s AND :e ORDER BY trade_date"
        ), {"s": val_start, "e": val_end}).fetchall()
        if len(bm) >= 2:
            benchmark_return = (float(bm[-1][0]) / float(bm[0][0]) - 1) if float(bm[0][0]) > 0 else 0
        from strategy.backtest.report import performance_report
        import types as _types
        curve = real.get('equity_curve')
        tcurve = real.get('turnover_curve')
        recs = None
        if curve and tcurve and len(tcurve) == len(curve) - 1:
            # 逐日记录适配：turnover=当日成交额，account=当日收盘净值（曲线[0]为初始基准位）
            recs = [_types.SimpleNamespace(turnover=t, account=curve[i + 1])
                    for i, t in enumerate(tcurve)]
        risk = performance_report(curve,
                                  [float(x[0]) for x in bm] if len(bm) >= 2 else None,
                                  daily_records=recs)
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
        'risk': risk,
        'matrix': matrix,
        'brinson': {
            'model_contribution': round(model_contribution, 4),
            'strategy_contribution': round(strategy_contribution, 4),
            'interaction': round(interaction, 4),
        }
    }



def run_permutation_test(df, pred, val_start: str, val_end: str, hold_days: int,
                         stop_loss: float = 0.08, take_profit: float = 0.15,
                         n_perms: int = 20, seed: int = 7, random_seed: int = 42):
    """置换检验（评估器 v2 的配套仪表）：打乱预测值 n 次构造无信息噪声分布。

    判据：真预测 sharpe 应显著高于打乱分布——打乱后的预测引擎测不出超额，
    否则说明引擎在给噪声送分；同时报告 random 基线作第二参照。
    Returns: {
      'real': {...}, 'perm': {'mean','std','min','max','p5','p95','ret_mean'},
      'random': {...}, 'n': n_perms, 'z': float, 'pct': 真预测分位,
      'verdict': 'strong'(≥p95) | 'above_mean'(z>0) | 'noise'(其余)
    }
    """
    import numpy as np
    import pandas as pd
    real = _backtest(df, pred, val_start, val_end, hold_days, stop_loss, take_profit)
    rng = np.random.default_rng(seed)
    vals = pred.values.copy()
    sharpes, rets = [], []
    for _ in range(n_perms):
        rng.shuffle(vals)
        bt = _backtest(df, pd.Series(vals, index=pred.index),
                       val_start, val_end, hold_days, stop_loss, take_profit)
        sharpes.append(bt['sharpe']); rets.append(bt['total_return'])
    rnd = _simple_backtest(df, None, val_start, val_end, hold_days, stop_loss, take_profit, seed=random_seed)
    pm, ps = float(np.mean(sharpes)), float(np.std(sharpes))
    z = (real['sharpe'] - pm) / ps if ps > 1e-9 else 0.0
    pct = float(np.mean([s < real['sharpe'] for s in sharpes]))
    verdict = 'strong' if pct >= 0.95 else ('above_mean' if z > 0 else 'noise')
    return {
        'real': real,
        'perm': {'mean': round(pm, 4), 'std': round(ps, 4),
                 'min': round(min(sharpes), 4), 'max': round(max(sharpes), 4),
                 'p5': round(float(np.percentile(sharpes, 5)), 4),
                 'p95': round(float(np.percentile(sharpes, 95)), 4),
                 'ret_mean': round(float(np.mean(rets)), 4)},
        'random': rnd, 'n': n_perms,
        'z': round(z, 3), 'pct': round(pct, 3), 'verdict': verdict,
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


def cs_rank_features(df, cols):
    """逐日截面排名 pct 标准化（v3.5 方法论）：每个交易日把特征变成截面分位 0~1。

    消除市场整体水平漂移（beta/牛熊），让模型只学"当日谁比谁强"；NaN 保留为 NaN
    （pandas rank 自动跳过），与推理端单日截面行为一致；同日同值的常数列得到同一分位
    （rank/count 语义，三并列=2/3），信息量归零——正是 idx_ret_20d 这类市场列想要的。
    原地更新并返回 df。
    """
    import pandas as pd
    cols = [c for c in cols if c in df.columns]
    if cols:
        df[cols] = df.groupby('trade_date')[cols].rank(pct=True)
    return df


def merge_circ_mv_panel(df, db):
    """为宽表面板 merge 当日 circ_mv 截面（stock_fundamentals_history），中性化前置步骤。

    trade_date 统一转 str[:10] 后左连接；返回带 circ_mv 列的 df（列残留无害，不在 FEATURES）。"""
    import pandas as pd
    from sqlalchemy import text
    if 'circ_mv' in df.columns:
        return df
    sd = str(df['trade_date'].min())[:10]
    ed = str(df['trade_date'].max())[:10]
    rows = db.execute(text(
        # 该表交易日列名为 report_date（语义即交易日，见 feature_compute._fetch 字段注册）
        "SELECT stock_code, report_date AS trade_date, circ_mv FROM stock_fundamentals_history "
        "WHERE report_date BETWEEN :s AND :e AND circ_mv IS NOT NULL"
    ), {"s": sd, "e": ed}).fetchall()
    if not rows:
        return df
    mv = pd.DataFrame(rows, columns=['stock_code', 'trade_date', 'circ_mv'])
    mv['trade_date'] = mv['trade_date'].astype(str).str[:10]
    mv['circ_mv'] = mv['circ_mv'].astype(float)
    df = df.copy()
    df['trade_date'] = df['trade_date'].astype(str).str[:10]
    return df.merge(mv, on=['stock_code', 'trade_date'], how='left')


def neutralize_features(df, cols, db):
    """逐日截面市值+行业中性化（feature_neut 配置，方法论与 KEPL neut 算子同内核）。

    必须先 merge_circ_mv_panel；NaN 保留为 NaN；与 cs_rank 组合时先中性化再排名。"""
    from scripts.feature_compute import neutralize_columns
    if 'circ_mv' not in df.columns:
        logger.warning('[neut] 面板缺 circ_mv 列，跳过中性化')
        return df
    cols = [c for c in cols if c in df.columns]
    if cols:
        df = neutralize_columns(df, cols, db)
        logger.info(f"[neut] 已中性化 {len(cols)} 列特征（市值+行业残差）")
    return df


def limit_pct(stock_code) -> float:
    """涨跌停幅度：创业板(30)/科创板(68) 20%，其余主板 10%（北交所不在股票池）。"""
    return 0.198 if str(stock_code).startswith(('30', '68')) else 0.098


def limit_flags(df):
    """面板涨跌停标记（纯函数）：需 stock_code+close 列，组内按日期升序。

    Returns: (limit_up, limit_down) 布尔 Series；首日无前收盘为 NaN → False（允许交易）。
    close 为后复权价，除权日幅度略有近似。
    """
    import pandas as pd
    prev = df.groupby('stock_code')['close'].shift(1)
    pct = df['close'] / prev - 1
    lim = df['stock_code'].map(limit_pct)
    return pct >= lim, pct <= -lim


def _paper_meta_from_model(db, ver, td):
    """从 ACTIVE 模型 config 构造纸面组合参数（键名/单位与训练评估口径对齐）。

    2026-09-06 审计修复：原初始化读 trading_rules.risk_management（模型配置无此键）
    → 全部落默认（止损恒 5%），且配置首次写入后永不随模型激活刷新（冻结在 v8.0）。
    对齐口径：stop_loss=risk.stop_loss_pct/100（模型配置是百分数值）、
    take_profit=止损×2（训练回测同式）、trailing=0（训练回测无移动止盈）、
    hold_days=risk.signal_timeout_days。
    """
    import json as _json
    from sqlalchemy import text
    mcfg = db.execute(text("SELECT config FROM model_versions WHERE version=:v"), {"v": ver}).scalar()
    c = _json.loads(mcfg) if isinstance(mcfg, str) else (mcfg or {})
    risk = c.get('risk', {}) or {}
    stop = float(risk.get('stop_loss_pct', 8)) / 100.0
    trail = float(risk.get('trailing_retracement') or 0)
    return {'initial_cash': c.get('initial_cash', 1_000_000),
            'max_positions': int(c.get('max_positions', 5)),
            'stop_loss': stop,
            'take_profit': 99.0 if trail else round(stop * 2, 4),  # trailing 启用时固定止盈关闭（防截断右尾）
            'trailing': trail,
            'hold_days': int(risk.get('signal_timeout_days', 20)),
            'portfolio_gate_dd': (c.get('portfolio_gate') or {}).get('dd'),
            'model_version': ver, 'started': td}


def dag_task_paper_portfolio(trade_date=None, start_date=None, **kw):
    """paper_portfolio 节点 — 纸面组合（影子运行，v2 撮合内核）。

    跟随 ACTIVE 模型的 signal_history 逐日模拟成交（T+1/涨跌停/止损止盈/trailing/
    到期/仓位约束与实盘规则同源），落 paper_positions/paper_trades。
    start_date 提供时从该日回放信号历史建仓（净值从现金起步）；未提供则只步进当日。
    当日已有 EOD 记录时幂等跳过。

    执行口径（design/05 M3）：settle_delay=False（卖出资金当日可用，与旧实现一致；
    T+1 资金为修正点，确认后切换）+ downsize_buy=False（现金不足整单放弃）+
    forbid_all_trade_at_limit=False（仅涨停禁买/跌停禁卖）+
    SignalStrategy(same_day_budget=True)（买入预算=当日盘后净值，旧 paper 口径）。
    """
    from datetime import date as _d
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    import json as _json
    import pandas as pd

    from strategy.backtest.account import Account
    from strategy.backtest.engine import run_backtest
    from strategy.backtest.models import Position, TradeConfig
    from strategy.strategy import SignalStrategy

    td = str(trade_date or _d.today())[:10]
    log_id = (kw.get('_node_log_ids', {}) or {}).get('paper_portfolio')
    if log_id:
        write_node_log(log_id=log_id, status='running', detail='纸面组合步进（v2 内核）…')

    try:
        db = get_sync_db()
        # ACTIVE 模型 + 纸面配置（首次自动从模型配置初始化）
        # _ver：手动引导非 ACTIVE 模型的账户（回放其自身信号历史），仅内部调用使用
        _ver = kw.get('_ver')
        ver = _ver or db.execute(text(
            "SELECT version FROM model_versions WHERE status='ACTIVE' "
            "ORDER BY activated_at DESC NULLS LAST, created_at DESC LIMIT 1")).scalar()
        if not ver:
            if log_id: write_node_log(log_id=log_id, status='success', rows=0, detail='无 ACTIVE 模型，跳过')
            db.close(); return 0
        if _ver:
            # 引导历史模型：用该模型自己的 config 构造纸面参数（不写全局 strategy_config）
            meta = _paper_meta_from_model(db, ver, td)
        else:
            meta_row = db.execute(text(
                "SELECT params FROM strategy_config WHERE strategy_name='paper_portfolio'")).scalar()
            if meta_row:
                meta = _json.loads(meta_row) if isinstance(meta_row, str) else (meta_row or {})
                # 模型切换即刷新：旧配置冻结在上一任模型的风控（v8.0 止损5%/5仓），
                # 新 ACTIVE 上线后若不刷新，纸面行为与该模型回测承诺完全脱节
                if meta.get('model_version') != ver:
                    meta = _paper_meta_from_model(db, ver, td)
                    db.execute(text(
                        "UPDATE strategy_config SET params=:p WHERE strategy_name='paper_portfolio'"),
                        {"p": _json.dumps(meta, ensure_ascii=False)})
                    db.commit()
                    logger.info(f"[paper] 模型切换→{ver}，纸面风控已刷新: 止损{meta['stop_loss']:.0%} "
                                f"{meta['max_positions']}仓 持有{meta['hold_days']}日")
            else:
                meta = _paper_meta_from_model(db, ver, td)
                db.execute(text("""
                    INSERT INTO strategy_config (strategy_name, display_name, enabled, params)
                    VALUES ('paper_portfolio', '纸面组合（影子运行）', true, :p)
                    ON CONFLICT (strategy_name) DO NOTHING
                """), {"p": _json.dumps(meta)})
                db.commit()
        ecfg = TradeConfig(initial_cash=float(meta['initial_cash']),
                           max_positions=int(meta['max_positions']),
                           stop_loss=float(meta['stop_loss']),
                           take_profit=float(meta['take_profit']),
                           trailing=float(meta.get('trailing', 0)),
                           hold_days=int(meta['hold_days']),
                           comm=0.00025, st_tax=0.001, slip=0.001,
                           min_cost=0.0, settle_delay=False,
                           forbid_all_trade_at_limit=False, downsize_buy=False)

        # 幂等：当日该模型账户已有 EOD → 跳过（按模型隔离）
        done = db.execute(text(
            "SELECT COUNT(*) FROM paper_trades WHERE trade_date=:d AND action='EOD' AND model_version=:v"),
            {"d": td, "v": ver}).scalar()
        if done:
            if log_id: write_node_log(log_id=log_id, status='success', rows=0, detail=f'{td} 已步进过，跳过')
            db.close(); return 0

        # 回放：该模型账户为空 → 从其自身首条信号日回放重建（新模型激活自动建账）
        pos_count_v = db.execute(text(
            "SELECT COUNT(*) FROM paper_positions WHERE model_version=:v"), {"v": ver}).scalar()
        first_sig = db.execute(text(
            "SELECT MIN(signal_date) FROM signal_history WHERE strategy_name='model_signal' "
            "AND direction='buy' AND model_version=:v"), {"v": ver}).scalar()
        _sd = start_date or (str(first_sig)[:10] if (pos_count_v == 0 and first_sig) else None)
        replay = bool(_sd and pos_count_v == 0)
        if replay:
            trade_days = [str(r[0])[:10] for r in db.execute(text(
                "SELECT DISTINCT trade_date FROM index_daily_quote WHERE index_code='000300' "
                "AND trade_date >= :s AND trade_date <= :d ORDER BY trade_date"),
                {"s": _sd, "d": td}).fetchall()]
            if not trade_days:
                if log_id: write_node_log(log_id=log_id, status='success', rows=0, detail='回放区间无交易日')
                db.close(); return 0
            range_start = trade_days[0]
        else:
            trade_days = [td]
            range_start = td

        # 信号（回放取整段 / 单日取当日），按 (date, code) 索引
        # 评分用 predict_score（模型三周期均值预测），历史行/异常行回退 strength
        # （2026-09-06 审计修复：原用 0-3 粗分档 strength，top2% 下同档并列靠代码序
        #   随机买入，纸面选股与模型排序脱节）
        sig_rows = db.execute(text(
            "SELECT signal_date, stock_code, strength, id, stock_name, predict_score FROM signal_history "
            "WHERE strategy_name='model_signal' AND direction='buy' AND model_version=:v "
            "AND signal_date >= :s AND signal_date <= :d ORDER BY signal_date, id"),
            {"v": ver, "s": range_start, "d": td}).fetchall()
        sig_map = {(str(r[0])[:10], r[1]): {'strength': float(r[5]) if r[5] is not None else float(r[2] or 0),
                                            'signal_id': r[3],
                                            'stock_name': r[4] or ''} for r in sig_rows}
        # 组合熔断（与训练评估同口径）：最新 EOD 净值较历史峰值回撤超阈值 → 今日不供新买入信号
        # （持仓退出由引擎照常处理）
        pdd = meta.get('portfolio_gate_dd')
        if pdd:
            eod_all = db.execute(text(
                "SELECT trade_date, equity FROM paper_trades WHERE action='EOD' AND trade_date < :d "
                "AND model_version=:v ORDER BY trade_date"), {"d": range_start, "v": ver}).fetchall()
            if eod_all:
                eq_hist = [float(r[1]) for r in eod_all]
                if eq_hist and eq_hist[-1] < max(eq_hist) * (1 - float(pdd)):
                    sig_map = {(d_, c): v_ for (d_, c), v_ in sig_map.items() if d_ != td}
                    logger.info(f"[paper] 组合熔断生效（回撤 {(1 - eq_hist[-1]/max(eq_hist)):.1%} ≥ {pdd:.0%}），{td} 停止开仓")

        # 既有持仓（单日步进时作为种子账户，按模型隔离）
        pos_rows = db.execute(text(
            "SELECT * FROM paper_positions WHERE model_version=:v"), {"v": ver}).fetchall()
        positions = {r.stock_code: {'shares': r.shares, 'buy_price': float(r.buy_price),
                                    'cost_basis': float(r.cost_basis), 'buy_date': str(r.buy_date)[:10],
                                    'peak': float(r.peak) if r.peak else None, 'signal_id': r.signal_id,
                                    'stock_name': r.stock_name or ''} for r in pos_rows}
        # 现金与净值恢复：只取该模型账户的最新 EOD；净值必须连同现金一起恢复——
        # 无行情日（周末/数据未发布）若把净值初始化成现金，持仓会被按 0 计价，
        # 净值瞬间腰斩（实测 -98.47% 事故：15312/1000000-1）
        cash = float(meta['initial_cash'])
        last_eq = cash
        last_cash = cash
        last_bm = None
        _le = db.execute(text(
            "SELECT cash, equity, detail FROM paper_trades WHERE action='EOD' AND model_version=:v "
            "ORDER BY trade_date DESC LIMIT 1"), {"v": ver}).fetchone()
        if _le:
            cash = float(_le[0]) if _le[0] is not None else cash
            last_cash = cash
            last_eq = float(_le[1]) if _le[1] is not None else cash
            try:
                _ldet = _le[2] if isinstance(_le[2], dict) else (_json.loads(_le[2]) if _le[2] else {})
                if _ldet.get('benchmark_close'):
                    last_bm = float(_ldet['benchmark_close'])
            except Exception:
                pass

        # 行情 df：信号 ∪ 持仓代码；回放含区间前一日（涨跌停标记需要真实前收盘），
        # 单日步进取前一交易日 + 当日
        codes = sorted({c for (_, c) in sig_map} | set(positions))
        prev_day = db.execute(text(
            "SELECT MAX(trade_date) FROM daily_quote WHERE trade_date < :d"),
            {"d": range_start if replay else td}).scalar()
        df_start = (str(prev_day)[:10] if prev_day else range_start) if replay \
            else (str(prev_day)[:10] if prev_day else td)
        if codes:
            qrows = db.execute(text(
                "SELECT trade_date, stock_code, close_hfq FROM daily_quote "
                "WHERE trade_date BETWEEN :s AND :d AND close_hfq IS NOT NULL AND close_hfq > 0 "
                "AND stock_code = ANY(:c) ORDER BY trade_date, stock_code"),
                {"s": df_start, "d": td, "c": codes}).fetchall()
            df = pd.DataFrame(qrows, columns=['trade_date', 'stock_code', 'close'])
            df['trade_date'] = df['trade_date'].astype(str)
        else:
            df = pd.DataFrame(columns=['trade_date', 'stock_code', 'close'])
        # 预测分 = 信号强度（仅信号行有分，其余 NaN——策略只对有分候选买入）
        pred = pd.Series(
            [float(sig_map[(d, c)]['strength']) if (d, c) in sig_map else float('nan')
             for d, c in zip(df['trade_date'], df['stock_code'])], index=df.index) \
            if len(df) else None

        # 种子账户（单日步进）：现金 + 持仓状态恢复
        acct = None
        if not replay:
            acct = Account(ecfg)
            acct.cash = cash
            for code, p in positions.items():
                acct.positions[code] = Position(code=code, shares=p['shares'],
                                                buy_price=p['buy_price'], buy_date=p['buy_date'],
                                                cost_basis=p['cost_basis'],
                                                peak=p['peak'] or p['buy_price'])
        result = run_backtest(df, pred, SignalStrategy(ecfg, same_day_budget=True), ecfg,
                              val_start=range_start, val_end=td, account=acct)
        recs = {r.trade_date: r for r in result.daily_records}
        facct = result.final_account

        # 基准收盘（EOD detail 用）
        bm_map = {str(r[0])[:10]: float(r[1]) for r in db.execute(text(
            "SELECT trade_date, close FROM index_daily_quote WHERE index_code='000300' "
            "AND trade_date BETWEEN :s AND :d"), {"s": range_start, "d": td}).fetchall()}

        # 逐日落库：成交（含 signal_id/stock_name 追踪 + 操作前后仓位/总资产快照）
        # + EOD（无行情日延续净值，保逐日曲线）
        # last_eq/last_cash 已从该模型上一 EOD 恢复（净值延续，非现金）
        pos_sig = {c: p.get('signal_id') for c, p in positions.items()}
        pos_name = {c: p.get('stock_name', '') for c, p in positions.items()}
        day_trades = {}
        for t in result.trades:
            day_trades.setdefault(t['date'], []).append(t)
        held_set = set(positions.keys()) if not replay else set()
        shares_held = {c: p['shares'] for c, p in positions.items()}
        run_cash = cash  # 当日逐笔现金推演（日终以引擎 rec.cash 为准校正）
        close_map = {(row.trade_date, row.stock_code): float(row.close)
                     for row in df.itertuples(index=False)} if len(df) else {}
        last_px = {}  # 各股最后已知收盘（停牌/缺行情日估值延续）

        def _held_val(day_, held):
            v = 0.0
            for c in held:
                px_ = close_map.get((day_, c), last_px.get(c))
                if px_ is not None:
                    v += shares_held.get(c, 0) * px_
            return v

        for day in trade_days:
            for t in day_trades.get(day, []):
                code = t['stock_code']
                pre_npos, pre_equity = len(held_set), run_cash + _held_val(day, held_set)
                if t['action'] == 'BUY':
                    sig = sig_map.get((day, code), {})
                    pos_sig[code] = sig.get('signal_id')
                    pos_name[code] = sig.get('stock_name', '')
                    held_set.add(code)
                    shares_held[code] = t['shares']
                    run_cash -= (t['amount'] or 0)  # BUY amount 已含费用（gross+fee）
                else:
                    held_set.discard(code)
                    shares_held.pop(code, None)
                    run_cash += (t['amount'] or 0) - (t['fee'] or 0)  # SELL amount 为净额（不含费）
                post_npos, post_equity = len(held_set), run_cash + _held_val(day, held_set)
                for c in held_set:
                    if (day, c) in close_map:
                        last_px[c] = close_map[(day, c)]
                db.execute(text("""
                    INSERT INTO paper_trades (trade_date, action, stock_code, stock_name, price, shares,
                        amount, commission, pnl, cash, equity, reason, signal_id, model_version,
                        pre_npos, pre_equity, post_npos, post_equity)
                    VALUES (:d, :action, :stock_code, :stock_name, :price, :shares,
                        :amount, :commission, :pnl, :ca, :e, :reason, :signal_id, :v,
                        :pn, :pe, :qn, :qe)
                """), {"d": day, 'action': t['action'], 'stock_code': code,
                       'stock_name': pos_name.get(code, ''), 'price': t['price'],
                       'shares': t['shares'], 'amount': t['amount'], 'commission': t['fee'],
                       'pnl': t.get('pnl'), 'ca': round(recs[day].cash, 2) if day in recs else None,
                       'e': round(recs[day].account, 2) if day in recs else None,
                       'reason': t['reason'], 'signal_id': pos_sig.get(code), 'v': ver,
                       'pn': pre_npos, 'pe': round(pre_equity, 2),
                       'qn': post_npos, 'qe': round(post_equity, 2)})
            rec = recs.get(day)
            if rec:
                # 当日有行情行：以引擎记录为准（并校正逐笔现金推演）
                last_eq = rec.account
                last_cash = rec.cash
                run_cash = rec.cash
                last_npos = len(held_set)
            else:
                # 无行情日（空仓且无信号/全部停牌）：现金/仓位/净值延续上一记录日，保逐日 EOD 曲线
                last_npos = len(held_set)
            _bm = bm_map.get(day) or last_bm  # 指数数据缺失日基准延续（与净值延续同口径）
            if bm_map.get(day):
                last_bm = bm_map[day]
            db.execute(text("""
                INSERT INTO paper_trades (trade_date, action, cash, equity, model_version, detail)
                VALUES (:d, 'EOD', :c, :e, :v, :dt)
            """), {"d": day, "c": round(last_cash, 2), "e": round(last_eq, 2), "v": ver,
                   "dt": _json.dumps({'positions': last_npos,
                                      'benchmark_close': _bm})})

        # 持仓同步：终态 upsert + 已清仓删除（按模型隔离）
        for code, p in facct.positions.items():
            db.execute(text("""
                INSERT INTO paper_positions (stock_code, stock_name, shares, buy_price, cost_basis,
                    buy_date, peak, signal_id, model_version, updated_at)
                VALUES (:stock_code, :stock_name, :shares, :buy_price, :cost_basis,
                    :bd, :peak, :signal_id, :v, CURRENT_TIMESTAMP)
                ON CONFLICT (model_version, stock_code) DO UPDATE SET stock_name=:stock_name, shares=:shares,
                    buy_price=:buy_price, cost_basis=:cost_basis, buy_date=:bd, peak=:peak,
                    signal_id=:signal_id, model_version=:v, updated_at=CURRENT_TIMESTAMP
            """), {"stock_code": code, 'stock_name': pos_name.get(code, ''), 'shares': p.shares,
                   'buy_price': p.buy_price, 'cost_basis': p.cost_basis,
                   'bd': str(p.buy_date)[:10], 'peak': p.peak,
                   'signal_id': pos_sig.get(code), 'v': ver})
        sold = {t['stock_code'] for t in result.trades if t['action'] == 'SELL'}
        for code in sold - set(facct.positions):
            db.execute(text("DELETE FROM paper_positions WHERE stock_code=:c AND model_version=:v"),
                       {"c": code, "v": ver})

        db.commit()
        n_tr = db.execute(text(
            "SELECT COUNT(*) FROM paper_trades WHERE action IN ('BUY','SELL') AND model_version=:v"),
            {"v": ver}).scalar()

        # ── 风控规则评估（step3）：规则启用了才产出告警（risk_alerts 表 + WS 推送）──
        try:
            from app.risk import evaluate_risk_rules
            _alerts = evaluate_risk_rules(db, td)
            if _alerts:
                logger.info(f"[paper_portfolio] 风控告警 {len(_alerts)} 条: "
                            + '; '.join(a['title'] for a in _alerts))
        except Exception as _re:
            logger.warning(f"[paper_portfolio] 风控评估失败（不影响组合步进）: {_re}")

        db.close()
        if log_id:
            write_node_log(log_id=log_id, status='success', rows=len(trade_days),
                           detail=f'纸面组合 {len(trade_days)} 日步进（v2 内核），累计 {n_tr} 笔模拟成交')
        return len(trade_days)
    except Exception as e:
        if log_id:
            write_node_log(log_id=log_id, status='failed', detail=str(e)[:200])
        raise


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
        # _ver：仅重算指定模型（信号页「生成」按钮）；缺省 = 全部主备模型（DAG 节点）
        ver = kw.get('_ver') or db.execute(text(
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

        # 宽表多拉 30 个自然日：vol_ratio_3d 等 rolling 派生特征在单日面板上恒为 NaN，
        # 需要历史行才能算出与训练端一致的 3 日均值（最终只取当日行使用）
        warmup_start = (_date.fromisoformat(today_str) - timedelta(days=30)).isoformat()
        df_wide = build_feature_wide_table(db, feature_names, warmup_start, today_str, 'stock')
        if df_wide.empty:
            write_node_log(log_id=log_id, status='success', rows=0, detail='今日无特征数据')
            db.close(); return 0
        df_today = df_wide[df_wide['trade_date'] == today_str].copy()
        if df_today.empty:
            write_node_log(log_id=log_id, status='success', rows=0, detail='今日无特征数据')
            db.close(); return 0

        # 空仓闸门（regime）：风险期不开新买入。config.regime.enabled 显式开启才生效，
        # 旧模型（v11.0 等无此键）信号行为不变；闸门放行日（连续第 3 天）正常产生信号
        _regime = model_cfg_obj.get('regime') or {}
        if _regime.get('enabled'):
            _gated = compute_regime_gates(db, [today_str], _regime)
            if today_str in _gated:
                write_node_log(log_id=log_id, status='success', rows=0,
                               detail=f'空仓信号日：指数<{_regime.get("ma_window", 20)}日线风险期，不开新仓（连续第 '
                                      f'{_regime.get("max_skip_days", 2)}+1 天放行）')
                db.close(); return 0

        # 派生特征与训练端保持一致（bias_5_20/vol_ratio_3d/idx_ret_20d）：
        # 训练模型 feature_names_in_ 含这些列，信号侧缺列会导致 ML 预测逐股回退规则模式
        if 'ma_5' in df_wide.columns and 'ma_20' in df_wide.columns:
            # SQL NUMERIC 为 Decimal，除零会抛 DivisionByZero，先转 float
            df_wide['bias_5_20'] = df_wide['ma_5'].astype(float) / df_wide['ma_20'].astype(float) - 1
            df_today = df_wide[df_wide['trade_date'] == today_str].copy()
        if 'vol_ratio' in df_wide.columns:
            df_wide['vol_ratio_3d'] = df_wide.groupby('stock_code')['vol_ratio'].transform(
                lambda x: x.astype(float).rolling(3).mean())
            df_today = df_wide[df_wide['trade_date'] == today_str].copy()
        idx_rows = db.execute(text(
            "SELECT close FROM index_daily_quote WHERE index_code='000300' AND trade_date <= :d ORDER BY trade_date DESC LIMIT 21"
        ), {"d": today_str}).fetchall()
        if len(idx_rows) >= 21:
            closes = [float(x[0]) for x in reversed(idx_rows)]
            df_today['idx_ret_20d'] = closes[-1] / closes[0] - 1

        # 特征中性化（与训练端一致）：feature_neut=true 时先做市值+行业残差化，再截面排名
        norm_cols = list(dict.fromkeys(
            list(feature_names) + [c for c in ('bias_5_20', 'vol_ratio_3d', 'idx_ret_20d')
                                    if c in df_today.columns]))
        if model_cfg_obj.get('feature_neut'):
            df_today = merge_circ_mv_panel(df_today, db)
            df_today = neutralize_features(df_today, norm_cols, db)
        # 特征标准化（与训练端一致）：模型配置 feature_norm=cs_rank 时逐列当日截面排名
        if model_cfg_obj.get('feature_norm') == 'cs_rank':
            df_today = cs_rank_features(df_today, norm_cols)

        # ── 执行约束：涨停股不可买，不产生买入信号 ──
        # trading_rules.signal_filter.allow_limit_up=true 可豁免（默认 false）
        allow_limit_up = (model_cfg_obj.get('trading_rules', {}) or {}) \
            .get('signal_filter', {}).get('allow_limit_up', False)
        limit_up_set = set()
        if not allow_limit_up:
            prev_td = db.execute(text(
                "SELECT MAX(trade_date) FROM daily_quote WHERE trade_date < :d"), {"d": today_str}).scalar()
            if prev_td:
                ex = "AND exchange IN ('SSE','SZSE')"
                cur_c = {r[0]: float(r[1]) for r in db.execute(text(
                    f"SELECT stock_code, close_hfq FROM daily_quote WHERE trade_date=:d "
                    f"AND close_hfq IS NOT NULL {ex}"), {"d": today_str}).fetchall()}
                prv_c = {r[0]: float(r[1]) for r in db.execute(text(
                    f"SELECT stock_code, close_hfq FROM daily_quote WHERE trade_date=:d "
                    f"AND close_hfq IS NOT NULL {ex}"), {"d": str(prev_td)[:10]}).fetchall()}
                limit_up_set = {c for c, cl in cur_c.items()
                                if prv_c.get(c, 0) > 0 and cl / prv_c[c] - 1 >= limit_pct(c)}
                logger.info(f"[model_signal] {today_str} 涨停股 {len(limit_up_set)} 只（买入信号将被拦截）")

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
        limit_skipped = 0  # 涨停拦截的买入信号数

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

        def _insert_buy(row, strength, reason, rank=None):
            p5 = pred_by_h.get('5d', {}).get(row.stock_code)
            p10 = pred_by_h.get('10d', {}).get(row.stock_code)
            p20 = pred_by_h.get('20d', {}).get(row.stock_code)
            score = round((p5 + p10 + p20) / 3.0, 4) if None not in (p5, p10, p20) else None
            db.execute(text("""
                INSERT INTO signal_history (signal_date, stock_code, stock_name, direction, strength, price, strategy_name, reason, combined_signal, model_version, params_snapshot, preference,
                    predict_5d_return, predict_10d_return, predict_20d_return, predict_score,
                    ml_confidence, suggested_action, source_strategies)
                VALUES (:d,:c,:n,'buy',:s,:p,'model_signal',:r,true,:v,:sn,:pref,:p5,:p10,:p20,:score,
                    :conf, 'buy', '["model_signal"]')
            """), {"d": td, "c": row.stock_code, "n": row.stock_name, "s": strength, "p": row.close,
                   "r": reason, "v": ver, "sn": model_snapshot, "pref": pref_mode,
                   "p5": round(p5, 4) if p5 is not None else None,
                   "p10": round(p10, 4) if p10 is not None else None,
                   "p20": round(p20, 4) if p20 is not None else None, "score": score,
                   "conf": round(rank, 3) if rank is not None else None})

        if use_predict and xgb_models:
            # M2：预测统一走 predict_for_version（特征派生/标准化/模型加载共享唯一入口，消除内联漂移）
            # 分周期各调一次：均值做截面排序（predict_score），同时落库 predict_{5,10,20}d_return
            # （2026-09-06 审计修复：四列建表以来从未写入，信号页预测列恒空、纸面组合无分可用）
            _p5, _e5 = predict_for_version(db, ver, df_today, today_str, today_str, horizon=5)
            _p10, _e10 = predict_for_version(db, ver, df_today, today_str, today_str, horizon=10)
            _p20, _e20 = predict_for_version(db, ver, df_today, today_str, today_str, horizon=20)
            preds_map = {}      # stock_code -> 预测收益率均值（排序依据）
            pred_by_h = {}      # 周期 -> {stock_code: 预测值}
            fallback_rows = []  # 预测失败（特征缺失/模型异常）→ 回退规则评分
            if _p5 is not None and _p10 is not None and _p20 is not None:
                for h, p in (('5d', _p5), ('10d', _p10), ('20d', _p20)):
                    pred_by_h[h] = {df_today.loc[i, 'stock_code']: float(v) for i, v in p.items()}
                preds_map = {c: (v5 + pred_by_h['10d'][c] + pred_by_h['20d'][c]) / 3.0
                             for c, v5 in pred_by_h['5d'].items()
                             if c in pred_by_h['10d'] and c in pred_by_h['20d']}
            else:
                logger.warning(f"[model_signal] 统一预测入口失败: {_e5 or _e10 or _e20}，整体回退规则模式")
                fallback_rows = rows

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
                    if r.stock_code in limit_up_set:
                        limit_skipped += 1
                        continue
                    p = preds_map[r.stock_code]
                    # 当日分布分位（并列均分：严格小于 + 一半并列），0-1
                    rank = float((vals < p).mean() + 0.5 * (vals == p).mean())
                    # 强度：分位映射 0-3（与规则评分同量纲），SMALLINT 取整
                    strength = min(max(round(rank * 3), 0), 3)
                    _lt = (model_cfg_obj.get('label_transform') or 'none')
                    if _lt == 'top20':
                        reason = (f'入前20%概率{p*100:.0f}%(top{buy_top_pct*100:.0f}%档)'
                                  if ml_mode == 'quantile' else f'入前20%概率{p*100:.0f}%(≥阈值{thr*100:.2f}%)')
                    elif _lt == 'rank':
                        reason = (f'ML分位{p*100:.1f}%(top{buy_top_pct*100:.0f}%档)'
                                  if ml_mode == 'quantile' else f'ML分位{p*100:.1f}%(≥阈值{thr*100:.2f}%)')
                    else:
                        reason = (f'ML预测{p*100:.2f}%(top{buy_top_pct*100:.0f}%档)'
                                  if ml_mode == 'quantile' else f'ML预测{p*100:.2f}%(≥阈值{thr*100:.2f}%)')
                    _insert_buy(r, strength, reason, rank=rank)
                    buy_count += 1

            # 预测失败回退规则评分
            for r in fallback_rows:
                if r.stock_code in limit_up_set:
                    limit_skipped += 1
                    continue
                score, reasons = _rule_score(r)
                if score >= t['buy_score_min']:
                    _insert_buy(r, score, ';'.join(reasons) or '规则评分')
                    buy_count += 1
        else:
            # 规则评分
            for r in rows:
                if r.stock_code in limit_up_set:
                    limit_skipped += 1
                    continue
                if not r.close or r.close == 0:
                    continue
                score, reasons = _rule_score(r)
                if score >= t['buy_score_min']:
                    _insert_buy(r, score, ';'.join(reasons))
                    buy_count += 1

        db.commit()
        if ml_fallback:
            logger.warning(f"[model_signal] ML 预测不可用（{len(fallback_rows)} 只股票回退规则模式）")
        mode_tag = 'ML' if (use_predict and xgb_models) else '规则'
        limit_desc = f' 涨停拦截{limit_skipped}' if limit_skipped else ''
        write_node_log(log_id=log_id, status='success', rows=buy_count,
                       detail=f'{mode_tag}模式 {buy_count} 买入 {len(rows)} 扫描{limit_desc}')
        db.close()
        # 影子运行：信号落库后纸面组合自动步进（幂等；失败不影响信号本身）
        try:
            dag_task_paper_portfolio(trade_date=td)
        except Exception as _pe:
            logger.warning(f"[paper] 影子运行失败（不影响信号）: {_pe}")
        return buy_count
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e)[:200])
        raise


def ic_rolling_stats(dates, rank_ics, window: int = 20):
    """IC 序列滚动统计（纯函数）：取最近 window 个已成熟截面的均值与 ICIR。"""
    import numpy as np
    pairs = [(d, v) for d, v in zip(dates, rank_ics) if v is not None][-window:]
    if len(pairs) < 5:
        return {'dates': [d for d, _ in pairs], 'rank_ic': [v for _, v in pairs],
                'rolling_mean': None, 'rolling_icir': None, 'n': len(pairs)}
    vals = np.array([v for _, v in pairs], dtype=float)
    mean = float(vals.mean())
    std = float(vals.std())
    return {'dates': [d for d, _ in pairs], 'rank_ic': [round(v, 6) for v in vals],
            'rolling_mean': round(mean, 6),
            'rolling_icir': round(mean / std, 4) if std > 1e-9 else None,
            'n': len(pairs)}


def ic_health_status(rolling_mean):
    """IC 健康判级（纯函数）：>0.005 HEALTHY，0~0.005 CAUTION，<0 DEGRADED。"""
    if rolling_mean is None:
        return None
    if rolling_mean > 0.005:
        return 'HEALTHY'
    if rolling_mean >= 0:
        return 'CAUTION'
    return 'DEGRADED'


_SEVERITY = {'HEALTHY': 0, 'CAUTION': 1, 'WARNING': 2, 'CRITICAL': 3}


def _send_feishu_alert(text: str):
    """出站飞书自定义机器人告警（FEISHU_ALERT_WEBHOOK 未配置则跳过，只记日志）。"""
    try:
        from app.config import settings
        import requests as _rq
        if not settings.FEISHU_ALERT_WEBHOOK:
            logger.info(f"[alert] 未配置 FEISHU_ALERT_WEBHOOK，仅记录: {text}")
            return False
        r = _rq.post(settings.FEISHU_ALERT_WEBHOOK,
                     json={'msg_type': 'text', 'content': {'text': text}}, timeout=5)
        return r.status_code == 200
    except Exception as e:
        logger.warning(f"[alert] 飞书告警发送失败: {e}")
        return False


def compute_model_ic_health(db, version: str, horizon: int = 10, window: int = 60):
    """计算 ACTIVE 模型近窗滚动 RankIC（IC 衰减监控的数据源）。

    对最近 window 个"已成熟"截面（交易日 t 的前瞻收益已实现，即 t ≤ 今天−horizon）
    逐日计算模型预测与实现收益的截面 RankIC；特征标准化按模型 config 同训练端。
    Returns: ic_rolling_stats 的 dict + {'horizon', 'model'}
    """
    from datetime import date as _d, timedelta as _td
    from sqlalchemy import text
    import numpy as np
    import pandas as pd

    today = str(_d.today())
    window_start = (_d.today() - _td(days=window * 2 + 40)).isoformat()  # 交易日≈日历日×0.7，留缓冲
    cfg_row = db.execute(text("SELECT feature_list, config FROM model_versions WHERE version=:v"),
                         {"v": version}).fetchone()
    feature_names = cfg_row[0] if isinstance(cfg_row[0], list) else (json.loads(cfg_row[0]) if cfg_row[0] else [])
    cfg = cfg_row[1] if isinstance(cfg_row[1], dict) else (json.loads(cfg_row[1]) if cfg_row[1] else {})
    if not feature_names:
        feature_names = cfg.get('feature_names', [])
    if not feature_names:
        raise ValueError('模型未配置 feature_names')

    wide = build_feature_wide_table(db, feature_names, window_start, today, 'stock')
    if wide.empty:
        raise ValueError('窗口内无特征数据')
    pred, err = predict_for_version(db, version, wide, window_start, today, horizon)
    if pred is None:
        raise ValueError(f'预测失败: {err}')

    # 前瞻收益（同股票按交易日 shift horizon 行）
    buf_end = (_d.fromisoformat(today) + _td(days=horizon * 2 + 10)).isoformat()
    qrows = db.execute(text(
        "SELECT stock_code, trade_date, close_hfq FROM daily_quote "
        "WHERE trade_date BETWEEN :s AND :e AND close_hfq IS NOT NULL AND close_hfq > 0 "
        "AND exchange IN ('SSE','SZSE') ORDER BY stock_code, trade_date"
    ), {"s": window_start, "e": buf_end}).fetchall()
    qdf = pd.DataFrame(qrows, columns=['stock_code', 'trade_date', 'close'])
    qdf['close'] = pd.to_numeric(qdf['close'], errors='coerce')
    # shift(-horizon) 取未来第 N 行（SQL LEAD 语义）；正 shift 是过去收益，会造出虚假高 IC
    qdf['fret'] = qdf.groupby('stock_code')['close'].shift(-horizon) / qdf['close'] - 1
    qdf['trade_date'] = qdf['trade_date'].astype(str)
    fret_map = {(r.stock_code, r.trade_date): r.fret for r in qdf[['stock_code', 'trade_date', 'fret']].itertuples()}

    w = wide[['trade_date', 'stock_code']].copy()
    w['pred'] = pred.loc[wide.index].values
    w['fret'] = [fret_map.get((c, str(d)[:10])) for c, d in zip(w['stock_code'], w['trade_date'])]
    w = w.dropna(subset=['pred', 'fret'])

    def _pair(g):
        if len(g) < 30:
            return None
        return g['pred'].rank().corr(g['fret'].rank())
    ics = w.groupby('trade_date').apply(_pair).dropna()

    stats = ic_rolling_stats([str(d)[:10] for d in ics.index], ics.values, window=window)
    stats.update({'horizon': horizon, 'model': version})
    return stats


def split_windows(dates, n_windows: int):
    """把升序交易日序列等分成 n_windows 个连续不重叠窗口（纯函数）。

    Returns: [(start, end), ...] 字符串日期；窗口数不足 n_windows 时按实际切。
    """
    dates = sorted(str(d)[:10] for d in dates)
    if not dates:
        return []
    n = min(n_windows, len(dates))
    size = len(dates) // n
    out = []
    for i in range(n):
        chunk = dates[i * size: (i + 1) * size] if i < n - 1 else dates[i * size:]
        if chunk:
            out.append((chunk[0], chunk[-1]))
    return out


def promotion_gate(windows, min_win_windows: int = 2):
    """晋升门槛（纯函数）：新模型 vs 基线的多窗口判定。

    规则（v3.6）：≥ min_win_windows 个窗口新模型 sharpe 优于基线，且窗口 RankIC 均值
    不低于基线（"IC 不退化"）。无基线时返回 NO_BASELINE。
    """
    if not windows:
        return 'NO_BASELINE', '无基线模型可比'
    wins = sum(1 for w in windows if (w['new'] or {}).get('sharpe', 0) > (w['baseline'] or {}).get('sharpe', 0))
    import numpy as _np
    ic_new = _np.mean([w['new']['rank_ic'] for w in windows if (w['new'] or {}).get('rank_ic') is not None] or [0])
    ic_base = _np.mean([w['baseline']['rank_ic'] for w in windows if (w['baseline'] or {}).get('rank_ic') is not None] or [0])
    if wins >= min_win_windows and ic_new >= ic_base:
        return 'PASS', f'{wins}/{len(windows)} 窗口 sharpe 胜出，RankIC {ic_new:.4f} ≥ 基线 {ic_base:.4f}'
    return 'FAIL', (f'{wins}/{len(windows)} 窗口 sharpe 胜出（需 ≥{min_win_windows}），'
                    f'RankIC {ic_new:.4f} vs 基线 {ic_base:.4f}')


def run_walk_forward(db, version: str, baseline_version=None, val_start: str = '', val_end: str = '',
                     n_windows: int = 4, hold_days: int = 10,
                     stop_loss: float = 0.05, take_profit: float = 0.15):
    """滚动多窗口稳定性检验（walk-forward 评估协议，晋升门槛的数据源）。

    将 [val_start, val_end] 等分成 n_windows 个连续不重叠窗口，每个窗口独立计算
    新模型（及可选基线模型）的：扣成本回测（_simple_backtest 同一引擎）+ 窗口
    RankIC（已成熟截面）。宽表全区间只建一次。
    """
    from datetime import date as _d, timedelta as _td
    from sqlalchemy import text
    import json
    import numpy as np
    import pandas as pd

    val_end = val_end or str(_d.today())
    if not val_start:
        val_start = (_d.fromisoformat(val_end) - _td(days=365)).isoformat()

    cfg_row = db.execute(text("SELECT feature_list, config FROM model_versions WHERE version=:v"),
                         {"v": version}).fetchone()
    feature_names = cfg_row[0] if isinstance(cfg_row[0], list) else (json.loads(cfg_row[0]) if cfg_row[0] else [])
    cfg = cfg_row[1] if isinstance(cfg_row[1], dict) else (json.loads(cfg_row[1]) if cfg_row[1] else {})
    if not feature_names:
        feature_names = cfg.get('feature_names', [])
    if not feature_names:
        raise ValueError('模型未配置 feature_names')

    df = build_feature_wide_table(db, feature_names, val_start, val_end, 'stock')
    if df.empty:
        raise ValueError('区间内无特征数据')

    # 全区间前瞻收益一次算好（各窗口复用；shift(-horizon)=未来第 N 个交易日）
    buf_end = (_d.fromisoformat(val_end) + _td(days=horizon_to_days(hold_days) * 2 + 10)).isoformat()
    qrows = db.execute(text(
        "SELECT stock_code, trade_date, close_hfq FROM daily_quote "
        "WHERE trade_date BETWEEN :s AND :e AND close_hfq IS NOT NULL AND close_hfq > 0 "
        "AND exchange IN ('SSE','SZSE') ORDER BY stock_code, trade_date"
    ), {"s": val_start, "e": buf_end}).fetchall()
    qdf = pd.DataFrame(qrows, columns=['stock_code', 'trade_date', 'close'])
    qdf['close'] = pd.to_numeric(qdf['close'], errors='coerce')
    h = horizon_to_days(hold_days)
    qdf['fret'] = qdf.groupby('stock_code')['close'].shift(-h) / qdf['close'] - 1
    qdf['trade_date'] = qdf['trade_date'].astype(str)
    fret_map = {(r.stock_code, r.trade_date): r.fret
                for r in qdf[['stock_code', 'trade_date', 'fret']].itertuples()}

    def _window_metrics(ver, w_start, w_end):
        pred, err = predict_for_version(db, ver, df, w_start, w_end, h)
        if pred is None:
            return None
        w = df[(df['trade_date'] >= w_start) & (df['trade_date'] <= w_end)][['stock_code', 'trade_date']].copy()
        w['pred'] = pred.loc[w.index].values
        w['fret'] = [fret_map.get((c, str(d)[:10])) for c, d in zip(w['stock_code'], w['trade_date'])]
        v = w.dropna(subset=['pred', 'fret'])
        rank_ic = None
        if len(v) >= 100:
            def _pair(g):
                return g['pred'].rank().corr(g['fret'].rank()) if len(g) >= 30 else None
            ics = v.groupby('trade_date').apply(_pair).dropna()
            if len(ics):
                rank_ic = round(float(ics.mean()), 4)
        bt = _backtest(df, pred, w_start, w_end, hold_days, stop_loss, take_profit)
        return {'sharpe': bt['sharpe'], 'total_return': bt['total_return'],
                'win_rate': bt['win_rate'], 'total_trades': bt['total_trades'], 'rank_ic': rank_ic}

    windows = split_windows(df['trade_date'].unique(), n_windows)
    if len(windows) < 2:
        raise ValueError(f'区间内可切窗口不足（{len(windows)} 个），请扩大区间')

    out_windows = []
    for w_start, w_end in windows:
        entry = {'start': w_start, 'end': w_end,
                 'new': _window_metrics(version, w_start, w_end),
                 'baseline': _window_metrics(baseline_version, w_start, w_end) if baseline_version else None}
        out_windows.append(entry)

    verdict, reason = promotion_gate(out_windows if baseline_version else [])
    new_vals = [w['new'] for w in out_windows if w['new']]
    result = {
        'version': version, 'baseline': baseline_version,
        'val_start': val_start, 'val_end': val_end,
        'hold_days': hold_days, 'stop_loss': stop_loss, 'take_profit': take_profit,
        'windows': out_windows,
        'new_mean_sharpe': round(float(np.mean([x['sharpe'] for x in new_vals])), 4) if new_vals else None,
        'new_mean_rank_ic': round(float(np.mean([x['rank_ic'] for x in new_vals if x['rank_ic'] is not None])), 4)
                            if any(x['rank_ic'] is not None for x in new_vals) else None,
        'verdict': verdict, 'reason': reason,
    }
    return result


def horizon_to_days(hold_days: int) -> int:
    """持仓天数 → 模型周期映射（5/10/20）。"""
    return 5 if hold_days <= 5 else (20 if hold_days > 10 else 10)


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
        ver = kw.get('_ver') or db.execute(text(
            "SELECT version FROM model_versions WHERE status='ACTIVE' "
            "ORDER BY activated_at DESC NULLS LAST, created_at DESC LIMIT 1"
        )).scalar()
        if not ver:
            write_node_log(log_id=log_id, status='success', rows=0, detail='无 ACTIVE 模型，跳过')
            db.close()
            return 0

        # 1. 回填 forward 收益（5/10/20 个交易日前的信号，所有版本+方向）
        # v3.7 修复：原先按自然日回推（5 自然日 ≈ 3 交易日，系统性短算）且只匹配
        # "恰好第 N 天"那一天（节点当天没跑则永久漏填）；现按交易日历回推，
        # 并补填历史漏填的 NULL 行（按信号日 + N 交易日的目标日收盘价计算）
        trade_cal = [str(r[0])[:10] for r in db.execute(text(
            "SELECT cal_date FROM trade_calendar WHERE cal_date <= :d ORDER BY cal_date DESC LIMIT 80"
        ), {"d": td}).fetchall()]
        cal_idx = {d: i for i, d in enumerate(trade_cal)}   # 0=今天，越大越早
        missed = 0
        for days in [5, 10, 20]:
            col = f"forward_{days}d_return"
            if days >= len(trade_cal):
                continue
            target_date = trade_cal[days]                   # 今天往前第 N 个交易日
            # 新信号：恰好到期的当日回填
            signals = db.execute(text(f"""
                SELECT id, stock_code, signal_date, price, direction FROM signal_history
                WHERE signal_date = :d AND strategy_name = 'model_signal' AND {col} IS NULL
            """), {"d": target_date}).fetchall()
            # 漏填补录：到期已超过 1 个交易日但仍为 NULL 的信号（历史节点缺跑）
            if days + 1 < len(trade_cal):
                backstop_date = trade_cal[days + 1]
                signals += db.execute(text(f"""
                    SELECT id, stock_code, signal_date, price, direction FROM signal_history
                    WHERE signal_date = :d AND strategy_name = 'model_signal' AND {col} IS NULL
                """), {"d": backstop_date}).fetchall()
            for sig in signals:
                # 到期日收盘价：按交易日历取信号日后第 N 个交易日（漏填补录时该日 < 今天）
                if sig.signal_date not in cal_idx:
                    continue
                sig_seq = cal_idx[sig.signal_date]
                expire_seq = sig_seq - days
                if expire_seq < 0 or expire_seq >= len(trade_cal):
                    missed += 1
                    continue
                expire_date = trade_cal[expire_seq]
                close = db.execute(text(
                    "SELECT close_hfq FROM daily_quote WHERE stock_code=:c AND trade_date=:d"
                ), {"c": sig.stock_code, "d": expire_date}).scalar()
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
        # 超时了结：超过 N 个交易日未触发止损/止盈的信号按到期日收盘价了结
        # （原先只有止损了结 → actual_return 恒为负 → 胜率恒 0 → 健康度恒 CRITICAL）
        timeout_days = 20
        try:
            _paper_cfg = db.execute(text(
                "SELECT params FROM strategy_config WHERE strategy_name='paper_portfolio'")).scalar()
            _pc = _json.loads(_paper_cfg) if isinstance(_paper_cfg, str) else (_paper_cfg or {})
            timeout_days = int(_pc.get('hold_days', 20)) or 20
        except Exception:
            pass
        cal_list = [d for d in reversed(trade_cal)]          # 升序（旧→今），长度 ≤80
        open_sigs = db.execute(text("""
            SELECT id, stock_code, signal_date, price, direction, preference FROM signal_history
            WHERE strategy_name='model_signal' AND status IS NULL
        """)).fetchall()
        for sig in open_sigs:
            sig_d = str(sig.signal_date)[:10]
            close_price = db.execute(text(
                "SELECT close_hfq FROM daily_quote WHERE stock_code=:c AND trade_date=:d"
            ), {"c": sig.stock_code, "d": td}).scalar()
            reason = None
            actual_ret = None
            if close_price and sig.price and float(sig.price) > 0:
                pnl = (float(close_price) - float(sig.price)) / float(sig.price)
                stop = sp_map.get(sig.preference, 0.08)  # 用信号自身偏好
                tp = stop + 0.07                          # 止盈线：止损线的镜像偏移（与纸面 0.08/0.15 同比例）
                expired = False
                if sig_d in cal_idx:
                    held = cal_idx[sig_d]                 # 信号日到今天隔了几个交易日
                    expired = held >= timeout_days
                elif sig_d < trade_cal[-1]:
                    expired = True  # 早于80天日历窗口的悬空信号（原逻辑永不过期）
                if sig.direction == 'buy':
                    if pnl < -stop:
                        reason = 'stop_loss'
                        actual_ret = pnl
                    elif pnl >= tp:
                        reason = 'take_profit'
                        actual_ret = pnl
                    elif expired:
                        reason = 'hold_expire'
                        actual_ret = pnl
                elif sig.direction == 'sell':
                    # 卖出信号：价格上涨超过止损线→了结
                    if pnl > stop:
                        reason = 'stop_loss'
                        actual_ret = pnl
                    elif expired:
                        reason = 'hold_expire'
                        actual_ret = pnl
            if reason:
                db.execute(text("UPDATE signal_history SET status='closed', actual_return=:r, close_reason=:c, closed_at=CURRENT_DATE WHERE id=:id"),
                           {"r": round(actual_ret, 4) if actual_ret else None, "c": reason, "id": sig.id})

        # 3. 5 维度评估（按版本过滤）
        total = db.execute(text("SELECT COUNT(*) FROM signal_history WHERE strategy_name='model_signal' AND model_version=:v"), {"v": ver}).scalar() or 0
        closed = db.execute(text("SELECT COUNT(*) FROM signal_history WHERE strategy_name='model_signal' AND status='closed' AND model_version=:v"), {"v": ver}).scalar() or 0
        wins = db.execute(text("SELECT COUNT(*) FROM signal_history WHERE strategy_name='model_signal' AND actual_return > 0 AND model_version=:v"), {"v": ver}).scalar() or 0
        win_rate = wins / max(closed, 1)
        avg_f5 = db.execute(text("SELECT AVG(forward_5d_return) FROM signal_history WHERE strategy_name='model_signal' AND forward_5d_return IS NOT NULL AND model_version=:v"), {"v": ver}).scalar()  # 无到期样本保持 None

        health = 'HEALTHY'
        # closed=0 时胜率无意义（未有任何信号到期定性，0/1=0% 会误报 CRITICAL），
        # 不参与判级；IC 衰减仍可按其标准升级
        win_meaningful = closed > 0
        if win_meaningful:
            if win_rate < 0.3:
                health = 'CRITICAL'
            elif win_rate < 0.45:
                health = 'WARNING'
            elif win_rate < 0.5:
                health = 'CAUTION'

        # ── 4. IC 衰减监控（v3.6）：近窗滚动 RankIC，判级劣于胜率评估则升级 ──
        ic_stats = None
        try:
            ic_stats = compute_model_ic_health(db, ver, horizon=10, window=20)
            ic_status = ic_health_status(ic_stats.get('rolling_mean'))
            detail_ic = {
                'rolling_mean': ic_stats.get('rolling_mean'),
                'rolling_icir': ic_stats.get('rolling_icir'),
                'n': ic_stats.get('n'), 'horizon': ic_stats.get('horizon'),
                'recent': list(zip(ic_stats['dates'][-5:], ic_stats['rank_ic'][-5:])),
            }
            ic_sev = _SEVERITY.get(ic_status, 0)
            if _SEVERITY.get(health, 0) < ic_sev:
                health = 'WARNING' if ic_status == 'DEGRADED' else ic_status
        except Exception as e:
            logger.warning(f"[model_health] IC 健康计算失败（不影响主流程）: {e}")
            ic_status, detail_ic = None, {'error': str(e)[:200]}

        prev_status = db.execute(text(
            "SELECT health_status FROM model_health WHERE version=:v AND check_date < :d "
            "ORDER BY check_date DESC LIMIT 1"), {"v": ver, "d": td}).scalar()

        # ── 5. 绩效指标（M4）：纸面组合 EOD 净值序列 → IR/alpha/beta/excess/换手 ──
        perf = None
        try:
            eod = db.execute(text(
                "SELECT trade_date, equity FROM paper_trades WHERE action='EOD' "
                "ORDER BY trade_date DESC LIMIT 238")).fetchall()
            if len(eod) >= 2:
                eod = list(reversed(eod))
                curve = [float(r[1]) for r in eod]
                bm = db.execute(text(
                    "SELECT close FROM index_daily_quote WHERE index_code='000300' "
                    "AND trade_date BETWEEN :s AND :e ORDER BY trade_date"),
                    {"s": str(eod[0][0])[:10], "e": str(eod[-1][0])[:10]}).fetchall()
                from strategy.backtest.report import performance_report
                perf = performance_report(curve, [float(r[0]) for r in bm] if len(bm) >= 2 else None)
        except Exception as e:
            logger.warning(f"[model_health] 绩效指标计算失败（不影响主流程）: {e}")

        db.execute(text("""
            INSERT INTO model_health (version, check_date, health_status, live_win_rate, signal_count,
                avg_forward_5d, rank_ic, rank_icir, ir, alpha_annualized, beta, excess_annualized,
                turnover_daily, detail)
            VALUES (:v, :d, :h, :wr, :sc, :af, :ric, :rir, :ir, :alpha, :beta, :exc, :to, :dt)
            ON CONFLICT (version, check_date) DO UPDATE SET
                health_status=EXCLUDED.health_status, live_win_rate=EXCLUDED.live_win_rate,
                signal_count=EXCLUDED.signal_count, avg_forward_5d=EXCLUDED.avg_forward_5d,
                rank_ic=EXCLUDED.rank_ic, rank_icir=EXCLUDED.rank_icir, detail=EXCLUDED.detail,
                ir=EXCLUDED.ir, alpha_annualized=EXCLUDED.alpha_annualized, beta=EXCLUDED.beta,
                excess_annualized=EXCLUDED.excess_annualized, turnover_daily=EXCLUDED.turnover_daily
        """), {
            "v": ver, "d": td, "h": health,
            "wr": round(win_rate, 4) if win_meaningful else None,  # 无了结样本存 NULL（非 0）
            "sc": total, "af": round(float(avg_f5), 4) if avg_f5 is not None else None,
            "ric": ic_stats.get('rolling_mean') if ic_stats else None,
            "rir": ic_stats.get('rolling_icir') if ic_stats else None,
            "ir": perf.get('sum', {}).get('information_ratio') if perf else None,
            "alpha": perf.get('excess', {}).get('alpha_annualized') if perf else None,
            "beta": perf.get('excess', {}).get('beta') if perf else None,
            "exc": perf.get('excess', {}).get('annualized_return') if perf else None,
            "to": perf.get('turnover', {}).get('daily_avg') if perf else None,
            "dt": _json.dumps({"closed": closed, "wins": wins, "forward_5d_avg": float(avg_f5) if avg_f5 is not None else None,
                               "ic": detail_ic, "ic_status": ic_status,
                               "perf": perf if perf else None}),
        })

        # IC 转入 DEGRADED 时告警（飞书 webhook 配置后生效；升级沿 _SEVERITY 比较）
        if ic_status == 'DEGRADED' and _SEVERITY.get(prev_status or '', 0) < _SEVERITY.get(health, 0):
            _send_feishu_alert(
                f"[stone] 模型 {ver} IC 衰减告警：近20截面 RankIC 均值 "
                f"{ic_stats.get('rolling_mean'):.4f} < 0，健康度 {health}。建议检查因子有效性或回退上一版模型。")

        db.commit()
        db.close()
        # rolling_mean 为 None（成熟截面 <5 个）时不能进入 :.4f 格式化（ValueError 会把
        # 已 commit 的成功节点误标 failed）；dict 本身非空，须单独判键
        ic_desc = (f" RankIC均值{ic_stats['rolling_mean']:.4f}({ic_status})"
                   if ic_stats and ic_stats.get('rolling_mean') is not None else " IC不可用")
        wr_desc = f'胜率{win_rate:.0%}(已定性{closed})' if win_meaningful else f'暂无到期信号({total}条跟踪中)'
        write_node_log(log_id=log_id, status='success', rows=total,
                       detail=f'{health}: {wr_desc}{ic_desc}')
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
        # 拓展表当日行数（表名→日期轴；事件类表当日 0 行属正常，仅展示不参与 pct 计算）
        ext_axes = {
            'stock_moneyflow': 'trade_date', 'stock_margin_detail': 'trade_date',
            'stock_top_list': 'trade_date', 'block_trade': 'trade_date',
            'moneyflow_hsgt': 'trade_date', 'index_weight': 'trade_date',
            'stock_share_float': 'float_date', 'stock_repurchase': 'ann_date',
            'stock_dividend': 'ex_date', 'stock_forecast': 'ann_date',
            'stock_express': 'ann_date', 'fina_indicator': 'ann_date',
        }
        ext_stats = {}
        for tbl, dcol in ext_axes.items():
            try:
                ext_stats[tbl] = db.execute(text(f"SELECT COUNT(*) FROM {tbl} WHERE {dcol}=:d"), {"d": td}).scalar() or 0
            except Exception as ee:
                db.rollback()
                logger.warning(f"[completeness] 拓展表 {tbl} 统计失败: {ee}")
                ext_stats[tbl] = -1
        import json as _json
        db.execute(text("""
            INSERT INTO daily_completeness (trade_date, stock_rows, index_rows, etf_rows, fund_rows,
                stock_baseline, index_baseline, etf_baseline, fund_baseline, ext_stats)
            VALUES (:d, :sr, :ir, :er, :fr, :sb, :ib, :eb, :fb, CAST(:ext AS JSONB))
            ON CONFLICT (trade_date) DO UPDATE SET
                stock_rows=EXCLUDED.stock_rows, index_rows=EXCLUDED.index_rows,
                etf_rows=EXCLUDED.etf_rows, fund_rows=EXCLUDED.fund_rows,
                stock_baseline=EXCLUDED.stock_baseline, index_baseline=EXCLUDED.index_baseline,
                etf_baseline=EXCLUDED.etf_baseline, fund_baseline=EXCLUDED.fund_baseline,
                ext_stats=EXCLUDED.ext_stats,
                updated_at=CURRENT_TIMESTAMP
        """), {"d": td, "sr": stock, "ir": idx_r, "er": etf, "fr": fund,
               "sb": stock_bl, "ib": index_bl, "eb": etf_bl, "fb": stock_bl,
               "ext": _json.dumps(ext_stats)})
        db.commit(); db.close()
        write_node_log(log_id=log_id, status='success', rows=4, detail=f'stock={stock} idx={idx_r} etf={etf} ext={sum(v for v in ext_stats.values() if v>0)}')
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
        # 进度心跳：本节点正常要跑 ~35 分钟，原先只在首尾写日志 → heartbeat_at 长时间不变，
        # 会被 dag_status 的「10 分钟无更新判失败」看门狗误杀（2026-09-10 实跑撞到）。
        # compute_all_features 支持 progress_cb，这里接上（批内每次处理完都会回调）。
        def _hb(done, total, fname, rows):
            update_node_progress(
                log_id=log_id, rows=rows,
                detail=f'特征计算 {done}/{total}: {fname}')

        result = compute_all_features(db, target_entity="stock", start_date=start, end_date=today,
                                      progress_cb=_hb)
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



def index_forward_return(idx_dates, idx_close, tdate, days):
    """指数从 tdate 起 N 个交易日后的收益（超额标签的基准腿）。

    tdate 不在指数日历中且存在前一交易日时回退前一交易日；日历前日期/窗口不足/价格非法返回 None。
    """
    import bisect as _b
    pos = _b.bisect_left(idx_dates, tdate)
    if pos >= len(idx_dates):
        return None
    if idx_dates[pos] != tdate:
        if pos == 0:
            return None
        pos -= 1
    j = pos + days
    if j >= len(idx_close) or idx_close[pos] <= 0:
        return None
    return idx_close[j] / idx_close[pos] - 1


def prepare_model_frame(db, df, cfg, feature_names, data_start, end_date):
    """宽表 → 可训练/可评估面板：派生特征 + 涨跌停标记 + 中性化 + 截面排名 + 标签。

    训练（dag_task_model_train）与复评（scripts/eval_version.py）共用同一实现——
    预处理若各写一份，复评数字与训练期就不可比（2026-09-11 定位伪回撤后补的护栏）。
    返回 (df, FEATURES)。
    """
    import pandas as pd
    import numpy as np
    from sqlalchemy import text
    # 只保留数值列
    feature_cols = [c for c in feature_names if c in df.columns]
    df = df[['trade_date','stock_code'] + feature_cols + ['close','volume']].copy()
    for c in feature_cols + ['close','volume']:
        if c in df.columns:
            df[c] = df[c].astype(float)

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

    # 涨跌停标记（训练内回测的执行约束；在 dropna 前计算，避免缺口行误判前收盘）
    df['_limit_up'], df['_limit_down'] = limit_flags(df)

    # 特征中性化（feature_neut）：先残差化后排名——对原始因子去市值/行业暴露
    if cfg.get('feature_neut'):
        df = merge_circ_mv_panel(df, db)
        df = neutralize_features(df, FEATURES, db)

    # 特征标准化（v3.5 可配置）：cs_rank=逐日截面排名 pct——在 dropna 之前做，
    # 与推理端一致（对每个特征的现有值排名，NaN 不参与排名）
    feature_norm = cfg.get('feature_norm', 'none')
    if feature_norm == 'cs_rank':
        df = cs_rank_features(df, FEATURES)
        logger.info(f"[train] 特征已逐日截面排名标准化（{len(FEATURES)} 列）")

    # 只保留有效特征列 + close/volume
    valid_features = [f for f in FEATURES if f in df.columns]
    df = df.dropna(subset=valid_features)

    # ── 3. 标签：build_targets 与 scripts/eval_version.py 共用同一实现 ──
    df = build_targets(db, df, cfg)
    return df, FEATURES


def _rank_score(model, X, y):
    """pairwise/rank 标签下的评估：预测分与标签的 Spearman 秩相关。"""
    from scipy.stats import spearmanr
    p = model.predict(X)
    return round(float(spearmanr(p, y).statistic), 4)


def _auc_score(model, X, y):
    """binary 标签下的评估：AUC（Mann-Whitney 秩公式，纯 numpy）。

    必须取 predict_proba——XGBClassifier.predict 返回 0/1 类标签，
    正类基础率 20% 时预测几乎全 0，并列秩会让 AUC 恒 ≈0.5（假随机）。
    """
    p = model.predict_proba(X)[:, 1] if hasattr(model, 'predict_proba') else model.predict(X)
    y = np.asarray(y)
    order = np.argsort(p, kind='stable')
    ranks = np.empty(len(p)); ranks[order] = np.arange(1, len(p) + 1)
    n_pos = int((y == 1).sum()); n_neg = int((y == 0).sum())
    if n_pos == 0 or n_neg == 0:
        return 0.0
    auc = (ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)
    return round(float(auc), 4)


def val_score(model, X, y, objective='regression'):
    """按训练目标选评估指标：binary→AUC / pairwise→秩相关 / 回归→r²（字段沿用 r2 落库）。"""
    if objective == 'binary':
        return _auc_score(model, X, y)
    if objective == 'pairwise':
        return _rank_score(model, X, y)
    return round(float(model.score(X, y)), 4)


def pred_score(model, X):
    """预测分统一入口：分类器取正类概率（predict 是 0/1 类标签，排序会全部并列）。

    训练评估与复评（scripts/eval_version.py）共用；二分类模型若走 predict，
    AUC 会恒 ≈0.5、quantile 排序全并列（2026-09-08 修过三处）。
    """
    if hasattr(model, 'predict_proba'):
        return model.predict_proba(X)[:, 1]
    return model.predict(X)


class SeedEnsemble:
    """多种子平均集成（回归）：predict=成员预测均值、score=均值预测的 r²。

    同一最优超参换 random_state 拟合 N 个成员取均值——单模型的种子噪声被
    平均掉，val-test 差距中的方差成分收窄。duck-typing 兼容 xgboost 接口，
    对 pred_score / val_score / 信号链路（pkl 载入）完全透明。
    注意：**不定义** predict_proba——回归成员没有概率接口，定义了会让
    pred_score 的 hasattr 探测误走概率分支（XGBRegressor 曾在此炸掉）；
    分类集成用 ProbaSeedEnsemble。
    """

    def __init__(self, models):
        self.models = models

    def predict(self, X):
        import numpy as np
        return np.mean([m.predict(X) for m in self.models], axis=0)

    def score(self, X, y):
        import numpy as np
        pred = self.predict(X)
        y = np.asarray(y, dtype=float)
        ss_res = float(np.sum((y - pred) ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        return round(1 - ss_res / ss_tot, 4) if ss_tot > 0 else 0.0


class ProbaSeedEnsemble(SeedEnsemble):
    """多种子平均集成（分类）：predict_proba=成员概率均值（pred_score 取 [:,1] 正类概率）。"""

    def predict_proba(self, X):
        import numpy as np
        return np.mean([m.predict_proba(X) for m in self.models], axis=0)


def seed_ensemble(models):
    """按成员类型选集成类：全体有 predict_proba 才走概率版。"""
    if all(hasattr(m, 'predict_proba') for m in models):
        return ProbaSeedEnsemble(models)
    return SeedEnsemble(models)


def with_training_protocol_defaults(cfg):
    """补训练协议默认键（purged CV + 多种子集成）——克隆/旧配置升级用。

    只在缺键时补（显式 enabled:false / seeds:1 的选择不被覆盖）。
    v3.9.2 前的模型 config 无这些键，训练端按关闭兼容；rolling_retrain 克隆
    ACTIVE 时经此函数带上新协议——「默认用上」靠机制不靠人记。
    """
    out = dict(cfg or {})
    out.setdefault('selection_cv', {'enabled': True, 'folds': 4, 'embargo_days': 25})
    out.setdefault('ensemble', {'seeds': 3})
    return out


def cross_sectional_rank_ic(pred, target, dates):
    """逐日截面 Spearman 秩相关均值（RankIC）——模型侧纯度量，不含成交/组合。

    与组合夏普差距解耦的原因：2026-09-15 实测 v15.0 出现 test RankIC 最高
    （20d +0.086）而回测最差（-1.08）的周期——夏普差距主体在环境/成本。
    向量化实现（组内 rank 后按日期聚合协方差），避免逐日 apply 的性能陷阱。
    """
    import numpy as np
    import pandas as pd
    t = pd.DataFrame({'d': pd.Series(dates).astype(str).values,
                      'p': np.asarray(pred, dtype=float),
                      'r': np.asarray(target, dtype=float)}).dropna()
    if len(t) < 10:
        return 0.0
    t['pr'] = t.groupby('d')['p'].rank()
    t['rr'] = t.groupby('d')['r'].rank()
    g = t.groupby('d')
    n = g['pr'].count()
    mp = g['pr'].transform('mean')
    mr = g['rr'].transform('mean')
    cov = ((t['pr'] - mp) * (t['rr'] - mr)).groupby(t['d']).sum() / (n - 1).clip(lower=1)
    sp = g['pr'].std()
    sr = g['rr'].std()
    ic = (cov / (sp * sr).replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).dropna()
    return round(float(ic.mean()), 4) if len(ic) else 0.0


def benchmark_window_stats(db, windows, index_code='000300'):
    """各时间窗基准（默认沪深300）区间收益与年化——差距分解的环境成分。

    windows: {name: (start, end)}；返回 {name: {'days','total_return','annual'}}。
    2026-09-15 实测：v15 三窗基准年化 train +0.9% / val +37.2% / test -8.1%，
    val-test 夏普差距的大头是环境切换而非模型退化。
    """
    from sqlalchemy import text
    out = {}
    for name, (s, e) in windows.items():
        rows = db.execute(text(
            "SELECT close FROM index_daily_quote WHERE index_code=:c AND trade_date BETWEEN :s AND :e"
            " AND close IS NOT NULL ORDER BY trade_date"),
            {"c": index_code, "s": s, "e": e}).fetchall()
        if len(rows) >= 2 and float(rows[0][0]) > 0:
            ret = float(rows[-1][0]) / float(rows[0][0]) - 1
            out[name] = {'days': len(rows), 'total_return': round(ret, 4),
                         'annual': round((1 + ret) ** (252 / len(rows)) - 1, 4)}
        else:
            out[name] = {'days': len(rows), 'total_return': 0.0, 'annual': 0.0}
    return out


def compute_gap_decomposition(val_t0, test_t0, test_t1=None, val_t1=None,
                              rank_ic=None, regime=None):
    """val-test 夏普差距三分解：执行口径 / 市场环境 / 模型排序力。

    背景（2026-09-15 v15.0 实测）：Val-Test=2.50 里模型退化只占小头——10d
    差距在 T+1 口径下消失（1.82→-0.27），train 段回测反而是最差窗口（平市
    +成本），RankIC val≈test 无退化。单一 gap 数字无法回答「该改模型还是该
    改口径」，此函数把三块拆开供评估报告/UI 呈现。

    Args:
        val_t0/test_t0: {label: bt dict}（T+0 信号日收盘成交）
        val_t1/test_t1: 同结构（T+1 次日收盘成交，可空）
        rank_ic: {label: {'train','val','test'}}（cross_sectional_rank_ic 产出，可空）
        regime: benchmark_window_stats 产出（可空）
    """
    import numpy as np
    labels = [l for l in ('5d', '10d', '20d') if l in (test_t0 or {})]

    def _ms(d):
        if not d:
            return None
        xs = [d[l]['sharpe'] for l in labels if l in d and d[l].get('sharpe') is not None]
        return round(float(np.mean(xs)), 4) if xs else None

    def _g(a, b):
        return round(a - b, 4) if (a is not None and b is not None) else None

    val_s, test_s = _ms(val_t0), _ms(test_t0)
    val1_s, test1_s = _ms(val_t1), _ms(test_t1)
    per_label = []
    for l in labels:
        g0 = _g((val_t0 or {}).get(l, {}).get('sharpe'), (test_t0 or {}).get(l, {}).get('sharpe'))
        g1 = _g((val_t1 or {}).get(l, {}).get('sharpe'), (test_t1 or {}).get(l, {}).get('sharpe'))
        ic = (rank_ic or {}).get(l, {})
        ic_val, ic_test = ic.get('val'), ic.get('test')
        # 结论归因：T+1 下差距收敛 → 执行口径伪影；RankIC 平移 → 环境主导；否则模型退化
        if g1 is not None and g0 is not None and g1 < max(0.5, g0 * 0.3):
            cause = '执行口径'
        elif ic_val is not None and ic_test is not None and abs(ic_val - ic_test) < 0.015:
            cause = '环境主导'
        else:
            cause = '模型退化'
        per_label.append({'label': l, 'gap_t0': g0, 'gap_t1': g1,
                          'ic_val': ic_val, 'ic_test': ic_test, 'cause': cause})
    return {
        'sharpe_gap_val_test_t0': _g(val_s, test_s),
        'sharpe_gap_val_test_t1': _g(val1_s, test1_s),
        'exec_caliber': {
            't0': {l: (test_t0 or {}).get(l, {}).get('sharpe') for l in labels},
            't1': {l: (test_t1 or {}).get(l, {}).get('sharpe') for l in labels} if test_t1 else None,
        },
        'exec_optimism': {'val': _g(val_s, val1_s), 'test': _g(test_s, test1_s)},
        'regime': regime or {},
        'rank_ic': rank_ic or {},
        'per_label': per_label,
    }


def _purged_time_folds(dates_sorted, folds=4, embargo=25):
    """时间序连续折 + 折前 purge/embargo（按交易日计）。返回 [(fold集, train集)]。

    折 k 的训练侧剔除折开始前 embargo 个交易日——标签最长前瞻 20 交易日，
    不剔除则「右边界样本的标签窗口伸进折内」，折外得分被未来信息污染
    （purged CV 的标准做法，López de Prado 2018）。纯函数供单测。
    """
    ds = list(dates_sorted)
    n = len(ds)
    folds = max(2, min(int(folds), n // 4))
    edges = [i * n // folds for i in range(folds + 1)]
    out = []
    for k in range(folds):
        lo, hi = edges[k], edges[k + 1]
        fold = set(ds[lo:hi])
        train = set(ds[:max(0, lo - int(embargo))]) | set(ds[hi:])
        out.append((fold, train))
    return out


def build_targets(db, df, cfg):
    """构建 5/10/20 日前瞻标签（winsorize + label_transform）。

    absolute 走三重屏障（止盈 +10%/止损 -5%/时间屏障）；excess 为 N 日到期收益 −
    沪深300 同期。抽成模块级函数是为了让 scripts/eval_version.py 复用同一实现——
    两套标签/回测实现漂移过一次（2026-09-11 定位伪回撤时踩到）。
    """
    import pandas as pd
    from sqlalchemy import text
    from datetime import date as _date, timedelta as _td
    # ── 3. 标签：forward N 日收益 ──
    # 批量查询样本区间（+60 自然日缓冲）内全部日线，内存中按股票分组计算，
    # 替代原先逐行 N+1 查询（A2 优化）
    label_mode = cfg.get('label_mode', 'absolute')
    logger.info(f"[train] 计算标签（label_mode={label_mode}）…")
    # absolute（旧口径，默认）: Triple Barrier，止盈 +10%，止损 -5%，时间屏障 = 周期天数
    # excess（v3.5）: N 日到期收益 − 沪深300 同期收益（超额），不做屏障——
    #   模型直接学"相对大盘强弱"，与信号层的截面选择机制对齐
    TAKE_PROFIT = 0.10
    STOP_LOSS = -0.05
    LABEL_DAYS = [5, 10, 20]  # 与 TARGETS 保持一致（A3: 新增 5d 周期）
    _s_min = str(df['trade_date'].min())[:10]
    _s_max = str(df['trade_date'].max())[:10]
    _buf_end = (_date.fromisoformat(_s_max) + _td(days=60)).isoformat()
    codes = df['stock_code'].unique().tolist()

    # 超额模式：沪深300 前瞻 N 日收益（指数自身交易日序列，tdate 缺失时回退前一交易日）
    _idx_fwd = None
    if label_mode == 'excess':
        _idx_rows = db.execute(text(
            "SELECT trade_date, close FROM index_daily_quote WHERE index_code='000300' "
            "AND trade_date BETWEEN :s AND :e ORDER BY trade_date"
        ), {"s": _s_min, "e": _buf_end}).fetchall()
        _idx_dates = [str(r[0])[:10] for r in _idx_rows]
        _idx_close = [float(r[1]) for r in _idx_rows]
        _idx_fwd = lambda tdate, days: index_forward_return(_idx_dates, _idx_close, tdate, days)

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
            if label_mode == 'excess':
                # 超额模式：N 日到期收益 − 沪深300 同期收益，不做屏障
                if len(fwd_win) < days:
                    labels[days].append(None)
                    continue
                ir = _idx_fwd(tdate, days) if _idx_fwd else None
                if ir is None:
                    labels[days].append(None)
                    continue
                labels[days].append(fwd_win[days - 1] / price_today - 1 - ir)
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

    # 标签截面排名化（label_transform=rank，v14 配置驱动）：收益数值噪声大且
    # 模型决策只用排序，标签换为当日全市场百分位（0~1）——损失与决策对齐。
    # 下游全部按排序消费预测分，对预测值量纲透明（原因文案在 signal 端适配）。
    label_transform = cfg.get('label_transform', 'none')
    if label_transform == 'rank':
        for col in ['target_5d', 'target_10d', 'target_20d']:
            df[col] = df.groupby('trade_date')[col].rank(pct=True)
        logger.info('[train] 标签已截面排名化（label_transform=rank）')
    elif label_transform == 'top20':
        # 二分类：名次进前 20% → 1。模型输出"入前20%概率"（有幅度、可比、可做仓位抓手）
        for col in ['target_5d', 'target_10d', 'target_20d']:
            df[col] = (df.groupby('trade_date')[col].rank(pct=True) >= 0.8).astype(int)
        logger.info('[train] 标签已二值化（label_transform=top20，前20%为正类）')
    return df


def run_training_backtest(y_true, y_pred, dates, codes, close_prices, volumes, hold_days,
                          initial_cash=1_000_000, max_pos=5, bt_ver='', bt_label='',
                          stop_loss=None, take_profit=None, commission=None, stamp_tax=None,
                          slippage=None, limit_up=None, limit_down=None, gated_dates=None,
                          dd_gate=None, trailing=None, exec_lag=0):
    import pandas as pd
    import numpy as np
    """回测引擎：资金约束 + 流动性约束 + 整数手约束 + 涨跌停约束。

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
        limit_up/limit_down: 涨停/跌停布尔数组（与 dates 对齐；涨停不买、跌停不卖）
        gated_dates: 空仓信号日集合（YYYY-MM-DD）——该日不开新仓，持仓仍按止损/到期退出
        dd_gate: 组合熔断阈值（如 0.10）——净值较峰值回撤超阈值即停开仓，
                 直至回撤收敛回阈值内（以昨日收盘净值判定，无未来函数）
        trailing: 移动止盈回撤（如 0.08）——启用后替代固定止盈：持仓从持有期
                 峰值回撤超阈值即以峰值×(1-trailing) 退出，不封顶右尾
        exec_lag: 成交时点延迟（0=信号日收盘成交【默认，与既有全部结果一致】；
                 1=次日收盘成交——候选分数取该股前一交易日的预测，模拟
                 "收盘后出信号、次日收盘才可成交"的真实可达性。退出仍在当日
                 收盘（盘中破位 EOD 出场的近似），涨停/流动性约束按成交日判定）
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
        'price': close_prices, 'volume': volumes,
        'limit_up': limit_up if limit_up is not None else False,
        'limit_down': limit_down if limit_down is not None else False,
    })
    if exec_lag:
        # 次日收盘成交：候选分数整体后移一个交易日（该股首日无分数自然不买）
        val_df = val_df.sort_values(['code', 'date']).reset_index(drop=True)
        val_df['pred'] = val_df.groupby('code')['pred'].shift(1)
    sorted_dates = sorted(val_df['date'].unique())

    def _mark_price(h, d):
        """当日估值价：有正报价用当日价并缓存，否则沿用最近有效报价。

        停牌/零价行若直接参与估值，持仓会被按 0 计价——曾造成 -34%/+57%
        成对伪回撤把 max_dd 撑到 47%（2026-09-11 定位）。宁可用最近有效价。
        """
        hday = val_df[(val_df['date'] == d) & (val_df['code'] == h['code'])]
        if not hday.empty:
            px = float(hday['price'].iloc[0])
            if px > 0:      # NaN 比较为 False → 自动走沿用分支
                h['last_price'] = px
                return px
        return h.get('last_price') or h['buy_price']

    def _snap(d):
        """记录当日权益与持仓快照（估值走 _mark_price，停牌日沿用上一价）。"""
        eq = cash + sum(h['shares'] * _mark_price(h, d) for h in holdings)
        equity_curve.append(eq)
        daily_log.append({
            'date': str(d)[:10], 'equity': round(eq, 2), 'cash': round(cash, 2),
            'position_value': round(eq - cash, 2), 'n_positions': len(holdings),
            'holdings': {h['code']: {'shares': int(h['shares']),
                                     'price': round(_mark_price(h, d), 4),
                                     'buy_price': round(h['buy_price'], 4)}
                         for h in holdings},
        })
        return eq

    equity = float(initial_cash)
    cash = float(initial_cash)
    holdings = []  # [{code, buy_price, buy_date, shares}]
    equity_curve = [equity]
    daily_log = []  # 逐日净值 + 持仓快照（落 backtest_daily_records）
    trade_count = 0
    win_count = 0
    trade_log = []  # M6-18: 记录每笔交易明细
    limit_up_blocked = 0   # 涨停拦截的买入候选数
    limit_down_blocked = 0 # 跌停拦截的卖出数
    cost_accum = 0.0       # 累计交易成本（佣金+印花税+冲击成本，口径补齐用）

    for di, d in enumerate(sorted_dates):
        # ── 1. 平仓：到期或止损 ──
        surviving = []
        tr_val = trailing if trailing is not None else 0
        for h in holdings:
            hold_dur = (d - h['buy_date']).days
            # 获取当前价（用最近一日价格近似）
            day_data = val_df[(val_df['date'] == d) & (val_df['code'] == h['code'])]
            if day_data.empty:
                surviving.append(h)
                continue
            cur_price = float(day_data['price'].iloc[0])
            if not (cur_price > 0):   # 含 NaN：停牌/零价行当日无有效报价
                # 估值沿用上一价，且不触发退出——否则会被判成"跌破止损"，
                # 按 buy_price×(1-sl) 假卖一笔
                surviving.append(h)
                continue
            sell_price = cur_price
            should_sell = False

            # 移动止盈（trailing）：先更新峰值再判回撤；启用时固定止盈让位
            if tr_val:
                h['peak'] = max(h.get('peak', h['buy_price']), cur_price)
                if cur_price <= h['peak'] * (1 - tr_val):
                    sell_price = h['peak'] * (1 - tr_val)
                    should_sell = True
            # 止损时卖价 ≈ 止损价
            if cur_price <= h['buy_price'] * (1 - sl_val):
                sell_price = h['buy_price'] * (1 - sl_val)
                should_sell = True
            # 固定止盈（trailing 启用时停用——两者并存时固定止盈会截断右尾）
            if tp_val and not tr_val and cur_price >= h['buy_price'] * (1 + tp_val):
                sell_price = h['buy_price'] * (1 + tp_val)
                should_sell = True
            # 到期平仓
            if hold_dur >= hold_days:
                should_sell = True

            # 跌停不可卖：被迫继续持有（T+1 由"先卖后买、只查隔夜仓"天然保证）
            if should_sell and bool(day_data['limit_down'].iloc[0]):
                should_sell = False
                limit_down_blocked += 1

            if should_sell:
                gross = h['shares'] * sell_price
                sell_cost = gross * (comm_val + st_val) + max(gross * slip_val, 0)
                cost_accum += sell_cost
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
                        pos_value += hh['shares'] * _mark_price(hh, d)
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
        # 组合熔断：昨日收盘净值较峰值回撤超 dd_gate → 停开仓（持仓按止损/到期退出）
        peak_eq = max(equity_curve) if equity_curve else float(initial_cash)
        _dd_gated = (dd_gate is not None and equity_curve
                     and equity_curve[-1] < peak_eq * (1 - dd_gate))
        # 空仓信号日/熔断生效：不开新仓（持仓仍按止损/到期退出），权益记账连续
        if (gated_dates and str(d)[:10] in gated_dates) or _dd_gated:
            equity = _snap(d)
            continue
        day = val_df[val_df['date'] == d].copy()
        # 排除已持仓
        held_codes = {h['code'] for h in holdings}
        day = day[~day['code'].isin(held_codes)]
        # 涨停不可买：候选剔除并计数（执行约束，方向性虚高的主要来源）
        lu_mask = day['limit_up'].astype(bool)
        limit_up_blocked += int(lu_mask.sum())
        day = day[~lu_mask]
        if len(day) == 0:
            equity = _snap(d)   # 无候选日：仅记录权益
            continue

        slots = max_pos - len(holdings)
        if slots <= 0:
            equity = _snap(d)   # 仓位已满：仅记录权益
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
            cost_accum += buy_cost   # 成交成立才计（资金不足被跳过的买单不计成本）

            trade_id = f"T{len(trade_log)+1:04d}"
            pos_value = total_cost
            for hh in holdings:
                pos_value += hh['shares'] * _mark_price(hh, d)
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
                'last_price': price,
                'trade_id': trade_id,
                'signal_source': str(hold_days) + 'd',
            })

        # ── 3. 记录当日权益（无有效报价的持仓沿用最近有效价，不按 0 计）──
        equity = _snap(d)

    # 最终清仓
    last_date = sorted_dates[-1]
    for h in holdings:
        px = _mark_price(h, last_date)
        cash += h['shares'] * px
        trade_count += 1
        if px > h['buy_price']:
            win_count += 1

    total_return = (cash / initial_cash - 1) if initial_cash > 0 else 0
    if limit_up_blocked or limit_down_blocked:
        logger.info(f"[backtest] {bt_ver}/{bt_label} 执行约束拦截: 涨停买入x{limit_up_blocked} 跌停卖出x{limit_down_blocked}")
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
        'trades': trade_log, 'daily': daily_log,
        # 口径补齐：累计成本与相对本金的拖累（只读观测，不改变撮合逻辑）
        'total_cost': round(cost_accum, 2),
        'cost_pct': round(cost_accum / initial_cash, 4) if initial_cash > 0 else 0,
    }


def compute_regime_gates(db, dates, cfg=None):
    """市场择时空仓闸门：指数收盘 < MA(N) 为风险期，风险期连续前 max_skip_days 天不开新仓。

    「不能连续空仓 3 天」约束（2026-09-05 用户拍板）：风险期第 1、2 天禁止买入，
    第 3 天强制放行并把连续计数归零——熊市中呈「禁、禁、买」循环，保留反弹敞口。
    cfg 来自 model_versions.config['regime']：
      {"enabled": true, "index_code": "000300", "ma_window": 20, "max_skip_days": 2}
    cfg 缺 enabled 键视为未启用；指数数据不足 win+1 天时不启用（不误伤）。
    返回 gated 日期字符串集合（这些日期不产生新买入）。streak 沿指数完整交易日轴累计，
    单日调用也能拿到正确的连续计数上下文。
    """
    import pandas as _pd2
    from datetime import datetime as _dtm, timedelta as _tdm
    from sqlalchemy import text as _sqltext
    cfg = cfg or {}
    if not cfg.get('enabled'):
        return set()
    idx_code = cfg.get('index_code', '000300')
    win = int(cfg.get('ma_window', 20) or 20)
    max_skip = int(cfg.get('max_skip_days', 2) or 2)
    ds = sorted({str(d)[:10] for d in (dates if dates is not None else [])})
    if not ds:
        return set()
    start = (_dtm.strptime(ds[0], '%Y-%m-%d') - _tdm(days=win * 3)).isoformat()
    rows = db.execute(_sqltext(
        "SELECT trade_date, close FROM index_daily_quote "
        "WHERE index_code=:c AND trade_date BETWEEN :s AND :e AND close IS NOT NULL "
        "ORDER BY trade_date"
    ), {"c": idx_code, "s": start, "e": ds[-1]}).fetchall()
    if len(rows) < win + 1:
        return set()
    idx = _pd2.DataFrame(rows, columns=['d', 'close'])
    idx['close'] = idx['close'].astype(float)
    idx['ma'] = idx['close'].rolling(win).mean()
    gated_all = set()
    streak = 0
    for r in idx.itertuples(index=False):
        if _pd2.isna(r.ma):
            continue  # MA 未成形期不设闸
        d10 = str(r.d)[:10]
        if r.close < r.ma:
            streak += 1
            if streak <= max_skip:
                gated_all.add(d10)
            else:
                streak = 0  # 第 max_skip+1 天强制放行，连续计数归零
        else:
            streak = 0
    return {d for d in ds if d in gated_all}


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

        # M1-3: 数据新鲜度断言（不允许包含今天或昨天的未收盘数据）
        max_d = str(df['trade_date'].max())[:10]
        cutoff = (_date.today() - _td(days=2)).isoformat()
        if max_d > cutoff:
            write_node_log(log_id=log_id, status='failed', detail=f'数据新鲜度异常: max={max_d} > cutoff={cutoff}')
            db.execute(text("UPDATE model_versions SET status='DRAFT' WHERE version=:v"), {"v": ver})
            db.commit()
            db.close(); return 0

        update_node_progress(log_id=log_id, rows=1, detail='步骤1:加载特征')

        # ── 2. 特征工程 + 标签：prepare_model_frame 与 eval_version.py 共用 ──
        update_node_progress(log_id=log_id, rows=2, detail='步骤2:特征工程')
        df, FEATURES = prepare_model_frame(db, df, cfg, feature_names, data_start, end_date)

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
        # 训练目标（v14 配置驱动）：regression=XGBRegressor 回归；
        # pairwise=XGBRanker rank:pairwise 按交易日分组学习排序——评估指标从 r²
        # 改用预测分与标签的秩相关（Spearman，按字段名 r2 落库沿用 trial 记录结构）
        train_objective = cfg.get('train_objective', 'regression')

        def _fit_model(params, X, y, dates_of_X, X_es=None, y_es=None):
            """按 train_objective 训练单周期模型，返回 (model, score_on_val 用 .score 或另行计算)。"""
            if train_objective == 'binary':
                from xgboost import XGBClassifier
                bp = dict(params); bp['objective'] = 'binary:logistic'
                if X_es is not None:
                    m = XGBClassifier(**bp, early_stopping_rounds=20, eval_metric='logloss')
                    m.fit(X, y, eval_set=[(X_es, y_es)], verbose=False)
                else:
                    m = XGBClassifier(**bp)
                    m.fit(X, y)
                return m
            if train_objective == 'pairwise':
                from xgboost import XGBRanker
                import itertools as _it
                order = np.argsort(dates_of_X, kind='stable')
                X_o, y_o = X[order], np.asarray(y)[order]
                d_o = dates_of_X[order]
                groups = np.array([len(g) for _, g in _it.groupby(d_o)], dtype=np.uint32)
                m = XGBRanker(objective='rank:pairwise', **params)
                m.fit(X_o, y_o, group=groups)
                return m
            from xgboost import XGBRegressor
            if X_es is not None:
                m = XGBRegressor(**params, early_stopping_rounds=20)
                m.fit(X, y, eval_set=[(X_es, y_es)], verbose=False)
            else:
                m = XGBRegressor(**params)
                m.fit(X, y)
            return m

        update_node_progress(log_id=log_id, rows=3, detail=f'Optuna实验:0/{n_trials} 开始搜索')

        # ── 回测引擎（模块四：资金管理 + 持仓 + 止损 + T+1）──
        initial_cash = cfg.get('initial_cash', 1000000)
        max_pos = cfg.get('max_positions', 5)
        stop_loss = cfg.get('risk', {}).get('stop_loss_pct', 8) / 100.0
        stamp_tax = cfg.get('stamp_tax', 0.001)
        commission = cfg.get('commission', 0.00025)
        slippage = cfg.get('slippage', 0.001)


        best_models = {}
        best_params_store = {}
        best_score = -999
        trial_records = []
        val_dates = df[val_mask]['trade_date'].values
        val_codes = df[val_mask]['stock_code'].values
        val_close = df[val_mask]['close'].values
        val_volume = df[val_mask]['volume'].values
        TARGETS = [('5d','target_5d',5), ('10d','target_10d',10), ('20d','target_20d',20)]

        # ── 超参选择协议（v3.9.2 可配）──
        # purged CV：折外均值替代单 val 得分——消除「N 组在 val 上选最优」的赢者
        # 诅咒（v13/v16 同协议旁证约 0.3-0.5 的 gap 通胀）；启用后 val 完全不参与
        # 选择，成为诚实 OOS。折按交易日连续切，折前 embargo ≥ 标签最长前瞻。
        _cv_cfg = cfg.get('selection_cv') or {}
        _use_cv = bool(_cv_cfg.get('enabled')) and int(_cv_cfg.get('folds', 4)) >= 2
        _cv_folds = []
        _tr_dates_all = df[train_mask]['trade_date'].values
        train_n = int(len(X_train) * 0.9)
        X_tr_h, X_es_h = X_train[:train_n], X_train[train_n:]
        if _use_cv:
            _fold_pairs = _purged_time_folds(sorted(set(_tr_dates_all)),
                                             folds=_cv_cfg.get('folds', 4),
                                             embargo=_cv_cfg.get('embargo_days', 25))
            for fold_dates, tr_dates in _fold_pairs:
                _cv_folds.append((np.where(np.isin(_tr_dates_all, list(fold_dates)))[0],
                                  np.where(np.isin(_tr_dates_all, list(tr_dates)))[0]))
            if n_trials > 30:
                logger.warning(f'[train] purged CV ×{len(_cv_folds)} 折已启用，{n_trials} trials '
                               f'拟合次数约 ×{len(_cv_folds)}，建议 optuna_trials ≤ 20')
            update_node_progress(log_id=log_id, rows=1,
                                 detail=f'超参选择：purged CV {len(_cv_folds)} 折'
                                        f'（embargo {_cv_cfg.get("embargo_days", 25)} 日），val 不参与选择')

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
                _tr_dates = df[train_mask]['trade_date'].values
                for label, tname, hdays in TARGETS:
                    if _use_cv:
                        # 折外均值：每折在剔除泄漏窗（折前 embargo 日）的剩余训练数据上
                        # 拟合、折内打分；模型不在此落——选出最优超参后统一重拟合
                        y_all = df[train_mask][tname].values
                        fold_r2 = []
                        for fold_idx, tr_idx in _cv_folds:
                            # X_train 是 DataFrame（非连续行集必须 .iloc，方括号会变成列查找）
                            fm = _fit_model(params, X_train.iloc[tr_idx], y_all[tr_idx],
                                            _tr_dates[tr_idx] if train_objective == 'pairwise' else None)
                            fold_r2.append(val_score(fm, X_train.iloc[fold_idx], y_all[fold_idx], train_objective))
                        r2 = float(np.mean(fold_r2))
                        models[label] = {'model': None, 'r2': r2}
                    else:
                        if train_objective == 'pairwise':
                            model = _fit_model(params, X_train, df[train_mask][tname], _tr_dates)
                        else:
                            model = _fit_model(params, X_tr, Y_tr[tname], None, X_es, Y_es[tname])
                        r2 = val_score(model, X_val, df[val_mask][tname], train_objective)
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
            _tr_dates2 = df[train_mask]['trade_date'].values
            for label, tname, hdays in TARGETS:
                model = _fit_model(params, X_train, df[train_mask][tname], _tr_dates2)
                r2 = val_score(model, X_val, df[val_mask][tname], train_objective)
                best_models[label] = model
                best_params_store[label] = {'params': params, 'r2': r2}
            update_node_progress(log_id=log_id, rows=1, detail='训练完成(无Optuna)')

        # Optuna 全部 trial 因剪枝/异常未产出模型 → 收尾
        if not best_models:
            try: db.close()
            except Exception: pass
            _abort('训练未产生有效模型（所有 trial 失败或已终止）')
            return 0

        # ── CV 模式：最优超参在完整训练窗统一重拟合（trial 内只拟合折模型）──
        if _use_cv and best_params_store:
            update_node_progress(log_id=log_id, rows=3, detail='CV 选出超参，全训练窗重拟合')
            for label, tname, hdays in TARGETS:
                if label not in best_params_store:
                    continue
                p = dict(best_params_store[label]['params'])
                if train_objective == 'pairwise':
                    model = _fit_model(p, X_train, df[train_mask][tname], _tr_dates_all)
                else:
                    model = _fit_model(p, X_tr_h, df[train_mask][tname].values[:train_n], None,
                                       X_es_h, df[train_mask][tname].values[train_n:])
                best_models[label] = model

        # ── 多种子集成（可选，cfg.ensemble.seeds>1）：同一最优超参 × N 个
        #    random_state 拟合、预测取均值——单模型种子噪声被平均，val-test
        #    差距的方差成分收窄。SeedEnsemble 对 pred_score/信号链路透明，
        #    pkl 仍是一个文件。──
        _n_seeds = int((cfg.get('ensemble') or {}).get('seeds') or 1)
        if _n_seeds > 1 and best_models:
            update_node_progress(log_id=log_id, rows=3, detail=f'种子集成拟合 ×{_n_seeds}')
            for label, tname, hdays in TARGETS:
                if label not in best_params_store or label not in best_models:
                    continue
                base = dict(best_params_store[label]['params'])
                members = []
                for s in range(_n_seeds):
                    p = {**base, 'random_state': 42 + s}
                    if train_objective == 'pairwise':
                        members.append(_fit_model(p, X_train, df[train_mask][tname], _tr_dates_all))
                    else:
                        members.append(_fit_model(p, X_tr_h, df[train_mask][tname].values[:train_n],
                                                  None, X_es_h, df[train_mask][tname].values[train_n:]))
                best_models[label] = seed_ensemble(members)
                best_params_store[label]['ensemble_seeds'] = _n_seeds
                best_params_store[label]['r2'] = val_score(
                    best_models[label], X_val, df[val_mask][tname], train_objective)

        # ── 最终评估：val + test 集回测（val 用于过拟合对比，test 为最终成绩）──
        update_node_progress(log_id=log_id, rows=4, detail='回测评估 (val+test)…')
        test_dates = df[test_mask]['trade_date'].values
        test_codes = df[test_mask]['stock_code'].values
        test_close = df[test_mask]['close'].values
        test_volume = df[test_mask]['volume'].values
        test_idx_ret = df[test_mask]['idx_ret_20d'].values

        # 空仓闸门（regime）+ 组合熔断（portfolio_gate）：训练评估与实盘信号同一规则
        _regime_cfg = cfg.get('regime') or {}
        pdd_gate = (cfg.get('portfolio_gate') or {}).get('dd')
        trail_val = float((cfg.get('risk', {}) or {}).get('trailing_retracement') or 0)
        regime_gates = compute_regime_gates(db, df['trade_date'].unique(), _regime_cfg)
        if _regime_cfg.get('enabled'):
            update_node_progress(log_id=log_id, rows=4,
                                 detail=f'空仓闸门已启用（指数<{_regime_cfg.get("ma_window", 20)}日线不开新仓，{len(regime_gates)} 个风险日）')

        def _run_backtests(mask, exec_lag=0, with_r2=True):
            """对给定切分跑全部周期的回测（exec_lag=1 为次日收盘成交口径），附带 test R²。"""
            res = {}
            dates = df[mask]['trade_date'].values
            codes = df[mask]['stock_code'].values
            closes = df[mask]['close'].values
            vols = df[mask]['volume'].values
            lups = df[mask]['_limit_up'].values
            ldowns = df[mask]['_limit_down'].values
            for label, tname, hdays in TARGETS:
                if label in best_models:
                    y_pred = pred_score(best_models[label], df[mask][FEATURES].values)
                    bt = run_training_backtest(df[mask][tname].values, y_pred, dates, codes, closes, vols,
                                   hdays, initial_cash=initial_cash, max_pos=max_pos,
                                   bt_ver=ver, bt_label=label,
                                   stop_loss=stop_loss, take_profit=stop_loss*2,
                                   commission=commission, stamp_tax=stamp_tax, slippage=slippage,
                                   limit_up=lups, limit_down=ldowns,
                                   gated_dates=regime_gates, dd_gate=pdd_gate,
                                   trailing=trail_val, exec_lag=exec_lag)
                    if with_r2:
                        bt['r2'] = val_score(best_models[label], df[mask][FEATURES].values, df[mask][tname], train_objective)
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

        # ── 差距分解（v3.9.2）：T+1 双口径 + RankIC 三窗 + 基准环境 ──
        # 让 overfit_gap 可回答「多少是口径、多少是环境、多少是模型」——单一
        # gap 数字曾把 2.50 的测量伪影误读成模型过拟合（2026-09-15 定位）。
        # cfg.report.exec_lag=false 可关（省 ~2/5 评估时长）。
        _report_lag = (cfg.get('report') or {}).get('exec_lag', True)
        test_results_t1 = _run_backtests(test_mask, exec_lag=1, with_r2=False) if _report_lag else None
        val_results_t1 = _run_backtests(val_mask, exec_lag=1, with_r2=False) if _report_lag else None
        update_node_progress(log_id=log_id, rows=4, detail='差距分解（RankIC + 环境）…')
        _rank_ic = {}
        for label, tname, hdays in TARGETS:
            if label in best_models:
                _p_full = pred_score(best_models[label], df[FEATURES].values)
                _rank_ic[label] = {
                    'train': cross_sectional_rank_ic(_p_full[train_mask.values], df[train_mask][tname].values, _tr_dates_all),
                    'val': cross_sectional_rank_ic(_p_full[val_mask.values], df[val_mask][tname].values, df[val_mask]['trade_date'].values),
                    'test': cross_sectional_rank_ic(_p_full[test_mask.values], df[test_mask][tname].values, df[test_mask]['trade_date'].values),
                }
        _bench = benchmark_window_stats(db, {
            'train': (str(cfg.get('train_start', ''))[:10], str(train_cut)[:10]),
            'val': (str(train_cut)[:10], str(test_cut)[:10]),
            'test': (str(test_cut)[:10], str(dates[-1])[:10]),
        })
        _gap = compute_gap_decomposition(val_results, test_results, test_results_t1,
                                         val_results_t1, _rank_ic, _bench)
        logger.info(f'[train] 差距分解: gap(T+0)={_gap["sharpe_gap_val_test_t0"]} '
                    f'gap(T+1)={_gap["sharpe_gap_val_test_t1"]} '
                    + ' '.join(f"{p['label']}:{p['cause']}" for p in _gap['per_label']))

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
            # 口径补齐：成本拖累 + 样本域（评估数字脱离口径没有可比性）
            'cost_total': round(sum(test_results[l].get('total_cost', 0) for l in _labels), 2),
            'cost_pct_avg': round(float(np.mean([test_results[l].get('cost_pct', 0) for l in _labels])), 4) if _labels else 0,
            # 差距分解（v3.9.2）：口径/环境/模型三成分 + T+1 口径 gap
            'gap_decomposition': _gap,
            'overfit_gap_t1': _gap.get('sharpe_gap_val_test_t1'),
            'sample_domain': {
                'entity': cfg.get('entity', 'stock'),
                'windows': {'train': [str(cfg.get('train_start', ''))[:10], str(train_cut)[:10]],
                            'val': [str(train_cut)[:10], str(test_cut)[:10]],
                            'test': [str(test_cut)[:10], str(test_dates[-1])[:10] if len(test_dates) else None]},
                'exec_timing': '收盘 T+0（信号日收盘成交）',
                'costs': {'commission': commission, 'stamp_tax': stamp_tax, 'slippage': slippage},
            },
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

        # 逐日净值 + 持仓快照（幂等：先删该版本旧记录）：出现伪回撤时可回溯到"哪天、哪只持仓估错"
        db.execute(text("DELETE FROM backtest_daily_records WHERE version=:v"), {"v": ver})
        for _lbl, _bt in test_results.items():
            for _rec in _bt.get('daily', []):
                db.execute(text("""
                    INSERT INTO backtest_daily_records
                    (version, label, trade_date, equity, cash, position_value, n_positions, holdings)
                    VALUES (:v,:l,:d,:eq,:ca,:pv,:n,:h)
                    ON CONFLICT (version, label, trade_date) DO NOTHING
                """), {"v": ver, "l": _lbl, "d": _rec['date'], "eq": _rec['equity'],
                       "ca": _rec['cash'], "pv": _rec['position_value'],
                       "n": _rec['n_positions'], "h": _json.dumps(_rec['holdings'], ensure_ascii=False)})

        db.execute(text("UPDATE model_versions SET status='PENDING', best_params=:bp, evaluation_report=:rep, sharpe=:sh, win_rate=:wr, max_drawdown=:md, annual_return=:ar WHERE version=:v"), {
            "v": ver,
            "bp": _json.dumps(best_params),
            "rep": _json.dumps(bt_summary),
            "sh": round(avg_sharpe, 4),
            "wr": round(avg_win, 4),
            "md": round(abs(max_dd_avg), 4),
            "ar": round(annual_return, 4),
        })
        db.commit()
        # 血缘台账：训练事件（模型 ↔ 数据窗快照绑定——训练后数据再被修复/重算时，
        # /api/lineage/model/{ver} 可直接给出漂移清单）
        try:
            from app.lineage import log_event
            log_event(db, 'train', ver,
                      scope=f"{cfg.get('train_start','?')}~{str(test_end)[:10]} 训练{str(train_cut)[:10]}~{str(test_cut)[:10]}",
                      detail={'train_start': cfg.get('train_start'),
                              'train_cut': str(train_cut)[:10], 'test_cut': str(test_cut)[:10],
                              'test_end': str(test_end)[:10],
                              'n_features': len(FEATURES), 'n_rows': int(len(df)),
                              'label_transform': cfg.get('label_transform', 'none'),
                              'sharpe': round(float(avg_sharpe), 4),
                              'max_dd': round(float(abs(max_dd_avg)), 4),
                              'annual_return': round(float(annual_return), 4),
                              'trials': len(trial_records)})
        except Exception:
            pass
        db.close()
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

def dag_task_analyze(trade_date=None, **kw):
    """DAG 节点：刷新大表统计信息（ANALYZE）。

    特征/行情/资金流当日写入后刷新 planner 统计，避免分区大表因统计过期
    走错执行计划（2026-09 分区改造后实测：无统计时数据页查询计划盲选）。"""
    log_id = (kw.get('_node_log_ids', {}) or {}).get('analyze')
    write_node_log(log_id=log_id, status='running', detail='ANALYZE 大表…')
    def _run():
        from app.db.connection import get_sync_db
        from sqlalchemy import text
        db = get_sync_db()
        done = []
        for tbl in ('feature_values', 'daily_quote', 'stock_moneyflow'):
            db.execute(text(f'ANALYZE {tbl}'))
            done.append(tbl)
        db.close()
        return {'tables': done}
    try:
        r = _with_hb(log_id, _rid(kw), _run)
        write_node_log(log_id=log_id, status='success',
                       detail='已刷新统计: ' + ', '.join(r.get('tables', [])))
        return r
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e))
        raise


def dag_task_factor_heal(trade_date=None, **kw):
    """DAG 节点：复权因子一致性自愈（tushare 半修正快照 → 假跳变 → 静默污染）。

    tushare 会**回溯性重定基** adj_factor 且分批落地：采集恰逢半修正窗口时，边界前
    存旧基准、边界后存新基准，跨界产生 ×1.2~×3 假后复权跳变（原始价不动）。
    2026-07-01 批次有 911 只，曾在回测里制造单日 +19.5% 假收益并触发假止损。

    流程：近 7 日检测（hfq 收益越界而原始价正常）→ 按代码拉官方当前因子重写
    close_hfq → 重算污染窗 [边界, +90 天] 特征。跑在 kline 之后（配额基本未动）、
    feature_compute 之前（避免两边并发写 feature_values）。

    **设计上不阻断流程**：这是数据卫生步骤，不是当日数据生产步骤；异常只记录并
    在节点 detail 里可见，不让下游因为一次自愈失败而整条跳过。超限/配额不足时
    只告警不改（剩余部分下次运行继续，检测幂等）。
    """
    from datetime import date; td = trade_date or str(date.today()); rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('factor_heal')
    write_node_log(log_id=log_id, status='running', detail='检测因子一致性')

    def _run():
        from app.db.connection import get_sync_db
        from scripts.repair_adj_factor import check_and_heal
        db = get_sync_db()

        def _hb(done, total, note):
            update_node_progress(log_id=log_id, rows=done, detail=f'因子自愈 {done}/{total}: {note}')

        try:
            h = check_and_heal(db, days=7, progress_cb=_hb)
        finally:
            db.close()
        return {'_heal': h}

    try:
        r = _with_hb(log_id, rid, _run)
        fatal = r.get('fatal', '')
        h = r.get('_heal') or {}
        if fatal:
            write_node_log(log_id=log_id, status='failed', detail=f'失败: {fatal}')
        elif not h.get('detected'):
            write_node_log(log_id=log_id, status='success', rows=0, detail='无假跳变，跳过')
        elif h.get('skipped_quota'):
            write_node_log(log_id=log_id, status='success', rows=h.get('detected', 0),
                           detail=f"检出 {h['detected']} 只但超限/配额不足，未自动修"
                                  f"（人工跑 scripts/repair_adj_factor.py）")
        else:
            write_node_log(log_id=log_id, status='success', rows=h.get('rows', 0),
                           detail=f"自愈完成: 检出{h['detected']}只 改写{h.get('healed', 0)}只"
                                  f" 特征{h.get('feature_rows', 0):,}行"
                                  + (f" 失败{h['failed']}只" if h.get('failed') else ''))
        return r
    except Exception as e:
        # 卫生步骤不阻断流程：记日志 + 节点标记失败便于发现，但不 raise
        logger.warning(f'[factor_heal] 自愈异常（不阻断下游）: {e}')
        write_node_log(log_id=log_id, status='failed', detail=f'自愈异常: {str(e)[:150]}')
        return {'rows': 0, '_heal': {'error': str(e)[:200]}}


def dag_task_rolling_retrain(trade_date=None, dry_run=False, **kw):
    """DAG 节点：模型新鲜度守护（陈旧告警 + 滚动重训）。

    背景：模型曾经 8 个月未重训且无人察觉（2026-09-11 评估缺陷之一），重训
    全靠人想起来手动点。本节点每日随流程跑：
    ① 陈旧告警——ACTIVE.trained_at 距今超 warn_days 写 risk_alerts
       （kind='model_stale'）：复用风控告警通道，WS 轮询器自动推前端铃铛 +
       Chrome 通知，同日同因去重。
    ② 滚动重训——超 alert_days 且无在途候选（TRAINING / 更新的 DRAFT）、
       且距上次自动重训超 cooldown_days 时：克隆 ACTIVE 配置建新版本并后台
       起训（与 /train 端点同一入口 start_training_background，进度/停止
       行为一致）。产物只落 DRAFT——激活永远走人工 promote 闸门，绝不自动
       上线（v13/v16 的教训：未过人工评估的模型不能碰实盘信号）。
    参数在 strategy_config.model_freshness：
       {warn_days:21, alert_days:30, cooldown_days:14, retrain_enabled:true,
        last_auto_at(内部记录，勿手改)}。
    dry_run=True 只输出决策不落库不起训（自测用）。
    **不阻断流程**：运维步骤，异常只记日志。
    """
    from datetime import date, datetime
    import json as _json2
    from sqlalchemy import text
    from app.db.connection import get_sync_db
    td = trade_date or str(date.today())
    log_id = (kw.get('_node_log_ids', {}) or {}).get('rolling_retrain')
    if log_id:
        write_node_log(log_id=log_id, status='running', detail='检查模型新鲜度')
    db = get_sync_db()
    try:
        row = db.execute(text(
            "SELECT params FROM strategy_config WHERE strategy_name='model_freshness'"
        )).fetchone()
        cfg = (_json2.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})) if row else {}
        warn_days = int(cfg.get('warn_days', 21))
        alert_days = int(cfg.get('alert_days', 30))
        cooldown = int(cfg.get('cooldown_days', 14))
        retrain_enabled = bool(cfg.get('retrain_enabled', True))

        act = db.execute(text(
            "SELECT version, trained_at FROM model_versions WHERE status='ACTIVE'"
        )).fetchone()
        if not act:
            detail = '无 ACTIVE 模型，跳过'
            if log_id:
                write_node_log(log_id=log_id, status='success', detail=detail)
            return {'skip': detail}
        ver, trained_at = act[0], act[1]
        age = (datetime.now() - trained_at).days if trained_at else 9999

        actions, blockers = [], []
        # ① 陈旧告警（写 risk_alerts → 既有 WS 轮询推送，前端零改动）
        if age >= warn_days:
            level = 'critical' if age >= alert_days else 'warn'
            title = f'模型陈旧：{ver} 已 {age} 天未重训'
            body = (f'ACTIVE 模型 {ver} 训练于 {trained_at:%Y-%m-%d}，距今 {age} 天'
                    f'（提醒阈值 {warn_days} 天 / 自动重训阈值 {alert_days} 天）。'
                    f'排序模型会随市场风格漂移衰减，请到模型页评估新候选或手动重训。')
            if dry_run:
                actions.append(f'[dry] 将发告警「{title}」({level})')
            else:
                from app.risk import _insert_alert
                if _insert_alert(db, td, 'model_stale', level, title, body):
                    db.commit()
                    actions.append('陈旧告警已发')
                else:
                    actions.append('今日告警已存在(去重)')
        # ② 滚动重训判定
        if age >= alert_days and retrain_enabled:
            busy = db.execute(text(
                "SELECT version FROM model_versions WHERE status='TRAINING'"
            )).fetchall()
            if busy:
                blockers.append(f'训练中({",".join(b[0] for b in busy)})')
            newer = db.execute(text(
                "SELECT version FROM model_versions WHERE status='DRAFT' AND trained_at > :t"
            ), {"t": trained_at}).fetchall()
            if newer:
                blockers.append(f'待审候选({",".join(b[0] for b in newer)})')
            la = cfg.get('last_auto_at')
            if la:
                try:
                    if (datetime.now() - datetime.fromisoformat(str(la))).days < cooldown:
                        blockers.append(f'冷却中(上次自动重训 {str(la)[:10]}，周期 {cooldown} 天)')
                except ValueError:
                    pass
            if not blockers and dry_run:
                actions.append('[dry] 条件满足，将克隆 ACTIVE 自动起训')
            elif not blockers:
                # 克隆 ACTIVE 配置 → 新版本号（数值最大主版本 +1，字符串 MAX 会 v9>v10）
                cfg_row = db.execute(text(
                    "SELECT config FROM model_versions WHERE version=:v"), {"v": ver}).fetchone()
                src_cfg = cfg_row[0] if isinstance(cfg_row[0], dict) else _json2.loads(cfg_row[0])
                # 旧 ACTIVE（v3.9.2 前）无训练协议键 → 补默认（purged CV + 集成），
                # 滚动重训自动用新协议；显式关闭的配置不被覆盖
                src_cfg = with_training_protocol_defaults(src_cfg)
                majors = []
                for (v,) in db.execute(text("SELECT version FROM model_versions")).fetchall():
                    try:
                        majors.append(int(str(v).lstrip('v').split('.')[0]))
                    except (ValueError, IndexError):
                        continue
                new_ver = f"v{max(majors) + 1}.0" if majors else 'v1.0'
                db.execute(text(
                    "INSERT INTO model_versions (version, model_name, status, config) "
                    "VALUES (:v, :n, 'DRAFT', :c)"),
                    {"v": new_ver, "n": f'滚动重训·{ver}克隆',
                     "c": _json2.dumps(src_cfg, ensure_ascii=False, default=str)})
                # last_auto_at 记进配置行（冷却期判定依据；strategy_config 无唯一约束，删插）
                cfg['last_auto_at'] = datetime.now().isoformat(timespec='seconds')
                db.execute(text(
                    "DELETE FROM strategy_config WHERE strategy_name='model_freshness'"))
                db.execute(text(
                    "INSERT INTO strategy_config (strategy_name, display_name, enabled, params) "
                    "VALUES ('model_freshness', '模型新鲜度守护', true, :p)"),
                    {"p": _json2.dumps(cfg, ensure_ascii=False)})
                db.commit()
                # 与 /train 端点同一入口：TaskManager 任务 + 后台线程 + WS 进度广播
                from app.api.models import start_training_background
                res = start_training_background(new_ver)
                if res.get('ok'):
                    actions.append(f'已触发自动重训 {new_ver}（任务 {str(res.get("task_id"))[:8]}…，'
                                   f'完成后待人工评估激活）')
                    logger.info(f'[rolling_retrain] 自动重训已触发: {new_ver} (克隆 {ver}, AGE={age}天)')
                    try:
                        from app.lineage import log_event
                        log_event(db, 'rolling_retrain', new_ver,
                                  scope=f'克隆 {ver}（ACTIVE 已训 {age} 天）',
                                  detail={'clone_of': ver, 'age_days': age,
                                          'task_id': res.get('task_id')})
                    except Exception:
                        pass
                else:
                    actions.append(f'起训失败: {res.get("error")}')
        detail = f'ACTIVE {ver} 已训 {age} 天（阈值 提醒{warn_days}/重训{alert_days}）'
        if actions:
            detail += '；' + '；'.join(actions)
        if blockers:
            detail += '；重训跳过: ' + '；'.join(blockers)
        if log_id:
            write_node_log(log_id=log_id, status='success', detail=detail)
        logger.info(f'[rolling_retrain] {detail}')
        return {'version': ver, 'age': age, 'actions': actions, 'blockers': blockers}
    except Exception as e:
        logger.warning(f'[rolling_retrain] 异常（不阻断下游）: {e}')
        if log_id:
            write_node_log(log_id=log_id, status='failed', detail=f'异常: {str(e)[:150]}')
        return {'error': str(e)[:200]}
    finally:
        db.close()


def dag_task_margin_daily(trade_date=None, **kw):
    """DAG 节点：两融余额采集 + 历史对齐补数（独立流程专用，建议 18:00 触发）。

    tushare margin_detail 按交易日全市场、1 次调用/天；盘后 ~17:30 才发布当日
    ——主流程 17:00 拉不到，曾致 09-03 起连续空窗且节点静默 success 0 行
    （2026-09-16 定位）。本节点不依赖任何上游（特征未引用两融字段）：

    ① 对齐补数：按 trade_calendar 找 [start_date, 当日] 全部缺失交易日，
       最旧优先补齐——与其它数据时间线对齐，天然断点续跑（缺失驱动、幂等）
    ② 配额护栏：余量 ≤ reserve 即收工，剩余次日继续
    ③ 单次上限 max_days_per_run（防一次跑干配额）
    参数在 strategy_config.margin_collect：
       {start_date:'2014-01-01', reserve:1500, max_days_per_run:2500}
    长跑有进度心跳（每 25 天上报），不会触发看门狗误杀。
    """
    from datetime import date; td = str(trade_date or date.today())[:10]; rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('margin_daily')
    write_node_log(log_id=log_id, status='running', detail='检查缺失交易日',
                   trade_date=td, node_name='margin_daily', run_id=rid)

    def _run():
        import json as _json2
        from sqlalchemy import text as _t2
        from app.db.connection import get_sync_db
        from crawler.adapters import get_data_source_manager
        from crawler.adapters.tushare_quota import TushareQuota
        from crawler.writers import batch_upsert_margin_detail
        db = get_sync_db()
        try:
            row = db.execute(_t2(
                "SELECT params FROM strategy_config WHERE strategy_name='margin_collect'"
            )).fetchone()
            cfg = (_json2.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})) if row else {}
            start = str(cfg.get('start_date', '2014-01-01'))[:10]
            reserve = int(cfg.get('reserve', 1500))
            max_days = int(cfg.get('max_days_per_run', 2500))
            missing = [str(r[0])[:10] for r in db.execute(_t2("""
                SELECT cal_date FROM trade_calendar tc
                WHERE tc.is_trade_day AND tc.cal_date BETWEEN :s AND :e
                  AND NOT EXISTS (SELECT 1 FROM stock_margin_detail m
                                  WHERE m.trade_date = tc.cal_date)
                ORDER BY cal_date
            """), {"s": start, "e": td}).fetchall()]
            # 当日可能不在日历（未同步）——只要缺就补上
            if td not in missing and not db.execute(
                    _t2("SELECT 1 FROM stock_margin_detail WHERE trade_date=:d LIMIT 1"),
                    {"d": td}).fetchone():
                missing.append(td)
            if not missing:
                return {'done': 0, 'saved': 0, 'missing': 0, 'stop': ''}
            quota = TushareQuota.get()
            source = get_data_source_manager().get_source()
            done = saved = 0
            today_got = 0
            stop = ''
            for d in missing:
                if done >= max_days:
                    stop = f'达单次上限 {max_days} 天'
                    break
                if quota.remaining() <= reserve:
                    stop = f'配额余 {quota.remaining()} ≤ 保留 {reserve}'
                    break
                n = batch_upsert_margin_detail(db, source.fetch_margin_detail_ext(d))
                saved += n
                done += 1
                if d == td:
                    today_got = n
                if done % 25 == 0:
                    update_node_progress(log_id=log_id, rows=done,
                                         detail=f'两融补数 {done}/{len(missing)} 天（至 {d}）')
            return {'done': done, 'saved': saved, 'missing': len(missing),
                    'stop': stop, 'today_got': today_got}
        finally:
            db.close()

    try:
        r = _with_hb(log_id, rid, _run, td=td, nn='margin_daily')
        fatal = r.get('fatal', '')
        if fatal:
            write_node_log(log_id=log_id, status='failed', detail=f'失败: {fatal}')
        else:
            note = f"补数 {r.get('done', 0)}/{r.get('missing', 0)} 天 {r.get('saved', 0):,} 行"
            if r.get('stop'):
                note += f"（{r['stop']}，剩余次日继续）"
            # rows 记数据行数（此前误记天数：2500 天显示成"行数 2500"）；当日
            # tushare 尚未发布（today_got=0 且今日在缺失清单里）时明确说明——
            # 是时序不是故障，下次运行自动回补
            if r.get('done', 0) and not r.get('today_got'):
                note += '；当日 tushare 尚未发布，下次运行自动回补'
            write_node_log(log_id=log_id, status='success',
                           rows=r.get('saved', 0), detail=note,
                           trade_date=td, node_name='margin_daily', run_id=rid)
        return r
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e)[:150],
                       trade_date=td, node_name='margin_daily', run_id=rid)
        raise


def dag_task_moneyflow(trade_date=None, **kw):
    """DAG 节点：资金流向（tushare moneyflow 全市场，写 stock_moneyflow）。"""
    from datetime import date; td = trade_date or str(date.today()); rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('moneyflow')
    write_node_log(log_id=log_id, status='running', detail='采集中')
    def _run():
        from crawler.adapters import get_data_source_manager
        from app.db.connection import get_sync_db
        from crawler.writers import batch_upsert_moneyflow
        source = get_data_source_manager().get_source()
        rows = source.fetch_moneyflow(td)
        db = get_sync_db()
        saved = batch_upsert_moneyflow(db, rows)
        db.close()
        return {'rows': saved, '_source': source.name}
    try:
        r = _with_hb(log_id, rid, _run)
        rows = r.get('rows', 0)
        write_node_log(log_id=log_id, status='success', rows=rows,
                       detail=f'完成 {rows} 行 (来源:{r.get("_source", "?")})')
        return r
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e))
        raise



# ── 拓展数据采集器（每日节点与 data_backfill 回补共用，step2 扩展）──

def _ext_moneyflow(source, db, td):
    from crawler.writers import batch_upsert_moneyflow
    return batch_upsert_moneyflow(db, source.fetch_moneyflow(td))

def _ext_backfill_recent(source, db, td, table, fetch, writer, lookback=20):
    """盘后晚发布数据的自愈采集：当日拉空则回看近 lookback 个自然日补缺失日。

    背景（2026-09-16 定位）：两融/龙虎榜 tushare 盘后 ~17:30 才发布当日，
    每日流程 17:00 跑时天天拉空，且从不回捞——margin_detail 从 09-03 起、
    top_list 从 09-10 起连续空窗。幂等：已有数据的日子直接跳过（0 配额），
    当日总是尝试（upsert 幂等）；非交易日接口返回空，无害。
    返回 (总写入行数, 当日是否拉到数据)——当日空是正常时序（次日回补），
    调用方需要区分「无事可做」和「当日暂缺」。
    """
    from datetime import date as _d, timedelta as _td
    from sqlalchemy import text as _t
    try:
        base = _d.fromisoformat(str(td)[:10])
    except ValueError:
        base = _d.today()
    total = 0
    today_fetched = 0
    for k in range(lookback + 1):
        d = (base - _td(days=k)).isoformat()
        if k > 0:
            if db.execute(_t(f"SELECT 1 FROM {table} WHERE trade_date=:d LIMIT 1"),
                          {"d": d}).fetchone():
                continue
        n = writer(db, fetch(d))
        total += n
        if k == 0:
            today_fetched = n
    return total, today_fetched


def _ext_top_list(source, db, td):
    from crawler.writers import batch_upsert_top_list
    return _ext_backfill_recent(source, db, td, 'stock_top_list',
                                source.fetch_top_list, batch_upsert_top_list)[0]

def _ext_margin_detail(source, db, td):
    from crawler.writers import batch_upsert_margin_detail
    return _ext_backfill_recent(source, db, td, 'stock_margin_detail',
                                source.fetch_margin_detail_ext, batch_upsert_margin_detail)[0]

def _ext_moneyflow_hsgt(source, db, td):
    from crawler.writers import batch_upsert_moneyflow_hsgt
    return batch_upsert_moneyflow_hsgt(db, source.fetch_moneyflow_hsgt(td))

def _ext_block_trade(source, db, td):
    from crawler.writers import batch_upsert_block_trade
    return batch_upsert_block_trade(db, source.fetch_block_trade(td))

def _ext_share_float(source, db, td):
    from crawler.writers import batch_insert_events
    return batch_insert_events(db, 'stock_share_float', source.fetch_share_float(td),
                               ['stock_code', 'float_date', 'holder_name'],
                               ['stock_code', 'ann_date', 'float_date', 'holder_name',
                                'shares', 'float_ratio', 'holder_type'])

def _ext_repurchase(source, db, td):
    from crawler.writers import batch_insert_events
    return batch_insert_events(db, 'stock_repurchase', source.fetch_repurchase(td),
                               ['stock_code', 'ann_date', 'vol', 'amount'],
                               ['stock_code', 'ann_date', 'end_date', 'proc', 'vol',
                                'amount', 'high_limit', 'low_limit'])

def _ext_dividend(source, db, td):
    from crawler.writers import batch_insert_events
    return batch_insert_events(db, 'stock_dividend', source.fetch_dividend(td),
                               ['stock_code', 'end_date', 'div_proc'],
                               ['stock_code', 'end_date', 'div_proc', 'ann_date', 'stk_div',
                                'cash_div', 'cash_div_tax', 'record_date', 'ex_date',
                                'pay_date', 'base_share'])

def _ext_forecast(source, db, td):
    from crawler.writers import batch_insert_events
    return batch_insert_events(db, 'stock_forecast', source.fetch_forecast(td),
                               ['stock_code', 'end_date', 'ann_date'],
                               ['stock_code', 'ann_date', 'end_date', 'type', 'p_change_min',
                                'p_change_max', 'net_profit_min', 'net_profit_max',
                                'last_parent_net', 'reason'])

def _ext_express(source, db, td):
    from crawler.writers import batch_insert_events
    return batch_insert_events(db, 'stock_express', source.fetch_express(td),
                               ['stock_code', 'end_date', 'ann_date'],
                               ['stock_code', 'ann_date', 'end_date', 'revenue', 'or_yoy',
                                'netprofit', 'yoy_net_profit', 'bps', 'total_assets'])

def _ext_index_weight(source, db, td):
    from crawler.writers import batch_upsert_index_weight
    from datetime import date as _d, timedelta as _td
    total = 0
    # 月度快照发布在月末交易日：窗口跨上月+本月，PK 去重幂等
    d = _d.fromisoformat(td)
    start = (_d(d.year, d.month, 1) - _td(days=1)).replace(day=1).isoformat() if (d.month > 1) else f'{d.year - 1}-12-01'
    for idx in ('000300.SH', '000905.SH'):
        total += batch_upsert_index_weight(db, source.fetch_index_weight(idx, start, td))
    return total

def _ext_fina_code(source, db, code):
    """单票全历史财务指标（fina_indicator(ts_code) 一次调用返回全部报告期）。"""
    from crawler.writers import batch_upsert_fina_indicator
    return batch_upsert_fina_indicator(db, source.fetch_fina_indicator(code))


def _ext_holder_number_code(source, db, code):
    """单票全历史股东户数（stk_holdernumber，公告制）→ stock_holder_number。"""
    from crawler.writers import batch_insert_events
    from datetime import date as _d
    ts_code = code + ('.SH' if code.startswith(('6', '9')) else '.SZ')
    rows = source.fetch_holder_history(ts_code, '2010-01-01', str(_d.today()))
    return batch_insert_events(db, 'stock_holder_number', rows,
                               ['stock_code', 'end_date'],
                               ['stock_code', 'end_date', 'holder_num'])


# (采集函数, 已入库存在性检查 SQL)；None = 无法按日期幂等（dividend/index_weight 按期去重）
_EXT_COLLECTORS = {
    'moneyflow':       (_ext_moneyflow,       "SELECT 1 FROM stock_moneyflow WHERE trade_date=:d LIMIT 1"),
    'top_list':        (_ext_top_list,        "SELECT 1 FROM stock_top_list WHERE trade_date=:d LIMIT 1"),
    'margin_detail':   (_ext_margin_detail,   "SELECT 1 FROM stock_margin_detail WHERE trade_date=:d LIMIT 1"),
    'moneyflow_hsgt':  (_ext_moneyflow_hsgt,  "SELECT 1 FROM moneyflow_hsgt WHERE trade_date=:d LIMIT 1"),
    'block_trade':     (_ext_block_trade,     "SELECT 1 FROM block_trade WHERE trade_date=:d LIMIT 1"),
    'share_float':     (_ext_share_float,     "SELECT 1 FROM stock_share_float WHERE ann_date=:d LIMIT 1"),
    'repurchase':      (_ext_repurchase,      "SELECT 1 FROM stock_repurchase WHERE ann_date=:d LIMIT 1"),
    'dividend':        (_ext_dividend,        "SELECT 1 FROM stock_dividend WHERE ex_date=:d LIMIT 1"),
    'forecast':        (_ext_forecast,        "SELECT 1 FROM stock_forecast WHERE ann_date=:d LIMIT 1"),
    'express':         (_ext_express,         "SELECT 1 FROM stock_express WHERE ann_date=:d LIMIT 1"),
    'index_weight':    (_ext_index_weight,    None),
}
# 代码轮换型采集器（按股票代码而非日期回补：fina_indicator 单票一次调用返回全部报告期）
_EXT_CODE_COLLECTORS = {
    'fina_indicator': _ext_fina_code,
    'holder_number': _ext_holder_number_code,
}


def _make_ext_node(name, doc):
    def _node(trade_date=None, **kw):
        from datetime import date
        td = str(trade_date or date.today())[:10]
        rid = _rid(kw)
        log_id = (kw.get('_node_log_ids', {}) or {}).get(name)
        write_node_log(log_id=log_id, status='running', detail='采集中')
        def _run():
            from crawler.adapters import get_data_source_manager
            from app.db.connection import get_sync_db
            source = get_data_source_manager().get_source()
            db = get_sync_db()
            saved = _EXT_COLLECTORS[name][0](source, db, td)
            db.close()
            return {'rows': saved, '_source': source.name}
        try:
            r = _with_hb(log_id, rid, _run)
            rows = r.get('rows', 0)
            write_node_log(log_id=log_id, status='success', rows=rows,
                           detail=f'完成 {rows} 行 (来源:{r.get("_source", "?")})')
            return r
        except Exception as e:
            write_node_log(log_id=log_id, status='failed', detail=str(e))
            raise
    _node.__name__ = f'dag_task_{name}'
    _node.__doc__ = doc
    return _node


def dag_task_data_backfill(trade_date=None, **kw):
    """DAG 节点：拓展数据通用回补（配额熔断 + 断点续跑）。

    参数存 strategy_config（strategy_name='backfill_ext'，params JSON）：
      {"tables": [...], "start_date": "2014-01-01", "end_date": "...", "reserve": 1000}
    循环「交易日 × 表」，已入库日期跳过；配额余量低于 reserve 即优雅退出，每夜自动续跑。"""
    from datetime import date as _d
    import json as _json
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    from crawler.adapters import get_data_source_manager
    from crawler.adapters.tushare_quota import TushareQuota, QuotaExhausted
    rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('data_backfill')
    write_node_log(log_id=log_id, status='running', detail='回补中')

    def _run():
        db = get_sync_db()
        row = db.execute(text("SELECT params FROM strategy_config WHERE strategy_name='backfill_ext' "
                              "ORDER BY id DESC LIMIT 1")).fetchone()
        cfg = _json.loads(row[0]) if row and isinstance(row[0], str) else (row[0] if row else {})
        tables = [t for t in cfg.get('tables', []) if t != 'fina_indicator']
        start, end = cfg.get('start_date', '2014-01-01'), cfg.get('end_date') or str(_d.today())
        reserve = int(cfg.get('reserve', 1000))
        days = [str(r[0]) for r in db.execute(text(
            "SELECT DISTINCT cal_date FROM trade_calendar WHERE cal_date BETWEEN :s AND :e "
            "AND is_trade_day = true ORDER BY cal_date"), {"s": start, "e": end}).fetchall()]
        quota = TushareQuota.get()
        source = get_data_source_manager().get_source()
        done = fail = 0
        # 代码轮换轴：fina_indicator / holder_number（公告制无日频扫描通道，按票全历史拉取）
        for _rot, _table in (('fina_indicator', 'fina_indicator'), ('holder_number', 'stock_holder_number')):
            if not cfg.get(_rot):
                continue
            have = {r[0] for r in db.execute(text(f"SELECT DISTINCT stock_code FROM {_table}")).fetchall()}
            codes = [r[0] for r in db.execute(text(
                "SELECT stock_code FROM stock_master WHERE stock_type='stock' "
                "ORDER BY stock_code")).fetchall() if r[0] not in have]
            logger.info(f"[data_backfill] {_rot} 待轮换 {len(codes)} 只")
            # 进度上报：轮换全程 >1 小时，不更新 rows 会被心跳看门狗判"无进展"误杀
            for ci, c in enumerate(codes):
                if _node_stopped(rid, log_id):
                    db.close()
                    raise RuntimeError(f'节点已终止（看门狗/用户），{_rot} 轮换中断，可续跑')
                if quota.remaining() <= reserve:
                    break
                try:
                    done += _EXT_CODE_COLLECTORS[_rot](source, db, c)
                except QuotaExhausted:
                    break
                except Exception as e:
                    fail += 1
                    logger.warning(f"[data_backfill] {_rot} {c}: {str(e)[:80]}")
                if ci % 20 == 0:
                    update_node_progress(log_id=log_id, rows=done,
                                         detail=f'{_rot} 轮换 {ci}/{len(codes)} 只')
                    db.commit()
        for di, td in enumerate(days):
            if _node_stopped(rid, log_id):
                db.close()
                raise RuntimeError('节点已终止（看门狗/用户），日期轴回补中断，可续跑')
            if di % 10 == 0:
                update_node_progress(log_id=log_id, rows=done,
                                     detail=f'日期轴 {td}（{di}/{len(days)}）')
            for t in tables:
                if t not in _EXT_COLLECTORS:
                    continue
                fn, exists_sql = _EXT_COLLECTORS[t]
                if exists_sql and db.execute(text(exists_sql), {"d": td}).fetchone():
                    continue
                # 已核空登记：事件类表大量"真 0 行"历史日（share_float 等 ~2900 空日/表），
                # 不登记则每轮 walk 从头重拉空日期烧配额，熔断点永远落在前段空日期区，
                # 回补永不收敛。0 行也登记，永不再拉。
                if db.execute(text(
                    "SELECT 1 FROM backfill_ext_checked WHERE table_name=:t AND checked_date=:d"
                ), {"t": t, "d": td}).fetchone():
                    continue
                if quota.remaining() <= reserve:
                    db.close()
                    return {'rows': done, 'halted': True,
                            '_detail': f'配额熔断（余 {quota.remaining()} ≤ 保留 {reserve}），已补 {done} 项，下次续跑'}
                try:
                    n = fn(source, db, td)
                    done += n
                    db.execute(text(
                        "INSERT INTO backfill_ext_checked (table_name, checked_date, rows_found) "
                        "VALUES (:t, :d, :n) ON CONFLICT DO NOTHING"), {"t": t, "d": td, "n": n})
                    db.commit()
                except QuotaExhausted:
                    db.rollback()
                    db.close()
                    return {'rows': done, 'halted': True, '_detail': f'配额耗尽，已补 {done} 项，下次续跑'}
                except Exception as e:
                    db.rollback()
                    fail += 1
                    logger.warning(f"[data_backfill] {t}@{td}: {str(e)[:80]}")
        db.close()
        return {'rows': done, '_detail': f'回补完成 {done} 项（异常 {fail}），配额余 {quota.remaining()}'}
    try:
        r = _with_hb(log_id, rid, _run)
        detail = r.get('_detail', f"完成 {r.get('rows', 0)} 项")
        write_node_log(log_id=log_id, status='success', rows=r.get('rows', 0), detail=detail)
        return r
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e))
        raise


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
    """factor_ic 节点 — 已发布 stock 特征批量重算 IC（近 1 年，1/5/10/20 前瞻）。

    共享前向收益面板一次拉取（逐因子重算 LEAD 窗口曾致节点 100 分钟），
    逐因子心跳写 detail/rows（页面实时可见进度，不再冻结在起始文案）。
    只刷新 factor_ic_stats 数据，不覆盖 features.ic_status 用户决策。
    """
    from datetime import date as _date, timedelta as _td
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    from scripts.factor_ic import compute_factor_ic_batch

    rid = _rid(kw)
    log_id = (kw.get('_node_log_ids', {}) or {}).get('factor_ic')
    write_node_log(log_id=log_id, status='running', detail='批量因子 IC 检验…')
    try:
        db = get_sync_db()
        val_end = str(trade_date or _date.today())[:10]
        val_start = (_date.fromisoformat(val_end) - _td(days=365)).isoformat()
        feats = db.execute(text(
            "SELECT feature_name FROM features WHERE status='enabled' "
            "AND target_entity='stock' ORDER BY feature_name"
        )).fetchall()
        names = [fn for (fn,) in feats]

        def _hb(done, total, fn):
            update_node_progress(log_id=log_id, rows=done,
                                 detail=f'IC检验 {done}/{total}: {fn}（近1年）')

        results = compute_factor_ic_batch(db, names, val_start, val_end, on_progress=_hb)
        db.close()
        ok = sum(1 for r in results.values() if 'error' not in r)
        fail = [f'{fn}: {r["error"][:60]}' for fn, r in results.items() if 'error' in r]
        detail = f'{ok}/{len(names)} 个因子完成（{val_start}~{val_end}）'
        if fail:
            detail += '；失败: ' + '、'.join(fail[:5])
        write_node_log(log_id=log_id, status='success', detail=detail, rows=ok)
        return True
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e)[:200])
        raise


def _active_versions(db):
    """全部上线模型（主在前，备按激活时间升序）——主备制的"在役集合"。"""
    from sqlalchemy import text as _t
    return [r[0] for r in db.execute(_t(
        "SELECT version FROM model_versions WHERE status='ACTIVE' "
        "ORDER BY (role='primary') DESC, activated_at ASC NULLS LAST")).fetchall()]


def dag_task_model_signals_all(trade_date=None, **kw):
    """model_signal 节点（主备制）：对全部上线模型按各自规则生产信号。"""
    from app.db.connection import get_sync_db
    db = get_sync_db()
    vers = _active_versions(db)
    db.close()
    if not vers:
        return dag_task_model_signal(trade_date=trade_date, **kw)
    total, parts = 0, []
    for v in vers:
        try:
            n = dag_task_model_signal(trade_date=trade_date, _ver=v, **kw)
            total += (n or 0)
            parts.append(f'{v}:{n or 0}条')
        except Exception as e:
            db.rollback() if not db.closed else None
            logger.warning(f'[model_signal] {v} 信号生产失败: {e}')
            parts.append(f'{v}:失败')
    log_id = (kw.get('_node_log_ids', {}) or {}).get('model_signal')
    write_node_log(log_id=log_id, status='success', rows=total,
                   detail=f'主备信号生产（{len(vers)} 模型）: ' + '、'.join(parts))
    return total


def dag_task_model_healths_all(trade_date=None, **kw):
    """model_health 节点（主备制）：对全部上线模型各自健康检查。"""
    from app.db.connection import get_sync_db
    db = get_sync_db()
    vers = _active_versions(db)
    db.close()
    if not vers:
        return dag_task_model_health(trade_date=trade_date, **kw)
    total, parts = 0, []
    for v in vers:
        try:
            n = dag_task_model_health(trade_date=trade_date, _ver=v, **kw)
            total += (n or 0)
            parts.append(f'{v}:{n or 0}')
        except Exception as e:
            logger.warning(f'[model_health] {v} 健康检查失败: {e}')
            parts.append(f'{v}:失败')
    log_id = (kw.get('_node_log_ids', {}) or {}).get('model_health')
    write_node_log(log_id=log_id, status='success', rows=total,
                   detail=f'主备健康检查（{len(vers)} 模型）: ' + '、'.join(parts))
    return total


def dag_task_paper_portfolio_all(trade_date=None, **kw):
    """paper_portfolio 节点（主备制）：对全部上线模型各自账户步进（各自规则+隔离账本）。"""
    from app.db.connection import get_sync_db
    db = get_sync_db()
    vers = _active_versions(db)
    db.close()
    if not vers:
        return dag_task_paper_portfolio(trade_date=trade_date, **kw)
    total, parts = 0, []
    for v in vers:
        try:
            n = dag_task_paper_portfolio(trade_date=trade_date, _ver=v, **kw)
            total += (n or 0)
            parts.append(f'{v}:{n or 0}日')
        except Exception as e:
            logger.warning(f'[paper_portfolio] {v} 步进失败: {e}')
            parts.append(f'{v}:失败')
    log_id = (kw.get('_node_log_ids', {}) or {}).get('paper_portfolio')
    write_node_log(log_id=log_id, status='success', rows=total,
                   detail=f'主备账户步进（{len(vers)} 模型）: ' + '、'.join(parts))
    return total


def dag_task_backup_qiniu(trade_date=None, **kw):
    """DAG 节点：数据库全量备份 → 七牛云 Kodo（restic 增量去重上传）。

    调 scripts/backup_qiniu.sh（pg_dump -Fd 目录格式全量 + restic 内容分块去重上传）。
    幂等性：脚本 flock 防并发重叠、暂存文件成功后原子替换、restic 快照不可变，
    任意重跑安全；全量快照自包含，某天流程失败无需回补——下一天的成功快照
    已覆盖全部数据，仅当天的还原点缺失。未配置 ~/.config/stone-backup/qiniu.env
    时脚本自动跳过并视为成功（新环境不阻塞流程）。"""
    log_id = (kw.get('_node_log_ids', {}) or {}).get('backup_qiniu')
    write_node_log(log_id=log_id, status='running', detail='pg_dump 全量导出 + restic 上传…')
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backup_qiniu.sh')
    def _run():
        import gc
        import subprocess
        gc.collect()  # 前置重节点（factor_ic 等）宽表帧及时回收，降低 dump 期间内存顶格概率
        r = subprocess.run(['bash', script], capture_output=True, text=True, timeout=8 * 3600)
        out = (r.stdout or '') + (r.stderr or '')
        if r.returncode != 0:
            raise RuntimeError('backup_qiniu.sh 失败: ' + out[-400:])
        return {'skipped': '跳过' in out, 'tail': out[-300:]}
    try:
        r = _with_hb(log_id, _rid(kw), _run)
        detail = '备份跳过（未配置 ~/.config/stone-backup/qiniu.env）' if r.get('skipped') else '已上传最新全量快照'
        write_node_log(log_id=log_id, status='success', detail=detail)
        return r
    except Exception as e:
        write_node_log(log_id=log_id, status='failed', detail=str(e))
        raise


NODE_FN_MAP = {
    'stock_master':      dag_task_stock_master,    'cron':               dag_task_cron,
    'backup_qiniu':      dag_task_backup_qiniu,
    'kline':              dag_task_kline,
    'factor_heal':        dag_task_factor_heal,
    'rolling_retrain':    dag_task_rolling_retrain,
    'margin_daily':       dag_task_margin_daily,
    'index':              dag_task_index,
    'etf':                dag_task_etf,
    'fund':               dag_task_fund,
    'treemap':            dag_task_treemap,
    'stats':              dag_task_stats,
    'daily_completeness': dag_task_completeness,
    'model_signal':       dag_task_model_signals_all,
    'model_health':       dag_task_model_healths_all,
    'feature_compute':    dag_task_feature_compute,
    'feature_backfill':  dag_task_feature_backfill,
    'entity_stats':      dag_task_entity_stats,
    'factor_ic':         dag_task_factor_ic,
    'paper_portfolio':    dag_task_paper_portfolio_all,
    'moneyflow':         dag_task_moneyflow,
    'analyze':           dag_task_analyze,
    **{n: _make_ext_node(n, f'DAG 节点：{n} 拓展数据采集') for n in (
        'top_list', 'margin_detail', 'moneyflow_hsgt', 'block_trade', 'share_float',
        'repurchase', 'dividend', 'forecast', 'express', 'index_weight', 'fina_daily')},
    'data_backfill':     dag_task_data_backfill,
}
