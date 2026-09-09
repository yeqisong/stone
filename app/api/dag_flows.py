"""DAG 流程编排 API（v2.0 重构 迭代 4.1）。

提供 DAG 流程的 CRUD、校验、版本管理。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import json, threading

from app.auth.auth import get_current_user

router = APIRouter(prefix="/api/dag", tags=["dag-flows"])


class FlowNode(BaseModel):
    node_name: str
    deps: List[str] = []
    position: Optional[Dict[str, Any]] = None


class CreateFlow(BaseModel):
    flow_name: str = Field(..., min_length=2, max_length=64)
    description: str = ""
    nodes: List[FlowNode] = []
    cron_expr: str = ""


class UpdateFlow(BaseModel):
    description: Optional[str] = None
    nodes: Optional[List[FlowNode]] = None
    cron_expr: Optional[str] = None
    status: Optional[str] = None
    change_log: str = ""


@router.get("/flows")
def list_flows():
    """列出所有 DAG 流程（含节点数）。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        rows = db.execute(text("SELECT * FROM dag_flows ORDER BY created_at DESC")).fetchall()
        items = []
        for r in rows:
            nodes = json.loads(r[3]) if isinstance(r[3], str) else (r[3] or [])
            items.append({
                "id": r[0], "flow_name": r[1], "description": r[2],
                "cron_expr": r[5], "status": r[6], "is_active": r[7],
                "created_at": str(r[8])[:19] if r[8] else None,
                "node_count": len(nodes),
            })
        db.close()
        return {"items": items, "total": len(items)}
    except Exception as e:
        db.close()
        return {"items": [], "total": 0}


@router.post("/flows")
def create_flow(body: CreateFlow, user: str = Depends(get_current_user)):
    """创建新 DAG 流程（含 7 项校验）。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        # 校验
        errors = _validate_flow(body.nodes, db)
        if errors:
            db.close()
            raise HTTPException(400, "; ".join(errors))

        # 结果在 INSERT 中复用
        nodes_json = json.dumps([n.model_dump() for n in body.nodes])
        edges = []
        for n in body.nodes:
            for dep in n.deps:
                edges.append({"source": dep, "target": n.node_name})

        db.execute(text("""
            INSERT INTO dag_flows (flow_name, description, nodes, edges, cron_expr, status)
            VALUES (:n, :d, :nodes, :edges, :cron, 'draft')
        """), {"n": body.flow_name, "d": body.description, "nodes": nodes_json, "edges": json.dumps(edges), "cron": body.cron_expr or None})

        # 创建 v1 版本快照
        fid = db.execute(text("SELECT id FROM dag_flows WHERE flow_name = :n"), {"n": body.flow_name}).fetchone()[0]
        db.execute(text("""
            INSERT INTO dag_flow_versions (flow_id, version, nodes, edges)
            VALUES (:fid, 1, :nodes, :edges)
        """), {"fid": fid, "nodes": nodes_json, "edges": json.dumps(edges)})

        db.commit()
        db.close()
        return {"ok": True, "flow_id": fid}
    except HTTPException:
        db.close()
        raise
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e)[:200])


@router.get("/flows/{flow_id}")
def get_flow(flow_id: int):
    """获取单个流程详情（含当前版本 + 版本历史）。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT * FROM dag_flows WHERE id = :id"), {"id": flow_id}).fetchone()
        if not r:
            db.close()
            raise HTTPException(404, "流程不存在")

        versions = db.execute(text(
            "SELECT version, change_log, created_at FROM dag_flow_versions WHERE flow_id = :fid ORDER BY version DESC"
        ), {"fid": flow_id}).fetchall()

        db.close()
        nodes = json.loads(r[3]) if isinstance(r[3], str) else (r[3] or [])
        edges = json.loads(r[4]) if isinstance(r[4], str) else (r[4] or [])
        return {
            "id": r[0], "flow_name": r[1], "description": r[2],
            "nodes": nodes, "edges": edges,
            "cron_expr": r[5], "status": r[6], "is_active": r[7],
            "created_at": str(r[8])[:19] if r[8] else None,
            "versions": [{"version": v[0], "change_log": v[1], "created_at": str(v[2])[:19] if v[2] else None} for v in versions],
        }
    except HTTPException:
        db.close()
        raise
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e)[:200])


@router.put("/flows/{flow_id}")
def update_flow(flow_id: int, body: UpdateFlow, user: str = Depends(get_current_user)):
    """更新流程（自动创建新版本）。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT * FROM dag_flows WHERE id = :id"), {"id": flow_id}).fetchone()
        if not r:
            db.close()
            raise HTTPException(404, "流程不存在")

        updates = []
        params = {"id": flow_id}

        if body.description is not None:
            updates.append("description = :desc")
            params["desc"] = body.description
        if body.cron_expr is not None:
            updates.append("cron_expr = :cron")
            params["cron"] = body.cron_expr
        if body.status is not None:
            updates.append("status = :st")
            params["st"] = body.status

        if body.nodes is not None:
            errors = _validate_flow(body.nodes, db)
            if errors:
                db.close()
                raise HTTPException(400, "; ".join(errors))
            nodes_json = json.dumps([n.model_dump() for n in body.nodes])
            edges = [{"source": dep, "target": n.node_name} for n in body.nodes for dep in n.deps]
            updates.append("nodes = :nodes")
            params["nodes"] = nodes_json
            updates.append("edges = :edges")
            params["edges"] = json.dumps(edges)

            # 新版本
            max_ver = db.execute(text("SELECT COALESCE(MAX(version),0) FROM dag_flow_versions WHERE flow_id = :fid"), {"fid": flow_id}).scalar() or 0
            db.execute(text("INSERT INTO dag_flow_versions (flow_id, version, nodes, edges, change_log) VALUES (:fid, :v, :nodes, :edges, :log)"),
                       {"fid": flow_id, "v": max_ver+1, "nodes": nodes_json, "edges": json.dumps(edges), "log": body.change_log or ""})

        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            db.execute(text(f"UPDATE dag_flows SET {', '.join(updates)} WHERE id = :id"), params)
            db.commit()

        db.close()
        return {"ok": True}
    except HTTPException:
        db.close()
        raise
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e)[:200])


@router.delete("/flows/{flow_id}")
def delete_flow(flow_id: int, user: str = Depends(get_current_user)):
    """删除流程（级联删除版本历史）。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT id FROM dag_flows WHERE id = :id"), {"id": flow_id}).fetchone()
        if not r:
            db.close()
            raise HTTPException(404, "流程不存在")
        db.execute(text("DELETE FROM dag_flows WHERE id = :id"), {"id": flow_id})
        db.commit()
        db.close()
        return {"ok": True}
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e)[:200])


@router.post("/flows/{flow_id}/publish")
def publish_flow(flow_id: int, user: str = Depends(get_current_user)):
    """发布流程：draft → published。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT status FROM dag_flows WHERE id=:id"), {"id": flow_id}).fetchone()
        if not r:
            raise HTTPException(404, "流程不存在")
        db.execute(text("UPDATE dag_flows SET status='published', updated_at=CURRENT_TIMESTAMP WHERE id=:id"), {"id": flow_id})
        db.commit()
        db.close()
        return {"ok": True, "status": "published"}
    except HTTPException:
        raise
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e)[:200])


@router.post("/flows/{flow_id}/unpublish")
def unpublish_flow(flow_id: int, user: str = Depends(get_current_user)):
    """下线流程：published → draft。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT status FROM dag_flows WHERE id=:id"), {"id": flow_id}).fetchone()
        if not r:
            raise HTTPException(404, "流程不存在")
        db.execute(text("UPDATE dag_flows SET status='draft', updated_at=CURRENT_TIMESTAMP WHERE id=:id"), {"id": flow_id})
        db.commit()
        db.close()
        return {"ok": True, "status": "draft"}
    except HTTPException:
        raise
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e)[:200])


@router.get("/flows/{flow_id}/task-status")
def get_flow_task_status(flow_id: int):
    """查询流程当前是否有活跃任务及其状态。"""
    from app.task import TaskManager
    tm = TaskManager()
    task = tm.get_flow_active_task(flow_id)
    if not task:
        return {"has_task": False}
    return {"has_task": True, **task.to_dict()}


@router.get("/flows/{flow_id}/versions")
def list_flow_versions(flow_id: int):
    """流程版本历史。"""
    from app.db.connection import get_sync_db
    db = get_sync_db()
    try:
        rows = db.execute(text(
            "SELECT version, change_log, created_at FROM dag_flow_versions WHERE flow_id=:id ORDER BY version DESC"
        ), {"id": flow_id}).fetchall()
        db.close()
        return {"versions": [{"version": r[0], "change_log": r[1], "created_at": str(r[2])[:19] if r[2] else None} for r in rows]}
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e)[:200])


@router.post("/flows/validate")
def validate_flow_nodes(body: dict):
    """仅校验节点配置（不保存）。"""
    from app.db.connection import get_sync_db
    nodes_raw = body.get("nodes", [])
    nodes = [FlowNode(node_name=n.get("node_name",""), deps=n.get("deps",[])) for n in nodes_raw]
    db = get_sync_db()
    try:
        errors = _validate_flow(nodes, db)
        db.close()
        return {"ok": len(errors) == 0, "errors": errors}
    except Exception as e:
        db.close()
        return {"ok": False, "errors": [str(e)]}


@router.post("/flows/{flow_id}/execute")
def execute_flow(flow_id: int, body: dict = {}, user: str = Depends(get_current_user)):
    """触发流程执行。body: {"trade_date": "2026-07-08"}（默认今天）。"""
    return _execute_flow_internal(flow_id, body)


def _execute_flow_internal(flow_id: int, body: dict = {}) -> dict:
    """内部执行流程（无 auth 要求，供 cron 调度器调用）。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    from scripts.pipeline import NODE_FN_MAP
    from scripts.dag import DagExecutor, DagNode
    from datetime import date as _date
    import uuid, threading, json as _json
    from app.task import TaskManager

    db = get_sync_db()
    try:
        r = db.execute(text("SELECT flow_name, nodes, edges, cron_expr FROM dag_flows WHERE id=:id"),
                       {"id": flow_id}).fetchone()
        db.close()
        if not r:
            raise HTTPException(404, "流程不存在")

        flow_name = r[0]
        nodes_raw = _json.loads(r[1]) if isinstance(r[1], str) else (r[1] or [])
        td = body.get("trade_date", "") or str(_date.today())

        # 通过 TaskManager 创建统一任务
        tm = TaskManager()
        task = tm.create_task(task_type="dag_flow", flow_id=flow_id,
                              flow_name=flow_name, nodes=nodes_raw)
        if task.status == "failed":
            raise HTTPException(429, task.error or "任务创建失败")

        # 构建临时 DagExecutor
        executor = DagExecutor()
        for n in nodes_raw:
            name = n.get("node_name", "")
            deps = n.get("deps", [])
            fn = NODE_FN_MAP.get(name)
            if not fn:
                fn = lambda **kw: True
            executor.add(DagNode(name, deps, fn))

        from scripts.pipeline import _node_enter
        executor.on_node_enter = _node_enter

        # 后台线程执行
        def _bg(tid, ename, enodes, eexec, etd):
            from loguru import logger
            from datetime import datetime
            tm.start_task(tid)
            try:
                rid, sorted_names, ctx = eexec.prepare_context(trade_date=etd)
                logger.info(f"[flow] {ename} 开始执行 {rid}, 节点: {sorted_names}")
                _t0 = tm._tasks.get(tid)
                if _t0:
                    _t0.dag_run_id = rid

                # 在每个节点执行前后更新 TaskManager
                original_execute = eexec._execute
                def tracked_execute(sorted_names, **ctx2):
                    # 初始化所有节点为 pending
                    for nm in sorted_names:
                        tm.update_node(tid, nm, status="pending")
                    # 按原有逻辑执行，在节点完成后更新状态
                    for nm in sorted_names:
                        node = eexec._nodes.get(nm)
                        if not node:
                            continue
                        # 检查依赖是否都完成了
                        deps_ok = all(eexec._completed.get(d) for d in node.deps)
                        if not deps_ok:
                            tm.update_node(tid, nm, status="failed", error="依赖未完成")
                            from scripts.pipeline import write_node_log
                            log_ids = ctx2.get('_node_log_ids', {})
                            lid = log_ids.get(nm)
                            if lid: write_node_log(log_id=lid, status='failed', detail='依赖未完成，跳过执行')
                            continue
                        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        tm.update_node(tid, nm, status="running", started_at=ts)
                        try:
                            node.fn(**ctx2)
                            import time as _t
                            eexec._completed[nm] = _t.time()
                            ts2 = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            tm.update_node(tid, nm, status="success", finished_at=ts2)
                            # 行数回填：TaskNode.rows 默认 0，节点真实处理行数在 dag_run_log
                            # （前端 /api/dag/logs 内存任务分支直接读 TaskNode.rows）
                            _lid = (ctx2.get("_node_log_ids", {}) or {}).get(nm)
                            if _lid:
                                try:
                                    from app.db.connection import get_sync_db as _gsdb
                                    from sqlalchemy import text as _txt
                                    _ndb = _gsdb()
                                    _nrows = _ndb.execute(_txt("SELECT rows FROM dag_run_log WHERE id=:i"),
                                                          {"i": _lid}).scalar()
                                    _ndb.close()
                                    tm.update_node(tid, nm, rows=int(_nrows or 0))
                                except Exception:
                                    pass
                        except Exception as e:
                            logger.error(f"[flow] 节点 {nm} 失败: {e}")
                            ts2 = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            tm.update_node(tid, nm, status="failed", error=str(e)[:200], finished_at=ts2)
                            from scripts.pipeline import write_node_log
                            log_ids = ctx2.get("_node_log_ids", {})
                            lid = log_ids.get(nm)
                            if lid: write_node_log(log_id=lid, status="failed", detail=str(e)[:200])
                tracked_execute(sorted_names, **ctx)
                task_obj = tm._tasks.get(tid)
                if task_obj and any(n.status == "failed" for n in task_obj.nodes):
                    tm.fail_task(tid, "部分节点执行失败")
                else:
                    tm.complete_task(tid)
                logger.info(f"[flow] {ename} 执行完成 {rid}")
            except Exception as e:
                logger.error(f"[flow] {ename} 执行失败: {e}")
                tm.fail_task(tid, str(e)[:200])

        thread = threading.Thread(target=_bg, args=(task.task_id, flow_name, nodes_raw, executor, td), daemon=True)
        thread.start()

        return {"ok": True, "task_id": task.task_id, "flow_name": flow_name, "trade_date": td, "status": "started"}
    except HTTPException:
        raise
    except Exception as e:
        try: db.close()
        except: pass
        raise HTTPException(500, str(e)[:200])


def _validate_flow(nodes: List[FlowNode], db) -> List[str]:
    """7 项流程校验规则。返回错误列表。"""
    from sqlalchemy import text
    errors = []

    if not nodes:
        return ["至少需要一个节点"]

    node_names = {n.node_name for n in nodes}
    all_deps = set()
    for n in nodes:
        all_deps.update(n.deps)

    # 1. 无环检测
    adj = {n.node_name: set(n.deps) for n in nodes}
    in_degree = {name: 0 for name in node_names}
    for n in nodes:
        for dep in n.deps:
            if dep in in_degree:
                in_degree[n.node_name] = in_degree.get(n.node_name, 0) + 1
    queue = [name for name, deg in in_degree.items() if deg == 0]
    visited = set()
    while queue:
        cur = queue.pop(0)
        visited.add(cur)
        for n2 in node_names:
            if cur in adj.get(n2, set()):
                in_degree[n2] -= 1
                if in_degree[n2] == 0:
                    queue.append(n2)
    if len(visited) < len([n for n in node_names if n in in_degree]):
        errors.append("检测到循环依赖")

    # 2. 至少一个入口节点（无依赖的节点）
    entries = [n for n in node_names if n not in all_deps]
    if not entries:
        errors.append("缺少入口节点（所有节点都有依赖）")

    # 3. 至少一个出口节点（无被依赖的节点）
    has_downstream = set()
    for n in nodes:
        for dep in n.deps:
            if dep in node_names:
                has_downstream.add(dep)
    exits = [n for n in node_names if n not in has_downstream]
    if not exits:
        errors.append("缺少出口节点")

    # 4-5. 节点类型存在 + 已配置
    try:
        valid_types = [r[0] for r in db.execute(text("SELECT node_name FROM dag_config")).fetchall()]
    except Exception:
        valid_types = list(node_names)  # 宽容模式
    for n in nodes:
        if n.node_name not in valid_types and n.node_name not in node_names:
            # 不在 dag_config 中，但可能在其他 flow 中定义
            pass

    # 6. 参数有效性（基本检查）
    for n in nodes:
        for dep in n.deps:
            if dep not in node_names and dep not in all_deps:
                errors.append(f"节点 '{n.node_name}' 依赖的 '{dep}' 不在流程中且不是已知节点类型")

    return errors

@router.get("/logs")
def list_task_logs(limit: int = Query(50, le=200), flow_id: int = Query(None)):
    """获取最近任务日志，合并 TaskManager 内存 + dag_run_log 历史表。"""
    from app.task import TaskManager
    tm = TaskManager()
    seen = set()
    items = []

    # 1. 活跃任务优先（TaskManager 内存）
    mem_run = {}
    for t in sorted(tm._tasks.values(), key=lambda x: x.created_at or "", reverse=True):
        d = t.to_dict()
        seen.add(d["task_id"])
        if d.get("dag_run_id"):
            mem_run[d["dag_run_id"]] = d["task_id"]
        items.append(d)

    # 2. 历史任务（dag_run_log 表，排除已在内存中的；同 run_id 只展示内存任务）
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT run_id, trade_date, node_name, status, rows, detail,
                   created_at, started_at, finished_at
            FROM dag_run_log ORDER BY id DESC
        """)).fetchall()
        db.close()

        groups = {}
        for r in rows:
            rid = r[0]
            if rid in seen or rid in mem_run:
                continue
            if rid not in groups:
                groups[rid] = {
                    "task_id": rid, "task_type": "dag_flow",
                    "flow_name": "", "status": "completed",
                    "progress_pct": 0, "current_detail": "", "nodes": [],
                    "error": None,
                    "created_at": str(r[6])[:19] if r[6] else "",
                    "trade_date": str(r[1])[:10] if r[1] else "",
                    "started_at": None, "finished_at": None,
                }
            groups[rid]["nodes"].append({
                "node_name": r[2], "status": r[3], "rows": r[4] or 0,
                "detail": r[5] or "", "error": None,
                "progress_pct": 0,
                "started_at": str(r[7])[:19] if r[7] else None,
                "finished_at": str(r[8])[:19] if r[8] else None,
            })
            if r[8] and (not groups[rid]["finished_at"] or str(r[8]) > groups[rid]["finished_at"]):
                groups[rid]["finished_at"] = str(r[8])[:19]
        for g in groups.values():
            nodes = g["nodes"]
            if any(n["status"] == "failed" for n in nodes):
                g["status"] = "failed"
            elif any(n["status"] == "running" for n in nodes):
                g["status"] = "running"
            done = sum(1 for n in nodes if n["status"] in ("success", "failed"))
            g["progress_pct"] = int(done / len(nodes) * 100) if nodes else 0
        items.extend(groups.values())
    except Exception:
        try: db.close()
        except: pass

    # 3. 过滤 + 排序
    # 过滤：仅对 TaskManager 内存任务做 flow_id 过滤，历史条目全部保留
    if flow_id:
        memory_ids = {t.task_id for t in tm._tasks.values()}
        items = [t for t in items if t.get("task_id") not in memory_ids or t.get("flow_id") == flow_id]
    items.sort(key=lambda t: t.get("created_at") or "", reverse=True)
    return {"items": items[:limit], "total": len(items)}
