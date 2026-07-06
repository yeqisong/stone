"""特征管理 API（v2.0 重构 — 迭代 2.2）。

提供特征的 CRUD、KEPL 公式校验、依赖提取、循环检测。
"""
from fastapi import APIRouter, HTTPException, Query, Body
from pydantic import BaseModel, Field
from typing import Optional, List
import json

from app.db.connection import get_sync_db
from app.kepl.parser import parse_kepl

router = APIRouter(prefix="/features", tags=["features"])


# ── Pydantic 模型 ──

class CreateFeature(BaseModel):
    feature_name: str = Field(..., min_length=2, max_length=64)
    display_name: str = ""
    target_entity: str = Field(..., pattern="^(stock|etf|index|global)$")
    description: str = ""
    formula: str = Field(..., min_length=1)
    status: str = "draft"  # draft / enabled


class UpdateFeature(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    formula: Optional[str] = None
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
                   depends_on, status,
                   total_effective_cells, missing_cells_total, pending_cells_total,
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
            items.append({
                "id": r[0],
                "feature_name": r[1],
                "display_name": r[2],
                "target_entity": r[3],
                "description": r[4],
                "formula": r[5],
                "depends_on": deps or [],
                "status": r[7],
                "total_effective_cells": r[8] or 0,
                "missing_cells_total": r[9] or 0,
                "pending_cells_total": r[10] or 0,
                "data_completeness": float(r[11]) if r[11] else 0,
                "latest_computed_date": str(r[12]) if r[12] else None,
                "data_anomaly_reason": r[13],
                "created_at": str(r[14])[:19] if r[14] else None,
                "updated_at": str(r[15])[:19] if r[15] else None,
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
def create_feature(body: CreateFeature):
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

        # 5. 循环检测（enabled 状态强制；draft 仅警告不阻塞）
        cycle = check_cycle(db, body.feature_name, deps)
        if cycle:
            if body.status == "enabled":
                raise HTTPException(400, f"检测到循环依赖: {' → '.join(cycle)}")
            # draft 状态仅记录，不阻塞

        # 6. 写入
        db.execute(text("""
            INSERT INTO features (feature_name, display_name, target_entity, description, formula, depends_on, status)
            VALUES (:fn, :dn, :te, :desc, :formula, :deps, :st)
        """), {
            "fn": body.feature_name,
            "dn": body.display_name,
            "te": body.target_entity,
            "desc": body.description,
            "formula": body.formula,
            "deps": json.dumps(deps),
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


@router.get("/{feature_id}")
def get_feature(feature_id: int):
    """获取特征详情（含上游依赖 + 下游引用）。"""
    db = get_sync_db()
    try:
        from sqlalchemy import text
        r = db.execute(text("""
            SELECT id, feature_name, display_name, target_entity, description, formula,
                   depends_on, status,
                   total_effective_cells, missing_cells_total, pending_cells_total,
                   data_completeness, latest_computed_date, data_anomaly_reason,
                   created_at, updated_at
            FROM features WHERE id = :id
        """), {"id": feature_id}).fetchone()
        if not r:
            db.close()
            raise HTTPException(404, "特征不存在")

        name = r[1]
        deps = r[6]
        if isinstance(deps, str):
            deps = json.loads(deps)

        # 查询下游依赖（哪些特征依赖本特征）
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
            "depends_on": deps or [],
            "downstream": downstream,
            "status": r[7],
            "total_effective_cells": r[8] or 0,
            "missing_cells_total": r[9] or 0,
            "pending_cells_total": r[10] or 0,
            "data_completeness": float(r[11]) if r[11] else 0,
            "latest_computed_date": str(r[12]) if r[12] else None,
            "data_anomaly_reason": r[13],
            "created_at": str(r[14])[:19] if r[14] else None,
            "updated_at": str(r[15])[:19] if r[15] else None,
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
                   total_effective_cells, missing_cells_total, pending_cells_total,
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
            "pending_cells_total": r[4] or 0,
            "data_completeness": float(r[5]) if r[5] else 0,
            "latest_computed_date": str(r[6]) if r[6] else None,
            "stale_days": fresh,  # 距上次计算的天数；None 表示从未计算
            "stale_warning": fresh is not None and fresh > 5,
            "completeness_warning": float(r[5] or 0) < 0.8,
            "data_anomaly_reason": r[7],
        }
    except HTTPException:
        db.close()
        raise
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e))


@router.put("/{feature_id}")
def update_feature(feature_id: int, body: UpdateFeature):
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
def delete_feature(feature_id: int):
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
