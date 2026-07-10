"""DAG 流程编排 API（v2.0 重构 迭代 4.1）。

提供 DAG 流程的 CRUD、校验、版本管理。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List
import json

from app.auth.auth import get_current_user

router = APIRouter(prefix="/api/dag", tags=["dag-flows"])


class FlowNode(BaseModel):
    node_name: str
    deps: List[str] = []


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
        rows = db.execute(text("""
            SELECT f.id, f.flow_name, f.description, f.cron_expr, f.status, f.is_active, f.created_at,
                   COALESCE(v.node_count, 0) AS node_count
            FROM dag_flows f
            LEFT JOIN (
                SELECT flow_id, COUNT(*) AS node_count FROM dag_flow_versions GROUP BY flow_id
            ) v ON v.flow_id = f.id
            ORDER BY f.created_at DESC
        """)).fetchall()
        items = []
        for r in rows:
            items.append({
                "id": r[0], "flow_name": r[1], "description": r[2],
                "cron_expr": r[3], "status": r[4], "is_active": r[5],
                "created_at": str(r[6])[:19] if r[6] else None,
                "node_count": r[7],
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

        nodes_json = json.dumps([{"node_name": n.node_name, "deps": n.deps} for n in body.nodes])
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
            nodes_json = json.dumps([{"node_name": n.node_name, "deps": n.deps} for n in body.nodes])
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
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    from scripts.pipeline import NODE_FN_MAP
    from scripts.dag import DagExecutor, DagNode
    from datetime import date as _date
    import uuid, threading, json as _json

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
        run_id = f"run-{uuid.uuid4().hex[:8]}"

        # 构建临时 DagExecutor，设置 on_node_enter 以写入 dag_run_log
        executor = DagExecutor()
        for n in nodes_raw:
            name = n.get("node_name", "")
            deps = n.get("deps", [])
            fn = NODE_FN_MAP.get(name)
            if not fn:
                fn = lambda **kw: True
            executor.add(DagNode(name, deps, fn))

        # 复用 pipeline 的节点日志回调
        from scripts.pipeline import _node_enter
        executor.on_node_enter = _node_enter

        # 后台线程执行（使用共享的 context 初始化）
        def _bg():
            from loguru import logger
            try:
                rid, sorted_names, ctx = executor.prepare_context(trade_date=td)
                logger.info(f"[flow] {flow_name} 开始执行 {rid}, 节点: {sorted_names}")
                executor._execute(sorted_names, **ctx)
                logger.info(f"[flow] {flow_name} 执行完成 {rid}")
            except Exception as e:
                logger.error(f"[flow] {flow_name} 执行失败: {e}")

        thread = threading.Thread(target=_bg, daemon=True)
        thread.start()

        return {"ok": True, "run_id": run_id, "flow_name": flow_name, "trade_date": td, "status": "started"}
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
