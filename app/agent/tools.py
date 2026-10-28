"""Agent 工具集——内部数据查询、沙箱执行、DAG 编排。

原则：
- 数据一律查本地库（日线/特征/资讯/雪球/信号/纸面组合），不联网取时效数据；
- 只读 SQL 工具走只读事务 + statement_timeout + 行数上限，写操作只开放「创建 DAG 草稿流程」
  这类平台内动作，且默认不发布（发布仍需人工在 UI 确认）；
- 每个工具的返回都做 JSON 安全化与长度截断，避免把模型上下文撑爆。
"""
import json
import re
from datetime import date, timedelta

from sqlalchemy import text

from app.agent import sandbox

# ── 数据字典（给模型看的「这里有什么数据」）────────────────────────────
TABLE_DOCS = {
    "daily_quote": "个股+ETF 日线（后复权 close_hfq 用于收益计算；close 是真实价）。关键列 stock_code/trade_date/open/high/low/close/close_hfq/volume/amount/turnover_rate",
    "index_daily_quote": "指数日线（沪深300 index_code='000300'）。列 trade_date/index_code/open/high/low/close/volume/amount",
    "stock_master": "证券主档（stock_type: stock/index/etf；status='N' 在市）。列 stock_code/stock_name/exchange/industry/industry_l1/industry_l2/ipo_date",
    "stock_fundamentals": "最新基本面快照。列 stock_code/pe_ttm/pb_mrq/roe/revenue_yoy/profit_yoy/market_cap/turnover_rate/volume_ratio/dv_ttm",
    "stock_fundamentals_history": "基本面历史（按报告期）。列 stock_code/report_date/roe/...",
    "features": "特征注册表（151 个）。列 feature_name/display_name/description/formula/feature_group/status/latest_computed_date",
    "feature_values": "特征值长表（14 亿行，按年分区）。列 feature_name/stock_code/trade_date/value。查询务必带 trade_date 或 feature_name 限定，否则很慢",
    "signal_history": "模型/规则信号。列 signal_date/stock_code/direction(buy/sell)/strength/price/reason/model_version/predict_score/forward_5d_return/status/close_reason/actual_return",
    "portfolio": "实盘持仓。列 stock_code/stock_name/quantity/cost_price/is_active/notes",
    "paper_positions": "纸面组合当前持仓。列 stock_code/shares/buy_price/buy_date/peak/model_version",
    "paper_trades": "纸面组合成交与逐日净值（action=BUY/SELL/EOD）。列 trade_date/action/stock_code/price/shares/equity/cash/pnl/reason",
    "news_item": "资讯原文（六源）。列 id/source(财联社 cls 等)/category/published_at/title/content/url",
    "news_summary": "LLM 资讯总结。列 news_id/summary/importance(1-3)/topics/sentiment",
    "v_news_feed": "资讯+总结合并视图。列 id/source/category/published_at/title/content/summary/importance/sentiment",
    "xueqiu_status": "雪球帖子（36 万条）。列 status_id/code/user_id/created_at/title/text_clean/view_count/like_count/reply_count/mark",
    "xueqiu_sentiment": "雪球 LLM 情绪打分。列 status_id/sentiment(positive/neutral/negative)/confidence/reason",
    "stock_moneyflow": "个股资金流。列 trade_date/stock_code/net_mf_amt/buy_elg_amt/sell_elg_amt/buy_lg_amt/sell_lg_amt",
    "stock_margin_detail": "两融明细。列 trade_date/stock_code/fin_amount/sec_amount/total_amount",
    "stock_top_list": "龙虎榜。列 trade_date/stock_code/reason/net",
    "dag_flows": "DAG 流程定义。列 id/flow_name/nodes/edges/cron_expr/status(published/draft)/last_run_at",
    "dag_run_log": "DAG 节点执行日志。列 run_id/trade_date/node_name/status/rows/detail/started_at/finished_at",
    "backfill_tasks": "补数任务。列 task_id/task_type/status/started_at/completed_at",
    "trade_calendar": "交易日历。列 cal_date/is_trade_day",
}

DAG_NODE_HINT = ("常用节点：cron(定时触发)、kline(个股日K)、index、etf、fund、moneyflow、margin_daily、"
                 "top_list、feature_compute(特征计算)、model_signal(信号)、model_health、paper_portfolio、"
                 "news_crawl、news_summary、xueqiu_sentiment、stats、analyze、backup_qiniu")


def _json_safe(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (date,)):
        return str(v)
    try:
        import decimal
        if isinstance(v, decimal.Decimal):
            return float(v)
    except Exception:
        pass
    return str(v)


# ══════════════════════════════════════════════
#  数据查询工具
# ══════════════════════════════════════════════

def tool_list_tables(_session_id: int, **kw) -> dict:
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT relname, reltuples::bigint FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('r','v','p') AND relname = ANY(:names)
        """), {"names": list(TABLE_DOCS)}).fetchall()
        est = {r[0]: max(int(r[1] or 0), 0) for r in rows}
        out = [{"table": t, "est_rows": est.get(t, 0), "desc": d} for t, d in TABLE_DOCS.items()]
        return {"tables": out, "note": "est_rows 为 planner 估算；查询请用 describe_table 看列名"}
    finally:
        db.close()


def tool_describe_table(_session_id: int, table: str = "", **kw) -> dict:
    if table not in TABLE_DOCS:
        return {"error": f"未知表 {table}，可用表见 list_tables"}
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT column_name, data_type FROM information_schema.columns
            WHERE table_name = :t AND table_schema='public' ORDER BY ordinal_position
        """), {"t": table}).fetchall()
        return {"table": table, "desc": TABLE_DOCS[table],
                "columns": [{"name": r[0], "type": r[1]} for r in rows]}
    finally:
        db.close()


_SQL_ALLOWED = re.compile(r"^\s*(select|with)\b", re.I)


def tool_query_sql(_session_id: int, sql: str = "", limit: int = 100, **kw) -> dict:
    """只读 SQL：单条 SELECT/WITH，只读事务 + 25s 超时 + 行数上限。"""
    q = (sql or "").strip().rstrip(";").strip()
    if not q:
        return {"error": "SQL 为空"}
    if not _SQL_ALLOWED.match(q):
        return {"error": "只允许 SELECT 或 WITH 开头的只读查询"}
    # 多条语句拦截（引号内的分号少见，宁可误拒也不放行写操作）
    if ";" in q:
        return {"error": "只允许单条 SQL（检测到分号）"}
    lim = max(1, min(int(limit or 100), 500))
    if not re.search(r"\blimit\s+\d+\s*$", q, re.I):
        q = f"{q}\nLIMIT {lim}"

    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        db.execute(text("SET LOCAL statement_timeout = '25s'"))
        rows = db.execute(text(q)).fetchall()
        cols = list(rows[0]._mapping.keys()) if rows else []
        data = [[_json_safe(v) for v in r] for r in rows]
        return {"columns": cols, "rows": data, "row_count": len(data),
                "truncated": len(data) >= lim, "limit": lim}
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}
    finally:
        db.close()


def tool_stock_overview(_session_id: int, stock_code: str = "", **kw) -> dict:
    """个股全景快照：行情、基本面、行业、最近信号、所属分组、雪球情绪。"""
    code = (stock_code or "").strip()
    if not re.match(r"^\d{6}$", code):
        return {"error": "stock_code 需为 6 位数字"}
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        m = db.execute(text(
            "SELECT stock_name, exchange, industry, industry_l1, industry_l2, status, ipo_date "
            "FROM stock_master WHERE stock_code=:c AND stock_type='stock' LIMIT 1"), {"c": code}).fetchone()
        if not m:
            return {"error": f"未找到个股 {code}"}
        q = db.execute(text("""
            SELECT trade_date, close, close_hfq, volume, amount
            FROM daily_quote WHERE stock_code=:c AND close > 0
            ORDER BY trade_date DESC LIMIT 2"""), {"c": code}).fetchall()
        f = db.execute(text("""
            SELECT pe_ttm, pb_mrq, roe, revenue_yoy, profit_yoy, market_cap, turnover_rate, dv_ttm
            FROM stock_fundamentals WHERE stock_code=:c ORDER BY trade_date DESC NULLS LAST LIMIT 1"""),
            {"c": code}).fetchone()
        sigs = db.execute(text("""
            SELECT signal_date, direction, strength, reason, predict_score, model_version
            FROM signal_history WHERE stock_code=:c ORDER BY signal_date DESC, id DESC LIMIT 5"""),
            {"c": code}).fetchall()
        groups = db.execute(text("""
            SELECT g.group_name FROM watch_group_items i JOIN watch_groups g ON g.id=i.group_id
            WHERE i.stock_code=:c ORDER BY g.is_default DESC, g.id"""), {"c": code}).fetchall()
        xq = db.execute(text("""
            SELECT s.sentiment, COUNT(*) FROM xueqiu_status x
            JOIN xueqiu_sentiment s ON s.status_id = x.status_id
            WHERE x.code=:c AND x.created_at > CURRENT_DATE - INTERVAL '30 days'
            GROUP BY s.sentiment"""), {"c": code}).fetchall()
        chg = None
        if len(q) == 2 and q[1][1]:
            chg = round((float(q[0][1]) / float(q[1][1]) - 1) * 100, 2)
        return {
            "stock": {"code": code, "name": m[0], "exchange": m[1], "industry": m[2],
                      "industry_l1": m[3], "industry_l2": m[4], "status": m[5],
                      "ipo_date": _json_safe(m[6])},
            "quote": {"trade_date": _json_safe(q[0][0]) if q else None,
                      "close": float(q[0][1]) if q and q[0][1] else None,
                      "chg_pct": chg,
                      "volume": float(q[0][3]) if q and q[0][3] else None,
                      "amount": float(q[0][4]) if q and q[0][4] else None},
            "fundamentals": {k: (float(v) if v is not None else None) for k, v in zip(
                ["pe_ttm", "pb_mrq", "roe", "revenue_yoy", "profit_yoy", "market_cap",
                 "turnover_rate", "dv_ttm"], f)} if f else None,
            "recent_signals": [{"date": _json_safe(r[0]), "direction": r[1], "strength": float(r[2] or 0),
                                "reason": r[3], "predict_score": float(r[4]) if r[4] is not None else None,
                                "model": r[5]} for r in sigs],
            "groups": [r[0] for r in groups],
            "xueqiu_sentiment_30d": {r[0]: r[1] for r in xq},
        }
    finally:
        db.close()


def tool_search_news(_session_id: int, keyword: str = "", days: int = 30, limit: int = 15, **kw) -> dict:
    """资讯检索（标题/正文/摘要），返回重要度与情绪。"""
    kw_ = (keyword or "").strip()
    if not kw_:
        return {"error": "keyword 不能为空"}
    days = max(1, min(int(days or 30), 365))
    lim = max(1, min(int(limit or 15), 50))
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT n.published_at, n.source, n.title, LEFT(n.content, 300), s.summary,
                   s.importance, s.sentiment
            FROM news_item n LEFT JOIN news_summary s ON s.news_id = n.id
            WHERE n.published_at > NOW() - (:d || ' days')::interval
              AND (n.title ILIKE :k OR n.content ILIKE :k OR s.summary ILIKE :k)
            ORDER BY n.published_at DESC LIMIT :l
        """), {"d": days, "k": f"%{kw_}%", "l": lim}).fetchall()
        return {"keyword": kw_, "days": days, "count": len(rows), "items": [
            {"published_at": _json_safe(r[0])[:19], "source": r[1], "title": r[2],
             "content": r[3], "summary": r[4], "importance": r[5], "sentiment": r[6]}
            for r in rows]}
    finally:
        db.close()


# ══════════════════════════════════════════════
#  沙箱工具
# ══════════════════════════════════════════════

def tool_run_python(session_id: int, code: str = "", timeout: int = 60, **kw) -> dict:
    r = sandbox.run_python(session_id, code or "", timeout=max(5, min(int(timeout or 60), 180)))
    r["hint"] = "工作区文件跨调用保留；pip_install 装的包在 PYTHONPATH 内可直接 import"
    return r


def tool_run_node(session_id: int, code: str = "", timeout: int = 60, **kw) -> dict:
    r = sandbox.run_node(session_id, code or "", timeout=max(5, min(int(timeout or 60), 180)))
    r["hint"] = "npm_install 装的包在 NODE_PATH 内可直接 import"
    return r


def tool_pip_install(session_id: int, packages: list = None, **kw) -> dict:
    pkgs = packages if isinstance(packages, list) else [str(packages or "")]
    return sandbox.pip_install(session_id, pkgs)


def tool_npm_install(session_id: int, packages: list = None, **kw) -> dict:
    pkgs = packages if isinstance(packages, list) else [str(packages or "")]
    return sandbox.npm_install(session_id, pkgs)


def tool_list_workspace(session_id: int, **kw) -> dict:
    return sandbox.list_workspace(session_id)


# ══════════════════════════════════════════════
#  DAG / 自动化工具
# ══════════════════════════════════════════════

def tool_list_dag_flows(_session_id: int, **kw) -> dict:
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT id, flow_name, status, cron_expr, last_run_at,
                   jsonb_array_length(COALESCE(nodes,'[]'::jsonb)) AS n
            FROM dag_flows ORDER BY id""")).fetchall()
        return {"flows": [{"id": r[0], "name": r[1], "status": r[2], "cron": r[3],
                           "last_run_at": _json_safe(r[4]), "node_count": r[5]} for r in rows],
                "node_hint": DAG_NODE_HINT}
    finally:
        db.close()


def tool_create_dag_flow(_session_id: int, flow_name: str = "", description: str = "",
                         nodes: list = None, cron_expr: str = "", **kw) -> dict:
    """创建 DAG 流程（draft，不自动发布）。nodes: [{"node_name":"kline","deps":[]}, ...]"""
    from app.api.dag_flows import FlowNode, _validate_flow
    from app.db.connection import get_sync_db
    name = (flow_name or "").strip()
    if len(name) < 2:
        return {"error": "flow_name 至少 2 个字符"}
    try:
        fns = [FlowNode(node_name=n.get("node_name", ""), deps=list(n.get("deps") or []))
               for n in (nodes or []) if isinstance(n, dict)]
    except Exception as e:
        return {"error": f"nodes 结构错误: {e}"}
    db = get_sync_db()
    try:
        errs = _validate_flow(fns, db)
        if errs:
            return {"error": "；".join(errs), "hint": DAG_NODE_HINT}
        from scripts.pipeline import NODE_FN_MAP
        unknown = [n.node_name for n in fns if n.node_name not in NODE_FN_MAP]
        if unknown:
            return {"error": f"未注册的节点: {unknown}", "hint": DAG_NODE_HINT}
        nodes_json = json.dumps([n.model_dump() for n in fns], ensure_ascii=False)
        edges = [{"source": d, "target": n.node_name} for n in fns for d in n.deps]
        row = db.execute(text("""
            INSERT INTO dag_flows (flow_name, description, nodes, edges, cron_expr, status)
            VALUES (:n, :d, :nodes, :edges, :cron, 'draft') RETURNING id
        """), {"n": name, "d": description or "", "nodes": nodes_json,
               "edges": json.dumps(edges), "cron": cron_expr or None}).fetchone()
        fid = row[0]
        db.execute(text("""INSERT INTO dag_flow_versions (flow_id, version, nodes, edges, change_log)
                           VALUES (:f, 1, :nodes, :edges, 'agent 创建')"""),
                   {"f": fid, "nodes": nodes_json, "edges": json.dumps(edges)})
        db.commit()
        return {"ok": True, "flow_id": fid, "flow_name": name, "status": "draft",
                "note": "已创建为草稿（不会自动运行）；确认无误后在 DAG 页发布，或让我调用 run_dag_flow 试跑"}
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    finally:
        db.close()


def tool_run_dag_flow(_session_id: int, flow_id: int = 0, trade_date: str = "", **kw) -> dict:
    """触发已发布流程执行（后台任务，受同流程互斥约束）。"""
    from app.api.dag_flows import _execute_flow_internal
    if not flow_id:
        return {"error": "flow_id 必填（先用 list_dag_flows 查）"}
    try:
        r = _execute_flow_internal(int(flow_id), {"trade_date": trade_date or ""})
        return {"ok": True, "result": {k: _json_safe(v) for k, v in (r or {}).items()
                                       if k in ("task_id", "status", "flow_name", "trade_date")}}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}


# ══════════════════════════════════════════════
#  注册表（OpenAI tools 格式）
# ══════════════════════════════════════════════

def _tool(name, desc, props, required=()):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": list(required)}}}


TOOL_SPECS = [
    _tool("list_tables", "列出可查询的数据表及行数估算与说明。不熟悉数据时先调用它。", {}),
    _tool("describe_table", "查看某张表的列名与类型。", {"table": {"type": "string"}}, ["table"]),
    _tool("query_sql", "执行只读 SQL（单条 SELECT/WITH）查询本地数据库。这是获取行情/特征/信号/资讯的主力工具。",
          {"sql": {"type": "string", "description": "PostgreSQL 只读查询"},
           "limit": {"type": "integer", "description": "返回行数上限，默认 100，最大 500"}}, ["sql"]),
    _tool("stock_overview", "个股全景快照：行情、估值、行业、最近信号、所属分组、30天雪球情绪。",
          {"stock_code": {"type": "string", "description": "6 位代码，如 600519"}}, ["stock_code"]),
    _tool("search_news", "按关键词检索资讯（标题/正文/摘要），返回重要度与情绪。",
          {"keyword": {"type": "string"}, "days": {"type": "integer", "description": "回看天数，默认30"},
           "limit": {"type": "integer", "description": "默认15"}}, ["keyword"]),
    _tool("run_python", "在会话沙箱执行 Python（可用 pandas/numpy/matplotlib，工作区跨调用保留）。",
          {"code": {"type": "string"}, "timeout": {"type": "integer", "description": "秒，默认60"}}, ["code"]),
    _tool("run_node", "在会话沙箱执行 Node.js（ESM，.mjs）。", {"code": {"type": "string"}}, ["code"]),
    _tool("pip_install", "给会话沙箱安装 Python 包（装到会话私有目录）。",
          {"packages": {"type": "array", "items": {"type": "string"}}}, ["packages"]),
    _tool("npm_install", "给会话沙箱安装 npm 包。",
          {"packages": {"type": "array", "items": {"type": "string"}}}, ["packages"]),
    _tool("list_workspace", "查看会话沙箱工作区里的文件。", {}),
    _tool("list_dag_flows", "列出平台已有的 DAG 流程（含 cron 与上次运行时间）。", {}),
    _tool("create_dag_flow", "创建 DAG 流程草稿（不自动发布）。nodes 形如 [{\"node_name\":\"kline\",\"deps\":[]}]。",
          {"flow_name": {"type": "string", "description": "2~64 字符"},
           "description": {"type": "string"},
           "nodes": {"type": "array", "items": {"type": "object"}},
           "cron_expr": {"type": "string", "description": "五段式 cron，如 '*/20 * * * *'，可空"}},
          ["flow_name", "nodes"]),
    _tool("run_dag_flow", "触发某个 DAG 流程执行（后台任务）。",
          {"flow_id": {"type": "integer"}, "trade_date": {"type": "string", "description": "YYYY-MM-DD，空=今天"}},
          ["flow_id"]),
]

_DISPATCH = {
    "list_tables": tool_list_tables, "describe_table": tool_describe_table,
    "query_sql": tool_query_sql, "stock_overview": tool_stock_overview,
    "search_news": tool_search_news,
    "run_python": tool_run_python, "run_node": tool_run_node,
    "pip_install": tool_pip_install, "npm_install": tool_npm_install,
    "list_workspace": tool_list_workspace,
    "list_dag_flows": tool_list_dag_flows, "create_dag_flow": tool_create_dag_flow,
    "run_dag_flow": tool_run_dag_flow,
}


def execute_tool(session_id: int, name: str, args: dict) -> dict:
    """执行工具并统一截断（返回给模型的内容不超过 12KB）。"""
    fn = _DISPATCH.get(name)
    if not fn:
        return {"error": f"未知工具 {name}"}
    try:
        r = fn(session_id, **(args or {}))
    except TypeError as e:
        return {"error": f"参数错误: {e}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}
    s = json.dumps(r, ensure_ascii=False, default=str)
    if len(s) > 12000:
        return {"truncated": True, "preview": s[:12000] + "…"}
    return r
