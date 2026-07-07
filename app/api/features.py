"""特征管理 API（v2.0 重构 — 迭代 2.2）。

提供特征的 CRUD、KEPL 公式校验、依赖提取、循环检测。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from pydantic import BaseModel, Field
from typing import Optional, List
import json
import uuid

from app.db.connection import get_sync_db
from app.kepl.parser import parse_kepl
from app.auth.auth import get_current_user

router = APIRouter(prefix="/features", tags=["features"])


# ── Pydantic 模型 ──

class CreateFeature(BaseModel):
    feature_name: str = Field(..., min_length=2, max_length=64)
    display_name: str = ""
    target_entity: str = Field(..., pattern="^(stock|etf|index|global)$")
    description: str = ""
    formula: str = Field(..., min_length=1)
    feature_group: str = ""
    tags: list = []
    status: str = "draft"  # draft / enabled


class UpdateFeature(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    formula: Optional[str] = None
    feature_group: Optional[str] = None
    tags: Optional[list] = None
    status: Optional[str] = None


class ValidateFormulaRequest(BaseModel):
    formula: str
    target_entity: str = "stock"


# ── 辅助函数 ──

def extract_deps_from_ast(ast: dict) -> List[str]:
    """从 KEPL parser 返回的 AST 中提取依赖列表。
    返回去重后的依赖名列表（函数名 + 字段名 + 数据源引用）。
    """
    deps = []
    seen = set()
    for fn in ast.get("functions", []):
        name = fn.get("name", "")
        if name and name not in seen:
            deps.append(name)
            seen.add(name)
    for field in ast.get("fields", []):
        name = field.get("name", "")
        if name and name not in seen:
            deps.append(name)
            seen.add(name)
    for ref in ast.get("external_refs", []):
        src = ref.get("source", "")
        field = ref.get("field", "")
        label = f"{src}.{field}" if field else src
        if label not in seen:
            deps.append(label)
            seen.add(label)
    return deps


def check_cycle(db, feature_name: str, depends_on: List[str]) -> Optional[List[str]]:
    """BFS 检测是否存在循环依赖。返回环路径，无环返回 None。"""
    from sqlalchemy import text
    rows = db.execute(text("SELECT feature_name, depends_on FROM features WHERE status != 'deprecated'")).fetchall()
    adj = {}  # feature_name → set of depended features
    for r in rows:
        name = r[0]
        deps = json.loads(r[1]) if isinstance(r[1], str) else (r[1] or [])
        adj[name] = set(deps)
    # 加入当前特征（可能尚未保存）
    adj[feature_name] = set(depends_on)

    # BFS：从 feature_name 出发，看是否能回到自身
    visited = set()
    queue = [(feature_name, [feature_name])]  # (current, path)
    while queue:
        current, path = queue.pop(0)
        for dep in adj.get(current, set()):
            if dep == feature_name:
                return path + [dep]
            if dep in visited:
                continue
            visited.add(dep)
            queue.append((dep, path + [dep]))
    return None


# ── API 端点 ──

@router.get("")
def list_features(
    entity: Optional[str] = Query(None, description="目标实体筛选: stock/etf/index/global"),
    status: Optional[str] = Query(None, description="状态筛选: draft/enabled/pending_recalc/deprecated/data_anomaly"),
    search: Optional[str] = Query(None, description="模糊搜索特征英文名/中文名"),
    page: int = 1,
    page_size: int = 20,
):
    """特征列表（分页+筛选）。"""
    db = get_sync_db()
    try:
        from sqlalchemy import text
        where = ["1=1"]
        params = {}
        if entity and entity != "all":
            where.append("target_entity = :ent")
            params["ent"] = entity
        if status and status != "all":
            where.append("status = :st")
            params["st"] = status
        if search:
            where.append("(feature_name ILIKE :s OR display_name ILIKE :s)")
            params["s"] = f"%{search}%"

        total = db.execute(text(f"SELECT COUNT(*) FROM features WHERE {' AND '.join(where)}"), params).scalar() or 0
        rows = db.execute(text(f"""
            SELECT id, feature_name, display_name, target_entity, description, formula,
                   depends_on, feature_group, tags, status,
                   total_effective_cells, missing_cells_total, abnormal_missing_cells,
                   data_completeness, latest_computed_date, data_anomaly_reason,
                   created_at, updated_at
            FROM features WHERE {' AND '.join(where)}
            ORDER BY created_at DESC
            LIMIT :lim OFFSET :off
        """), {**params, "lim": page_size, "off": (page-1)*page_size}).fetchall()

        items = []
        for r in rows:
            deps = r[6]
            if isinstance(deps, str):
                deps = json.loads(deps)
            tags = r[8]
            if isinstance(tags, str):
                tags = json.loads(tags)
            items.append({
                "id": r[0],
                "feature_name": r[1],
                "display_name": r[2],
                "target_entity": r[3],
                "description": r[4],
                "formula": r[5],
                "depends_on": deps or [],
                "feature_group": r[7] or "",
                "tags": tags or [],
                "status": r[9],
                "total_effective_cells": r[10] or 0,
                "missing_cells_total": r[11] or 0,
                "abnormal_missing_cells": r[12] or 0,
                "data_completeness": float(r[13]) if r[13] else 0,
                "latest_computed_date": str(r[14]) if r[14] else None,
                "data_anomaly_reason": r[15],
                "created_at": str(r[16])[:19] if r[16] else None,
                "updated_at": str(r[17])[:19] if r[17] else None,
            })
        db.close()
        return {"items": items, "total": total, "page": page, "page_size": page_size}
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e))


@router.post("/validate")
def validate_formula(body: ValidateFormulaRequest):
    """验证 KEPL 公式语法 + 提取依赖（不保存）。"""
    result = parse_kepl(body.formula, body.target_entity)
    deps = []
    if result.get("ok") and result.get("ast"):
        deps = extract_deps_from_ast(result["ast"])
    return {
        "ok": result.get("ok", False),
        "errors": result.get("errors", []),
        "ast": result.get("ast"),
        "dependencies": deps,
    }


@router.post("")
def create_feature(body: CreateFeature, user: str = Depends(get_current_user)):
    """新增特征。"""
    db = get_sync_db()
    try:
        from sqlalchemy import text
        import re

        # 1. 英文名校验
        if not re.match(r'^[a-z][a-z0-9_]{1,63}$', body.feature_name):
            raise HTTPException(400, "英文名必须是小写字母开头，仅包含小写字母、数字、下划线，长度 2-64")

        # 2. 唯一性
        existing = db.execute(text("SELECT id FROM features WHERE feature_name = :n"), {"n": body.feature_name}).fetchone()
        if existing:
            raise HTTPException(400, f"特征英文名 '{body.feature_name}' 已被占用")

        # 3. KEPL 公式解析
        parsed = parse_kepl(body.formula, body.target_entity)
        if not parsed.get("ok"):
            errs = parsed.get("errors", [])
            msg = "; ".join(e.get("message", str(e)) for e in errs)
            raise HTTPException(400, f"KEPL 公式语法错误: {msg}")

        # 4. 提取依赖
        deps = extract_deps_from_ast(parsed.get("ast", {}))

        # 4.5 检查依赖的函数是否已发布（draft 函数不可引用）
        fn_names = [f["name"] for f in parsed.get("ast", {}).get("functions", []) if f.get("type") == "custom"]
        if fn_names:
            draft_fns = db.execute(text(
                "SELECT name FROM functions WHERE name = ANY(:names) AND status = 'draft'"
            ), {"names": fn_names}).fetchall()
            if draft_fns:
                names = [r[0] for r in draft_fns]
                raise HTTPException(400, f"依赖的函数未发布: {', '.join(names)}。请先将函数发布后再创建特征。")

        # 5. 循环检测（enabled 状态强制；draft 仅警告不阻塞）
        cycle = check_cycle(db, body.feature_name, deps)
        if cycle:
            if body.status == "enabled":
                raise HTTPException(400, f"检测到循环依赖: {' → '.join(cycle)}")
            # draft 状态仅记录，不阻塞

        # 6. 写入
        db.execute(text("""
            INSERT INTO features (feature_name, display_name, target_entity, description, formula, depends_on, feature_group, tags, status)
            VALUES (:fn, :dn, :te, :desc, :formula, :deps, :fg, :tags, :st)
        """), {
            "fn": body.feature_name,
            "dn": body.display_name,
            "te": body.target_entity,
            "desc": body.description,
            "formula": body.formula,
            "deps": json.dumps(deps),
            "fg": body.feature_group or None,
            "tags": json.dumps(body.tags or []),
            "st": body.status or "draft",
        })
        db.commit()
        db.close()
        return {"ok": True, "feature_name": body.feature_name, "dependencies": deps}
    except HTTPException:
        db.close()
        raise
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e))


# ── 2.4 特征族 / 标签 / 全屏依赖图 ──

@router.get("/groups")
def list_feature_groups():
    """获取所有特征族列表。"""
    db = get_sync_db()
    try:
        from sqlalchemy import text
        rows = db.execute(text(
            "SELECT DISTINCT feature_group FROM features WHERE feature_group IS NOT NULL AND feature_group != '' ORDER BY feature_group"
        )).fetchall()
        db.close()
        return {"groups": [r[0] for r in rows]}
    except Exception as e:
        db.close()
        return {"groups": []}


@router.get("/tags")
def list_feature_tags():
    """获取所有标签列表。"""
    db = get_sync_db()
    try:
        from sqlalchemy import text
        rows = db.execute(text(
            "SELECT tags FROM features WHERE tags IS NOT NULL"
        )).fetchall()
        db.close()
        all_tags = set()
        for r in rows:
            t = json.loads(r[0]) if isinstance(r[0], str) else (r[0] or [])
            all_tags.update(t)
        return {"tags": sorted(all_tags)}
    except Exception:
        db.close()
        return {"tags": []}


@router.get("/dependency-graph")
def get_dependency_graph():
    """获取全量依赖图（用于 D3/ECharts 渲染）。返回 nodes + edges。"""
    db = get_sync_db()
    try:
        from sqlalchemy import text
        rows = db.execute(text(
            "SELECT feature_name, display_name, target_entity, status, depends_on, feature_group, tags FROM features"
        )).fetchall()
        db.close()

        nodes = []
        node_map = {}
        edges = []

        for r in rows:
            name = r[0]
            deps = json.loads(r[4]) if isinstance(r[4], str) else (r[4] or [])
            tags = json.loads(r[6]) if isinstance(r[6], str) else (r[6] or [])

            # 节点分层：原始字段(0) / 基础特征(1) / 复合特征(2) / 顶层(3)
            level = 0 if not deps else 1
            if any(d not in ('close','open','high','low','volume','amount','turnover') for d in deps):
                level = 2

            nodes.append({
                "id": name,
                "name": name,
                "display_name": r[1] or name,
                "entity": r[2],
                "status": r[3],
                "group": r[5] or "",
                "tags": tags,
                "level": level,
            })
            node_map[name] = True

            for dep in deps:
                edges.append({"source": dep, "target": name})

        # 过滤 edges 中 source 不存在的边（原始字段不在 nodes 中）
        edges = [e for e in edges if e["source"] in node_map]

        return {"nodes": nodes, "edges": edges}
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e))



@router.get("/{feature_id}")
def get_feature(feature_id: int):
    """获取特征详情（含上游依赖 + 下游引用）。"""
    db = get_sync_db()
    try:
        from sqlalchemy import text
        r = db.execute(text("""
            SELECT id, feature_name, display_name, target_entity, description, formula,
                   depends_on, feature_group, tags, status,
                   total_effective_cells, missing_cells_total, abnormal_missing_cells,
                   data_completeness, latest_computed_date, data_anomaly_reason,
                   created_at, updated_at
            FROM features WHERE id = :id
        """), {"id": feature_id}).fetchone()
        if not r:
            db.close()
            raise HTTPException(404, "特征不存在")

        name = r[1]
        deps_raw = r[6]
        if isinstance(deps_raw, str):
            deps_raw = json.loads(deps_raw)
        tags_raw = r[8]
        if isinstance(tags_raw, str):
            tags_raw = json.loads(tags_raw)

        # 查询下游依赖
        ds_rows = db.execute(text(
            "SELECT feature_name, display_name, status FROM features WHERE status != 'deprecated' AND depends_on @> :dep"
        ), {"dep": json.dumps([name])}).fetchall()
        downstream = [{"feature_name": d[0], "display_name": d[1], "status": d[2]} for d in ds_rows]

        db.close()
        return {
            "id": r[0],
            "feature_name": r[1],
            "display_name": r[2],
            "target_entity": r[3],
            "description": r[4],
            "formula": r[5],
            "depends_on": deps_raw or [],
            "feature_group": r[7] or "",
            "tags": tags_raw or [],
            "downstream": downstream,
            "status": r[9],
            "total_effective_cells": r[10] or 0,
            "missing_cells_total": r[11] or 0,
            "abnormal_missing_cells": r[12] or 0,
            "data_completeness": float(r[13]) if r[13] else 0,
            "latest_computed_date": str(r[14]) if r[14] else None,
            "data_anomaly_reason": r[15],
            "created_at": str(r[16])[:19] if r[16] else None,
            "updated_at": str(r[17])[:19] if r[17] else None,
        }
    except HTTPException:
        db.close()
        raise
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e))


@router.get("/{feature_id}/quality")
def get_feature_quality(feature_id: int):
    """获取特征数据质量指标。"""
    db = get_sync_db()
    try:
        from sqlalchemy import text
        r = db.execute(text("""
            SELECT feature_name, status,
                   total_effective_cells, missing_cells_total, abnormal_missing_cells,
                   data_completeness, latest_computed_date, data_anomaly_reason
            FROM features WHERE id = :id
        """), {"id": feature_id}).fetchone()
        db.close()
        if not r:
            raise HTTPException(404, "特征不存在")

        # 计算新鲜度（距今天数）
        fresh = None
        if r[6]:
            from datetime import date
            fresh = (date.today() - r[6]).days

        return {
            "feature_name": r[0],
            "status": r[1],
            "total_effective_cells": r[2] or 0,
            "missing_cells_total": r[3] or 0,
            "abnormal_missing_cells": r[4] or 0,
            "data_completeness": float(r[5]) if r[5] else 0,
            "latest_computed_date": str(r[6]) if r[6] else None,
            "stale_days": fresh,  # 距上次计算的天数；None 表示从未计算
            "stale_warning": fresh is not None and fresh > 5,
            "completeness_warning": float(r[5] or 0) < 0.6,
            "data_anomaly_reason": r[7],
        }
    except HTTPException:
        db.close()
        raise
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e))


@router.put("/{feature_id}")
def update_feature(feature_id: int, body: UpdateFeature, user: str = Depends(get_current_user)):
    """更新特征（部分更新）。
    - 不可修改 target_entity 和 feature_name
    - 修改 formula 时重新解析依赖，并标记本特征和下游为 pending_recalc
    """
    db = get_sync_db()
    try:
        from sqlalchemy import text

        # 查当前记录
        current = db.execute(text("SELECT * FROM features WHERE id = :id"), {"id": feature_id}).fetchone()
        if not current:
            db.close()
            raise HTTPException(404, "特征不存在")

        current_name = current[1]  # feature_name
        current_status = current[7]  # status
        current_formula = current[5]  # formula

        updates = []
        params = {"id": feature_id}

        if body.display_name is not None:
            updates.append("display_name = :dn")
            params["dn"] = body.display_name

        if body.description is not None:
            updates.append("description = :desc")
            params["desc"] = body.description

        if body.feature_group is not None:
            updates.append("feature_group = :fg")
            params["fg"] = body.feature_group or None

        if body.tags is not None:
            updates.append("tags = :tags")
            params["tags"] = json.dumps(body.tags or [])

        if body.status is not None:
            # 状态流转校验：弃用时检查下游
            if body.status == "deprecated" and current_status != "deprecated":
                downstream = db.execute(text(
                    "SELECT feature_name FROM features WHERE status != 'deprecated' AND depends_on @> :dep"
                ), {"dep": json.dumps([current_name])}).fetchall()
                if downstream:
                    names = [d[0] for d in downstream]
                    db.close()
                    raise HTTPException(400, f"无法弃用 '{current_name}'，以下特征依赖它: {', '.join(names)}")
            updates.append("status = :st")
            params["st"] = body.status

        if body.formula is not None:
            # 重新解析公式
            parsed = parse_kepl(body.formula, current[3])  # target_entity
            if not parsed.get("ok"):
                errs = parsed.get("errors", [])
                msg = "; ".join(e.get("message", str(e)) for e in errs)
                db.close()
                raise HTTPException(400, f"KEPL 公式语法错误: {msg}")

            new_deps = extract_deps_from_ast(parsed.get("ast", {}))

            # 检查依赖函数是否已发布
            fn_names = [f["name"] for f in parsed.get("ast", {}).get("functions", []) if f.get("type") == "custom"]
            if fn_names:
                draft_fns = db.execute(text(
                    "SELECT name FROM functions WHERE name = ANY(:names) AND status = 'draft'"
                ), {"names": fn_names}).fetchall()
                if draft_fns:
                    names = [r[0] for r in draft_fns]
                    db.close()
                    raise HTTPException(400, f"依赖的函数未发布: {', '.join(names)}。请先将函数发布后再创建特征。")

            # 循环检测
            cycle = check_cycle(db, current_name, new_deps)
            if cycle:
                db.close()
                raise HTTPException(400, f"检测到循环依赖: {' → '.join(cycle)}")

            updates.append("formula = :formula")
            params["formula"] = body.formula
            updates.append("depends_on = :deps")
            params["deps"] = json.dumps(new_deps)

            # 旧依赖 vs 新依赖不同 → 标记本特征为 pending_recalc
            old_deps_raw = current[6]
            old_deps = json.loads(old_deps_raw) if isinstance(old_deps_raw, str) else (old_deps_raw or [])
            if sorted(old_deps) != sorted(new_deps):
                updates.append("status = 'pending_recalc'")
                # 同时标记所有下游为 pending_recalc
                _cascade_pending(db, current_name)

        if not updates:
            db.close()
            return {"ok": True, "message": "无变更"}

        updates.append("updated_at = CURRENT_TIMESTAMP")
        sql = f"UPDATE features SET {', '.join(updates)} WHERE id = :id"
        db.execute(text(sql), params)
        db.commit()
        db.close()
        return {"ok": True}
    except HTTPException:
        db.close()
        raise
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e))


@router.delete("/{feature_id}")
def delete_feature(feature_id: int, user: str = Depends(get_current_user)):
    """软删除特征（状态改为 deprecated）。"""
    db = get_sync_db()
    try:
        from sqlalchemy import text
        current = db.execute(text("SELECT feature_name, status FROM features WHERE id = :id"), {"id": feature_id}).fetchone()
        if not current:
            db.close()
            raise HTTPException(404, "特征不存在")

        # 检查下游依赖
        name = current[0]
        downstream = db.execute(text(
            "SELECT feature_name FROM features WHERE status != 'deprecated' AND depends_on @> :dep"
        ), {"dep": json.dumps([name])}).fetchall()
        if downstream:
            names = [d[0] for d in downstream]
            db.close()
            raise HTTPException(400, f"无法弃用 '{name}'，以下特征依赖它: {', '.join(names)}")

        db.execute(text("UPDATE features SET status='deprecated', updated_at=CURRENT_TIMESTAMP WHERE id = :id"), {"id": feature_id})
        db.commit()
        db.close()
        return {"ok": True}
    except HTTPException:
        db.close()
        raise
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e))


# ── 数据预览 / 状态管理 ──

class StatsBody(BaseModel):
    total_effective_cells: int = 0
    missing_cells_total: int = 0
    pending_cells_total: int = 0
    data_completeness: Optional[float] = None
    latest_computed_date: Optional[str] = None


class StatusBody(BaseModel):
    status: str = Field(..., pattern="^(draft|enabled|pending_recalc|deprecated|data_anomaly)$")


@router.get("/{feature_id}/data")
def get_feature_data(
    feature_id: int,
    code: Optional[str] = Query(None, description="实体代码，如 000001"),
    page: int = 1,
    page_size: int = 50,
):
    """分页预览特征在宽表中的实际数值（按日期降序）。"""
    db = get_sync_db()
    try:
        from sqlalchemy import text
        # 查特征信息
        feat = db.execute(text(
            "SELECT feature_name, target_entity FROM features WHERE id = :id"
        ), {"id": feature_id}).fetchone()
        if not feat:
            db.close()
            raise HTTPException(404, "特征不存在")

        feature_name = feat[0]
        entity = feat[1]

        # 从 feature_values 表查询特征值
        base_params = {"fn": feature_name, "lim": page_size, "off": (page-1)*page_size}
        if entity == "global":
            sql = """
                SELECT '' as stock_code, trade_date, value
                FROM feature_values
                WHERE feature_name = :fn
                ORDER BY trade_date DESC
                LIMIT :lim OFFSET :off
            """
            count_sql = "SELECT COUNT(*) FROM feature_values WHERE feature_name = :fn"
        elif code:
            sql = """
                SELECT stock_code, trade_date, value
                FROM feature_values
                WHERE feature_name = :fn AND stock_code = :code
                ORDER BY trade_date DESC
                LIMIT :lim OFFSET :off
            """
            count_sql = "SELECT COUNT(*) FROM feature_values WHERE feature_name = :fn AND stock_code = :code"
            base_params["code"] = code
        else:
            sql = """
                SELECT stock_code, trade_date, value
                FROM feature_values
                WHERE feature_name = :fn
                ORDER BY trade_date DESC, stock_code
                LIMIT :lim OFFSET :off
            """
            count_sql = "SELECT COUNT(*) FROM feature_values WHERE feature_name = :fn"

        try:
            total = db.execute(text(count_sql), {k:v for k,v in base_params.items() if k not in ("lim","off")}).scalar() or 0
            rows = db.execute(text(sql), base_params).fetchall()

            items = []
            for r in rows:
                if entity == "global":
                    items.append({"trade_date": str(r[0]), "value": float(r[1]) if r[1] is not None else None})
                else:
                    items.append({"stock_code": r[0], "trade_date": str(r[1]), "value": float(r[2]) if r[2] is not None else None})

            db.close()
            msg = None
            if total == 0:
                msg = "该特征数据尚未计算，请通过 DAG 触发特征计算流水线"
            return {"items": items, "total": total, "page": page, "page_size": page_size, "entity": entity, "empty_reason": msg}
        except Exception:
            # 宽表尚未创建，返回空
            db.close()
            return {"items": [], "total": 0, "page": page, "page_size": page_size, "entity": entity, "empty_reason": "特征值尚未计算，请触发特征计算 DAG"}
    except HTTPException:
        db.close()
        raise
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e))


@router.post("/{feature_id}/stats")
def update_feature_stats(feature_id: int, body: StatsBody, user: str = Depends(get_current_user)):
    """DAG 回写特征数据质量统计。"""
    db = get_sync_db()
    try:
        from sqlalchemy import text
        current = db.execute(text("SELECT id FROM features WHERE id = :id"), {"id": feature_id}).fetchone()
        if not current:
            db.close()
            raise HTTPException(404, "特征不存在")

        # 自动计算完整度
        completeness = body.data_completeness
        if completeness is None and body.total_effective_cells > 0:
            completeness = 1.0 - body.missing_cells_total / body.total_effective_cells
            completeness = round(max(0, min(1, completeness)), 4)

        date_val = body.latest_computed_date

        db.execute(text("""
            UPDATE features SET
                total_effective_cells = :tot,
                missing_cells_total = :miss,
                pending_cells_total = :pend,
                data_completeness = COALESCE(:comp, data_completeness),
                latest_computed_date = COALESCE(:dt, latest_computed_date),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
        """), {
            "id": feature_id,
            "tot": body.total_effective_cells,
            "miss": body.missing_cells_total,
            "pend": body.pending_cells_total,
            "comp": completeness,
            "dt": date_val,
        })

        # 自动质量监控规则
        if completeness is not None:
            if completeness < 0.8:
                db.execute(text(
                    "UPDATE features SET status='data_anomaly', data_anomaly_reason='数据完整度低于80%，请检查数据源' WHERE id=:id AND status='enabled'"
                ), {"id": feature_id})
            elif completeness >= 0.9:
                db.execute(text(
                    "UPDATE features SET status='enabled', data_anomaly_reason=NULL WHERE id=:id AND status='data_anomaly'"
                ), {"id": feature_id})

        db.commit()
        db.close()
        return {"ok": True}
    except HTTPException:
        db.close()
        raise
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e))


@router.patch("/{feature_id}/status")
def update_feature_status(feature_id: int, body: StatusBody, user: str = Depends(get_current_user)):
    """手动切换特征状态（含下游检查）。"""
    db = get_sync_db()
    try:
        from sqlalchemy import text
        current = db.execute(text("SELECT feature_name, status FROM features WHERE id = :id"), {"id": feature_id}).fetchone()
        if not current:
            db.close()
            raise HTTPException(404, "特征不存在")

        name = current[0]
        old_status = current[1]

        if body.status == old_status:
            db.close()
            return {"ok": True, "message": "状态未变化"}

        # 弃用检查
        if body.status == "deprecated":
            downstream = db.execute(text(
                "SELECT feature_name FROM features WHERE status != 'deprecated' AND depends_on @> :dep"
            ), {"dep": json.dumps([name])}).fetchall()
            if downstream:
                names = [d[0] for d in downstream]
                db.close()
                raise HTTPException(400, f"无法弃用 '{name}'，以下特征依赖它: {', '.join(names)}")

        # 执行状态更新
        db.execute(text("UPDATE features SET status=:st, updated_at=CURRENT_TIMESTAMP WHERE id=:id"), {"st": body.status, "id": feature_id})

        # 级联：弃用时标记下游
        if body.status == "deprecated":
            _cascade_pending(db, name)

        db.commit()
        db.close()
        return {"ok": True, "new_status": body.status}
    except HTTPException:
        db.close()
        raise
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e))


def _cascade_pending(db, upstream_name: str):
    """递归标记所有依赖 upstream_name 的下游特征为 pending_recalc。"""
    from sqlalchemy import text
    visited = set()
    queue = [upstream_name]

    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)

        # 查找 depend_on 包含 current 的特征
        rows = db.execute(text(
            "SELECT feature_name FROM features WHERE status NOT IN ('deprecated','draft') AND depends_on @> :dep"
        ), {"dep": json.dumps([current])}).fetchall()

        for r in rows:
            downstream_name = r[0]
            if downstream_name not in visited:
                db.execute(text(
                    "UPDATE features SET status='pending_recalc', updated_at=CURRENT_TIMESTAMP WHERE feature_name = :n"
                ), {"n": downstream_name})
                queue.append(downstream_name)
    db.commit()


# ── 特征补数（手动触发单特征计算）──

import threading as _threading
import time as _time
from datetime import date as _date

_compute_tasks: dict = {}  # task_id → {status, feature_id, progress_pct, current_date, ...}
_compute_lock = _threading.Lock()


class ComputeRangeBody(BaseModel):
    start_date: str = ""
    end_date: str = ""
    force: bool = False  # True=覆盖已有数据, False=跳过已有


@router.post("/{feature_id}/compute-range")
def compute_range(feature_id: int, body: ComputeRangeBody, user: str = Depends(get_current_user)):
    """手动触发单特征补数计算。"""
    from sqlalchemy import text
    db = get_sync_db()
    try:
        feat = db.execute(text(
            "SELECT feature_name, target_entity, formula FROM features WHERE id = :id"
        ), {"id": feature_id}).fetchone()
        db.close()
        if not feat:
            raise HTTPException(404, "特征不存在")

        task_id = str(uuid.uuid4())[:8]
        with _compute_lock:
            _compute_tasks[task_id] = {
                "status": "running", "feature_id": feature_id,
                "feature_name": feat[0], "start_date": body.start_date,
                "end_date": body.end_date, "force": body.force,
                "progress_pct": 0, "current_date": "", "total_dates": 0,
                "started_at": _time.time(),
            }

        thread = _threading.Thread(target=_run_compute, args=(
            task_id, feature_id, feat[0], feat[1], feat[2],
            body.start_date or None, body.end_date or None, body.force
        ), daemon=True)
        thread.start()

        # 唤醒 WS 广播
        from app.signal import wake_dag_broadcast
        wake_dag_broadcast()

        return {"ok": True, "task_id": task_id, "feature_name": feat[0], "status": "started"}
    except HTTPException:
        raise
    except Exception as e:
        db.close() if 'db' in dir() else None
        raise HTTPException(500, str(e))


@router.post("/{feature_id}/recompute-stats")
def recompute_stats(feature_id: int, user: str = Depends(get_current_user)):
    """原子级重算特征统计（不触发计算，仅基于 feature_values 现有数据更新诊断）。"""
    _update_feature_stats_after_compute(feature_id)
    return {"ok": True, "message": "诊断已更新"}


@router.get("/{feature_id}/compute-status")
def compute_status(feature_id: int):
    """查询当前特征的补数进度。"""
    with _compute_lock:
        for t in _compute_tasks.values():
            if t["feature_id"] == feature_id and t["status"] == "running":
                return {"has_task": True, **{k: v for k, v in t.items()}}
    return {"has_task": False}


def _run_compute(task_id, feature_id, feature_name, target_entity, formula, start_date, end_date, force):
    """后台线程：执行特征计算。"""
    from app.db.connection import get_sync_db
    from scripts.feature_compute import compute_feature
    from sqlalchemy import text
    from loguru import logger as _logger

    try:
        db = get_sync_db()

        def _report(pct, msg):
            with _compute_lock:
                _compute_tasks[task_id].update({"progress_pct": pct, "current_date": msg})
                from app.signal import wake_dag_broadcast
                wake_dag_broadcast()

        _report(5, "拉取行情数据...")

        # 统一批量模式：拉取全量 daily_quote → DataFrame → 批量计算 → 批量写入
        # ON CONFLICT DO UPDATE 已处理覆盖/跳过逻辑
        _report(20, "计算特征值...")
        r = compute_feature(db, feature_name, formula, target_entity,
                           start_date=start_date, end_date=end_date)
        rows_count = r.get('rows', 0)
        _logger.info(f"[compute] {feature_name} range {start_date}~{end_date}: {rows_count} rows")

        if not r.get('ok'):
            _report(0, f"计算失败: {r.get('error', '未知错误')}")
            db.close()
            return

        _report(70, f"已计算 {rows_count} 行")

        db.close()

        # 数据诊断：总格子、正常缺失(停牌+lookback)、异常缺失、完整度
        _report(80, "诊断：计算总格子/缺失格子...")
        _update_feature_stats_after_compute(feature_id)

        _report(100, "完成")
        with _compute_lock:
            _compute_tasks[task_id]["status"] = "completed"
    except Exception as e:
        _logger.error(f"[compute] {feature_name} 失败: {e}")
        with _compute_lock:
            _compute_tasks[task_id]["status"] = "failed"
            _compute_tasks[task_id]["error"] = str(e)


def _update_feature_stats_after_compute(feature_id: int):
    """计算完成后更新特征的数据统计。

    总格子     = Σ 每个股票(上市日→min(退市日,今天)) 之间的交易日数（已剔除未上市/已退市）
    正常缺失   = 停牌天数 + 活跃股票数 × lookback（天然无解的窗口期）
    异常缺失   = 总格子 - 正常缺失 - 已计算（需要排查的）
    完整度     = 已计算 / 总格子
    """
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        feat = db.execute(text(
            "SELECT f.feature_name, f.target_entity, f.depends_on "
            "FROM features f WHERE f.id = :id"
        ), {"id": feature_id}).fetchone()
        if not feat:
            return
        fn, entity, depends_on = feat[0], feat[1], feat[2] or []

        # lookback：取依赖函数的最大 lookback（features 表没有 lookback，从 functions 取）
        if isinstance(depends_on, str):
            depends_on = json.loads(depends_on)
        max_lookback = 0
        if depends_on:
            lb_rows = db.execute(text(
                "SELECT COALESCE(MAX(lookback), 0) FROM functions WHERE name = ANY(:names)"
            ), {"names": depends_on}).scalar() or 0
            max_lookback = max(max_lookback, lb_rows)

        # 1. 理论总格子：每只股票从上市到退市之间的交易日数
        if entity == 'global':
            total_cells = db.execute(text(
                "SELECT COUNT(*) FROM trade_calendar WHERE cal_date >= '2000-01-01' AND cal_date <= CURRENT_DATE AND is_trade_day = true"
            )).scalar() or 0
            stock_count = 1
        else:
            ent_filter = ""
            if entity == 'index':
                ent_filter = "AND sm.stock_type='index'"
            elif entity == 'etf':
                ent_filter = "AND sm.stock_type='etf'"
            else:
                ent_filter = "AND sm.stock_type='stock' AND sm.status='N' AND sm.exchange IN ('SSE','SZSE')"

            # 用 entity_meta 的 ipo_date/delist_date 精确计算
            total_cells = db.execute(text(f"""
                SELECT COALESCE(SUM(
                    (SELECT COUNT(*) FROM trade_calendar tc
                     WHERE tc.cal_date BETWEEN COALESCE(em.ipo_date, '2000-01-01')
                         AND COALESCE(em.delist_date, CURRENT_DATE)
                     AND tc.is_trade_day = true)
                ), 0)
                FROM entity_meta em
                JOIN stock_master sm ON sm.stock_code = em.stock_code AND sm.stock_type = em.stock_type
                WHERE sm.status = 'N' {ent_filter}
            """)).scalar() or 0

            stock_count = db.execute(text(f"""
                SELECT COUNT(*) FROM stock_master sm WHERE sm.status = 'N' {ent_filter}
            """)).scalar() or 1

        # 2. 实际已计算
        actual = db.execute(text(
            "SELECT COUNT(*) FROM feature_values WHERE feature_name = :fn"
        ), {"fn": fn}).scalar() or 0

        # 3. 正常缺失 = 停牌格 + lookback 窗口
        # 停牌格：用 is_suspended 列（如果有）或从 trade_calendar 反推
        suspended_cells = 0
        try:
            suspended_cells = db.execute(text("""
                SELECT COALESCE(COUNT(*), 0)
                FROM trade_calendar tc
                WHERE tc.cal_date >= '2000-01-01'
                AND tc.cal_date <= CURRENT_DATE
                AND tc.is_suspended = true
            """)).scalar() or 0
            if entity != 'global' and stock_count > 0:
                # 停牌是 per-stock 的，简化：取停牌日数 × 股票数近似
                # 精确实现需要逐股票关联，此处用 DAG stats 节点做
                suspended_cells = suspended_cells * stock_count
        except Exception:
            pass

        # lookback 窗口缺失 = 每只股票前 lookback 天无数据
        lookback_missing = max_lookback * stock_count if entity != 'global' else max_lookback

        normal_missing = suspended_cells + lookback_missing

        # 4. 异常缺失
        abnormal = max(0, total_cells - normal_missing - actual)

        completeness = round(actual / total_cells, 4) if total_cells > 0 else 0

        db.execute(text("""
            UPDATE features SET
                total_effective_cells = :tot,
                data_completeness = :comp,
                missing_cells_total = :norm,
                abnormal_missing_cells = :abn,
                latest_computed_date = CURRENT_DATE,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
        """), {
            "tot": total_cells, "comp": completeness,
            "norm": normal_missing, "abn": abnormal,
            "id": feature_id,
        })
        db.commit()
    except Exception as e:
        pass
    finally:
        db.close()


# 暴露补数任务列表给 WS 广播使用
def get_active_compute_tasks():
    with _compute_lock:
        return [{"task_id": k, **v} for k, v in _compute_tasks.items() if v["status"] == "running"]


@router.post("/check-stats-integrity")
def check_stats_integrity(user: str = Depends(get_current_user)):
    """轻量校验：对每个特征，用 feature_values 的实际行数与存储的 total_effective_cells 对比。
    使用 feature_name 索引，单特征 O(log N)，不扫全表。
    """
    from sqlalchemy import text
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT id, feature_name, total_effective_cells, missing_cells_total, abnormal_missing_cells, data_completeness, status
            FROM features
            WHERE total_effective_cells > 0 OR missing_cells_total > 0 OR abnormal_missing_cells > 0
        """)).fetchall()

        fixed = []
        for r in rows:
            fid, name, stored_cells, missing, abnormal, stored_comp, status = r
            # 用索引查询该特征在 feature_values 中的实际行数
            actual = db.execute(text(
                "SELECT COUNT(*) FROM feature_values WHERE feature_name = :fn"
            ), {"fn": name}).scalar() or 0

            if actual == 0 and (stored_cells > 0 or missing > 0 or abnormal > 0):
                # 严重不一致：元数据有值但实际表为空 → 全部归零
                db.execute(text("""
                    UPDATE features SET status='data_anomaly',
                        data_anomaly_reason='feature_values 无数据，请执行特征计算',
                        total_effective_cells=0, data_completeness=0,
                        missing_cells_total=0, abnormal_missing_cells=0,
                        updated_at=CURRENT_TIMESTAMP WHERE id=:id
                """), {"id": fid})
                fixed.append(name)
            elif actual > 0:
                ratio = stored_cells / max(actual, 1)
                if (ratio < 0.8 or ratio > 1.2) and status != 'data_anomaly':
                    db.execute(text("""
                        UPDATE features SET status='data_anomaly',
                            data_anomaly_reason='统计不一致：存储=' || :c || ' 实际=' || :a,
                            updated_at=CURRENT_TIMESTAMP WHERE id=:id
                    """), {"c": str(stored_cells), "a": str(actual), "id": fid})
                    fixed.append(name)

        db.commit()
        db.close()
        return {"ok": True, "checked": len(rows), "fixed": len(fixed), "names": fixed}
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e))
