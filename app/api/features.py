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
    ic_status: Optional[str] = Query(None, description="IC 决策筛选: candidate/included/excluded"),
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
        if ic_status and ic_status != "all":
            where.append("COALESCE(ic_status,'candidate') = :icst")
            params["icst"] = ic_status
        if search:
            where.append("(feature_name ILIKE :s OR display_name ILIKE :s)")
            params["s"] = f"%{search}%"

        total = db.execute(text(f"SELECT COUNT(*) FROM features WHERE {' AND '.join(where)}"), params).scalar() or 0
        rows = db.execute(text(f"""
            SELECT id, feature_name, display_name, target_entity, description, formula,
                   depends_on, feature_group, tags, status,
                   total_effective_cells, missing_cells_total, abnormal_missing_cells,
                   data_completeness, latest_computed_date, data_anomaly_reason,
                   created_at, updated_at, COALESCE(ic_status,'candidate')
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
                "ic_status": r[18],
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


# ── 因子 IC 体检（board 必须声明在 /{feature_id} 之前，否则被 int 路径参数吞掉）──

@router.get("/ic/board")
def ic_board(horizon: int = Query(10, ge=1, le=250), user: str = Depends(get_current_user)):
    """因子 IC 排行板：每个因子取该 horizon 最近一次检验结果 + 红绿灯 + 决策状态。"""
    from sqlalchemy import text
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT DISTINCT ON (f.feature_name)
                   f.id, f.feature_name, f.display_name, f.status, f.ic_status,
                   f.target_entity, s.horizon, s.rank_ic_mean, s.rank_ic_ir, s.ic_win_rate,
                   s.t_stat, s.direction, s.sample_days, s.avg_names, s.traffic,
                   s.val_start, s.val_end, s.created_at
            FROM features f
            LEFT JOIN factor_ic_stats s
              ON s.feature_name = f.feature_name AND s.horizon = :h
            WHERE f.target_entity = 'stock'
            ORDER BY f.feature_name, s.created_at DESC NULLS LAST
        """), {"h": horizon}).fetchall()
        db.close()
        board = []
        for r in rows:
            board.append({
                'feature_id': r[0], 'feature_name': r[1], 'display_name': r[2],
                'feature_status': r[3], 'ic_status': r[4] or 'candidate',
                'horizon': r[6], 'rank_ic': float(r[7]) if r[7] is not None else None,
                'icir': float(r[8]) if r[8] is not None else None,
                'win_rate': float(r[9]) if r[9] is not None else None,
                't_stat': float(r[10]) if r[10] is not None else None,
                'direction': r[11], 'sample_days': r[12],
                'avg_names': float(r[13]) if r[13] is not None else None,
                'traffic': r[14], 'val_start': str(r[15]) if r[15] else None,
                'val_end': str(r[16]) if r[16] else None,
                'computed_at': str(r[17]) if r[17] else None,
            })
        board.sort(key=lambda x: (abs(x['icir'] or 0)), reverse=True)
        return {"horizon": horizon, "board": board}
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e))


@router.get("/ic/correlation")
def ic_correlation(horizon: int = Query(10, ge=1, le=250),
                   val_start: str = Query(''), val_end: str = Query(''),
                   threshold: float = Query(0.7, ge=0.3, le=0.95),
                   max_sections: int = Query(120, ge=20, le=365),
                   user: str = Depends(get_current_user)):
    """因子相关性矩阵 + 贪心去冗推荐。

    相关性口径：各因子逐日截面排名（cs_rank）后对全区间做 pooled Pearson——
    每个截面都是均匀分布 marginal 时，等价于"逐日截面 Spearman 的时间平均"。
    为控制 DB 开销均匀抽样 ≤max_sections 个截面（对 0.7 量级的阈值判断绰绰有余）。
    """
    import re as _re
    import numpy as _np
    import pandas as pd
    from datetime import date as _d, timedelta as _td
    from sqlalchemy import text
    from scripts.pipeline import cs_rank_features
    from scripts.factor_ic import greedy_dedup

    db = get_sync_db()
    try:
        feats = [r[0] for r in db.execute(text(
            "SELECT feature_name FROM features WHERE status='enabled' AND target_entity='stock' "
            "ORDER BY feature_name")).fetchall()]
        if len(feats) < 2:
            raise HTTPException(400, "可用 stock 因子不足 2 个")
        val_end = val_end or str(_d.today())
        if not val_start:
            val_start = (_d.fromisoformat(val_end) - _td(days=3 * 365)).isoformat()

        # 均匀抽样截面日期（以任一因子的日期序列为准，因子间日期对齐）
        all_dates = [str(r[0])[:10] for r in db.execute(text(
            "SELECT DISTINCT trade_date FROM feature_values WHERE feature_name=:f "
            "AND trade_date BETWEEN :sd AND :ed ORDER BY trade_date"),
            {"f": feats[0], "sd": val_start, "ed": val_end}).fetchall()]
        if len(all_dates) < 20:
            raise HTTPException(400, f"区间内截面不足（{len(all_dates)} 天），请扩大区间")
        step = max(1, len(all_dates) // max_sections)
        sample_dates = all_dates[::step]

        # 轻量透视：仅特征值，无行情 JOIN
        safe_names = [_re.sub(r'[^0-9a-zA-Z_]', '', f) for f in feats]
        if safe_names != feats:
            raise HTTPException(400, "特征名含非法字符")
        selects = ",\n               ".join(
            f"MAX(value) FILTER (WHERE feature_name = '{fn}') AS \"{fn}\"" for fn in feats)
        rows = db.execute(text(f"""
            SELECT trade_date, stock_code, {selects}
            FROM feature_values
            WHERE feature_name = ANY(:names) AND trade_date = ANY(CAST(:dates AS date[]))
            GROUP BY stock_code, trade_date
        """), {"names": feats, "dates": sample_dates}).fetchall()
        db.close()
        df = pd.DataFrame(rows, columns=['trade_date', 'stock_code'] + feats)
        for c in feats:
            df[c] = pd.to_numeric(df[c], errors='coerce')

        # 截面排名 → pooled Pearson = 平均逐日 Spearman
        df = cs_rank_features(df, feats)
        corr_df = df[feats].corr()
        corr = {}
        for i, a in enumerate(feats):
            for j, b in enumerate(feats):
                v = corr_df.iloc[i, j]
                corr[(a, b)] = float(v) if pd.notna(v) else 0.0

        # 最新一次 ICIR（指定 horizon）
        ic_rows = db.execute(text("""
            SELECT DISTINCT ON (feature_name) feature_name, rank_ic_ir, traffic
            FROM factor_ic_stats WHERE horizon = :h
            ORDER BY feature_name, created_at DESC
        """), {"h": horizon}).fetchall()
        icir = {r[0]: float(r[1]) if r[1] is not None else None for r in ic_rows}
        traffic = {r[0]: r[2] for r in ic_rows}

        cand_icir = {f: (icir.get(f) or 0) for f in feats}
        selected, skipped = greedy_dedup(corr, cand_icir, threshold=threshold)

        return {
            "val_start": val_start, "val_end": val_end, "horizon": horizon,
            "threshold": threshold, "sections": len(sample_dates),
            "factors": feats,
            "matrix": [[round(corr[(a, b)], 3) for b in feats] for a in feats],
            "icir": {f: (round(cand_icir[f], 3) if icir.get(f) is not None else None) for f in feats},
            "traffic": traffic,
            "recommended": selected,
            "skipped": skipped,
        }
    except HTTPException:
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
                   depends_on, feature_group, tags, status,
                   total_effective_cells, missing_cells_total, abnormal_missing_cells,
                   data_completeness, latest_computed_date, data_anomaly_reason,
                   created_at, updated_at, ic_status, ic_decided_at
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
            "ic_status": r[18] or 'candidate',
            "ic_decided_at": str(r[19])[:19] if r[19] else None,
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
        # 从 features 表读取缓存行数，避免 COUNT(*) 全表扫描
        cached_total = db.execute(text(
            "SELECT COALESCE(actual_row_count, 0) FROM features WHERE id = :id"
        ), {"id": feature_id}).scalar() or 0

        # 按实体过滤时，总数必须按该股票实时统计（actual_row_count 是全市场缓存数，
        # 例如 bias_5 全市场 1739 万行 vs 单只股票仅 6 千行，直接复用会误导分页）
        if code and entity != "global":
            total = db.execute(text(
                "SELECT COUNT(*) FROM feature_values WHERE feature_name = :fn AND stock_code = :code"
            ), {"fn": feature_name, "code": code}).scalar() or 0
        else:
            total = cached_total

        if entity == "global":
            sql = """
                SELECT '' as stock_code, trade_date, value
                FROM feature_values
                WHERE feature_name = :fn
                ORDER BY trade_date DESC
                LIMIT :lim OFFSET :off
            """
        elif code:
            sql = """
                SELECT stock_code, trade_date, value
                FROM feature_values
                WHERE feature_name = :fn AND stock_code = :code
                ORDER BY trade_date DESC
                LIMIT :lim OFFSET :off
            """
            base_params["code"] = code
        else:
            sql = """
                SELECT stock_code, trade_date, value
                FROM feature_values
                WHERE feature_name = :fn
                ORDER BY trade_date DESC, stock_code
                LIMIT :lim OFFSET :off
            """

        try:
            rows = db.execute(text(sql), base_params).fetchall()

            items = []
            # 批量查 stock_name + exchange + close_price（只查本页数据）
            code_set = {r[0] for r in rows if entity != "global" and r[0]}
            name_map = {}
            close_map = {}
            if code_set:
                # 按特征实体类型过滤 stock_type，避免 code 重复(如000025同时是stock和index)
                st_filter = entity if entity in ("stock", "etf") else None
                if st_filter:
                    sm_rows = db.execute(text(
                        "SELECT stock_code, stock_name, exchange FROM stock_master WHERE stock_code = ANY(:codes) AND stock_type = :st"
                    ), {"codes": list(code_set), "st": st_filter}).fetchall()
                else:
                    sm_rows = db.execute(text(
                        "SELECT stock_code, stock_name, exchange FROM stock_master WHERE stock_code = ANY(:codes)"
                    ), {"codes": list(code_set)}).fetchall()
                name_map = {r[0]: (r[1], r[2]) for r in sm_rows}
                # 批量查收盘价
                dq_table = "daily_quote" if entity != "index" else "index_daily_quote"
                code_col = "stock_code" if entity != "index" else "index_code"
                dq_rows = db.execute(text(f"""
                    SELECT {code_col}, trade_date::text, close FROM {dq_table}
                    WHERE {code_col} = ANY(:codes) AND trade_date::text = ANY(:dates)
                """), {"codes": list(code_set), "dates": [str(r[1]) for r in rows if entity != "global"]}).fetchall()
                close_map = {(r[0], r[1]): float(r[2]) if r[2] else None for r in dq_rows}

            for r in rows:
                if entity == "global":
                    items.append({"trade_date": str(r[0]), "value": float(r[1]) if r[1] is not None else None})
                else:
                    sc = r[0]
                    td = str(r[1])
                    nm, ex = name_map.get(sc, ("", ""))
                    items.append({
                        "stock_code": sc, "stock_name": nm, "exchange": ex,
                        "trade_date": td,
                        "value": float(r[2]) if r[2] is not None else None,
                        "close": close_map.get((sc, td)),
                    })

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
            if completeness < 0.6:
                db.execute(text(
                    "UPDATE features SET status='data_anomaly', data_anomaly_reason='数据完整度低于60%，请检查数据源' WHERE id=:id AND status='enabled'"
                ), {"id": feature_id})
            elif completeness >= 0.7:
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


@router.get("/{feature_id}/missing-heatmap")
def missing_heatmap(feature_id: int, days: int = Query(120, ge=30, le=365),
                    top_n: int = Query(50, ge=10, le=100),
                    mode: str = Query("top_missing", pattern="^(top_missing|random)$")):
    """返回缺失热力图数据：最近 N 个交易日 × top_n 只股票。

    mode=top_missing: 选缺失最多的 top_n 只（排查问题）
    mode=random: 随机选 top_n 只（整体覆盖率快照）
    """
    from sqlalchemy import text
    db = get_sync_db()
    try:
        feat = db.execute(text("SELECT feature_name, target_entity FROM features WHERE id=:id"),
                         {"id": feature_id}).fetchone()
        if not feat:
            raise HTTPException(404, "特征不存在")
        fn, entity = feat[0], feat[1]
        if entity == 'global':
            return {"days_labels": [], "stock_labels": [], "matrix": [], "message": "全局特征无热力图"}

        # 最近 N 个交易日（DISTINCT：trade_calendar 每个交易所一行，需去重）
        dates = db.execute(text("""
            SELECT DISTINCT cal_date FROM trade_calendar
            WHERE cal_date <= CURRENT_DATE AND is_trade_day = true
            ORDER BY cal_date DESC LIMIT :n
        """), {"n": days}).fetchall()
        days_labels = [str(r[0]) for r in reversed(dates)]
        if not days_labels:
            return {"days_labels": [], "stock_labels": [], "matrix": []}

        # 缺失率最高的 top_n 只股票（排除完全无数据的退市股）
        ent_filter = "AND sm.stock_type='stock' AND sm.exchange IN ('SSE','SZSE')"
        if entity == 'index':
            ent_filter = "AND sm.stock_type='index'"
        elif entity == 'etf':
            ent_filter = "AND sm.stock_type='etf'"

        total_days = len(days_labels)
        if mode == 'random':
            # 随机抽样：从所有活跃股中随机选 top_n 只
            stocks = db.execute(text(f"""
                SELECT sm.stock_code FROM stock_master sm
                WHERE sm.status = 'N' {ent_filter}
                ORDER BY RANDOM()
                LIMIT :top
            """), {"top": top_n}).fetchall()
            stock_labels = [r[0] for r in stocks]
        else:
            # top_missing：选缺失最多的 top_n 只（需有至少 1 个数据点）
            stocks = db.execute(text(f"""
                SELECT sm.stock_code,
                       :total - COALESCE(fv.cnt, 0) as missing
                FROM stock_master sm
                LEFT JOIN (
                    SELECT stock_code, COUNT(*) as cnt FROM feature_values
                    WHERE feature_name = :fn AND trade_date::text = ANY(:dates)
                    GROUP BY stock_code
                ) fv ON sm.stock_code = fv.stock_code
                WHERE sm.status = 'N' {ent_filter}
                  AND COALESCE(fv.cnt, 0) > 0
                ORDER BY missing DESC
                LIMIT :top
            """), {"fn": fn, "dates": days_labels, "total": total_days, "top": top_n}).fetchall()
            stock_labels = [r[0] for r in stocks]

        # 拉取 top_n 只股的 ipo_date（区分灰格子）
        ipo_map = {}
        if stock_labels:
            ipo_rows = db.execute(text(
                "SELECT stock_code, ipo_date FROM stock_master WHERE stock_code = ANY(:codes)"
            ), {"codes": stock_labels}).fetchall()
            ipo_map = {r[0]: str(r[1]) if r[1] else None for r in ipo_rows}

        # 构建矩阵：2=红 1=灰 0=绿
        # 用 daily_quote 实际交易集判定"该股该日是否交易"（只查 top_n 只 × N 天，走 stock_code 索引）
        matrix = []
        traded = set()
        if stock_labels:
            dq_table = "daily_quote"
            dq_ex_filter = "AND exchange IN ('SSE','SZSE')"
            if entity == 'index':
                dq_table = "index_daily_quote"
                dq_ex_filter = ""
            elif entity == 'etf':
                dq_ex_filter = "AND (LEFT(stock_code,2)='15' OR LEFT(stock_code,1)='5')"

            dq_rows = db.execute(text(f"""
                SELECT stock_code, trade_date::text FROM {dq_table}
                WHERE stock_code = ANY(:codes) AND trade_date::text = ANY(:dates) {dq_ex_filter}
            """), {"codes": stock_labels, "dates": days_labels}).fetchall()
            traded = set((r[0], str(r[1])) for r in dq_rows)

        fv_rows = db.execute(text("""
            SELECT stock_code, trade_date::text FROM feature_values
            WHERE feature_name = :fn AND stock_code = ANY(:codes) AND trade_date::text = ANY(:dates)
        """), {"fn": fn, "codes": stock_labels, "dates": days_labels}).fetchall()
        has_data = set((r[0], str(r[1])) for r in fv_rows)

        for si, stock in enumerate(stock_labels):
            for di, day in enumerate(days_labels):
                key = (stock, day)
                if key in has_data:
                    matrix.append([di, si, 0])  # 绿：有数据
                elif key not in traded:
                    matrix.append([di, si, 1])  # 灰：未交易
                else:
                    matrix.append([di, si, 2])  # 红：交易但缺

        db.close()
        return {"days_labels": days_labels, "stock_labels": stock_labels, "matrix": matrix}
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e))


@router.post("/{feature_id}/recompute-stats")
def recompute_stats(feature_id: int, user: str = Depends(get_current_user)):
    """原子级重算特征统计（不触发计算，仅基于 feature_values 现有数据更新诊断）。"""
    _update_feature_stats_after_compute(feature_id, force_recompute=True)
    return {"ok": True, "message": "诊断已更新"}


@router.get("/{feature_id}/compute-status")
def compute_status(feature_id: int):
    """查询当前特征的补数进度。返回最新的 running 任务（按 started_at 倒序）。"""
    with _compute_lock:
        candidates = []
        for t in _compute_tasks.values():
            if t["feature_id"] == feature_id and t["status"] == "running":
                candidates.append(t)
        if candidates:
            # 按 started_at 倒序取最新的
            latest = max(candidates, key=lambda t: t.get("started_at", 0))
            return {"has_task": True, **{k: v for k, v in latest.items()}}
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

        # 分片进度回调：将分片号映射到 20%~70% 的进度区间
        def _chunk_progress(chunk_num, total_chunks, chunk_start, chunk_end, rows):
            pct = 20 + int((chunk_num / total_chunks) * 50)  # 20% → 70%
            msg = f"计算中 ({chunk_num}/{total_chunks} 片: {chunk_start}~{chunk_end}, {rows}行)"
            _report(min(pct, 70), msg)

        _report(20, "计算特征值...")
        r = compute_feature(db, feature_name, formula, target_entity,
                           start_date=start_date, end_date=end_date,
                           progress_cb=_chunk_progress)
        rows_count = r.get('rows', 0)
        _logger.info(f"[compute] {feature_name} range {start_date}~{end_date}: {rows_count} rows")

        if not r.get('ok'):
            error_msg = f"计算失败: {r.get('error', '未知错误')}"
            _report(0, error_msg)
            with _compute_lock:
                _compute_tasks[task_id]["status"] = "failed"
                _compute_tasks[task_id]["error"] = error_msg
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
            _compute_tasks[task_id]["progress_pct"] = 100
        # 唤醒 WS 广播最后一次（completed 状态也会被推送一次）
        from app.signal import wake_dag_broadcast
        wake_dag_broadcast()
    except Exception as e:
        _logger.error(f"[compute] {feature_name} 失败: {e}")
        with _compute_lock:
            _compute_tasks[task_id]["status"] = "failed"
            _compute_tasks[task_id]["error"] = str(e)
            _compute_tasks[task_id]["progress_pct"] = 0
        from app.signal import wake_dag_broadcast
        wake_dag_broadcast()


def _update_feature_stats_after_compute(feature_id: int, force_recompute: bool = False):
    """计算完成后更新特征的数据统计。

    总格子     = 从 entity_stats 表读取（DAG 每日增量更新，永不 JOIN）
    正常缺失   = 停牌天数 + 活跃股票数 × lookback（天然无解的窗口期）
    异常缺失   = 总格子 - 正常缺失 - 已计算（需要排查的）
    完整度     = 已计算 / 总格子

    Args:
        force_recompute: 保留兼容性，当前不再需要 JOIN 重算（entity_stats 已预计算）
    """
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        # 安全网：任何查询最多等 5s 锁，避免无限阻塞
        db.execute(text("SET LOCAL lock_timeout = '5s'"))

        feat = db.execute(text(
            "SELECT f.feature_name, f.target_entity, f.depends_on "
            "FROM features f WHERE f.id = :id"
        ), {"id": feature_id}).fetchone()
        if not feat:
            return
        fn, entity, depends_on = feat[0], feat[1], feat[2] or []

        # lookback：取依赖函数的最大 lookback
        if isinstance(depends_on, str):
            depends_on = json.loads(depends_on)
        max_lookback = 0
        if depends_on:
            lb_rows = db.execute(text(
                "SELECT COALESCE(MAX(lookback), 0) FROM functions WHERE name = ANY(:names)"
            ), {"names": depends_on}).scalar() or 0
            max_lookback = max(max_lookback, lb_rows)

        # 1. 理论总格子：从 entity_stats 读（DAG 每日增量更新）
        es = db.execute(text(
            "SELECT total_cells, active_count FROM entity_stats WHERE entity_type = :et"
        ), {"et": entity}).fetchone()
        if es:
            total_cells, stock_count = es[0], max(es[1], 1)
        else:
            # entity_stats 未初始化时的回退（不应发生）
            total_cells = 1
            stock_count = 1

        # 2. 实际已计算
        actual = db.execute(text(
            "SELECT COUNT(*) FROM feature_values WHERE feature_name = :fn"
        ), {"fn": fn}).scalar() or 0

        # 3. 窗口期天然缺失 = 每只股票前 lookback 天无法计算
        #    停牌格子无法精确统计（需 per-stock daily_quote），归入"未补"
        lookback_missing = max_lookback * stock_count if entity != 'global' else max_lookback

        # 4. 总缺失（窗口期 + 未补历史数据）
        total_missing = max(0, total_cells - actual)

        completeness = round(actual / total_cells, 4) if total_cells > 0 else 0

        db.execute(text("""
            UPDATE features SET
                total_effective_cells = :tot,
                data_completeness = :comp,
                missing_cells_total = :win,       -- 窗口期天然缺失
                abnormal_missing_cells = :totmiss, -- 总缺失（窗口期 + 未补）
                actual_row_count = :actual,       -- 实际行数缓存，避免 COUNT(*)
                latest_computed_date = CURRENT_DATE,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
        """), {
            "tot": total_cells, "comp": completeness,
            "win": lookback_missing, "totmiss": total_missing,
            "actual": actual,
            "id": feature_id,
        })

        # 自动恢复状态：data_anomaly + 完整度≥60% → enabled（异常已修复）
        db.execute(text("""
            UPDATE features SET status = 'enabled', data_anomaly_reason = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id AND status = 'data_anomaly' AND data_completeness >= 0.6
        """), {"id": feature_id})

        db.commit()
    except Exception as e:
        from loguru import logger as _log
        _log.error(f"[stats] update failed for feature_id={feature_id}: {e}")
    finally:
        db.close()


# 暴露补数任务列表给 WS 广播使用
STALE_RUNNING_TIMEOUT = 300  # 5 分钟无进展视为僵尸任务

def get_active_compute_tasks():
    """返回 running + 刚完成的 completed/failed 任务。

    completed/failed 任务首次广播后标记 _broadcasted，下次不再返回。
    清理僵尸 running 任务（超过 5 分钟且无进展）。
    """
    import time as _time
    now = _time.time()
    with _compute_lock:
        result = []
        stale = []
        for k, v in list(_compute_tasks.items()):
            if v["status"] == "running":
                # 检测僵尸任务：超过 STALE_RUNNING_TIMEOUT 秒且 progress_pct == 0
                started = v.get("started_at", 0)
                if now - started > STALE_RUNNING_TIMEOUT and v.get("progress_pct", 0) == 0:
                    v["status"] = "failed"
                    v["error"] = "任务超时无进展，已自动标记为失败"
                    stale.append(k)
                    continue
                result.append({"task_id": k, **v})
            elif v["status"] in ("completed", "failed") and not v.get("_broadcasted"):
                v["_broadcasted"] = True
                result.append({"task_id": k, **v})
            # 清理 60 秒前完成的旧任务
            if v["status"] in ("completed", "failed") and v.get("_broadcasted"):
                started = v.get("started_at", 0)
                if now - started > 60:
                    stale.append(k)
        for k in stale:
            del _compute_tasks[k]
        return result


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


# ── 因子 IC 检验（任务模式与 compute-range 一致：后台线程 + 轮询）──

from sqlalchemy import text as _ic_text

_ic_tasks: dict = {}
_ic_lock = _threading.Lock()


class IcRequestBody(BaseModel):
    val_start: str = ""          # 空 → 默认近 3 年
    val_end: str = ""            # 空 → 今天
    horizons: List[int] = Field(default=[1, 5, 10, 20])
    layers: int = Field(default=5, ge=2, le=10)


def _run_ic(task_id, feature_id, feature_name, body: dict):
    from scripts.factor_ic import compute_factor_ic
    from loguru import logger as _log
    try:
        db = get_sync_db()
        results = compute_factor_ic(db, feature_name, body['val_start'], body['val_end'],
                                    horizons=body['horizons'], layers=body['layers'])
        db.close()
        errs = [r for r in results if 'error' in r]
        with _ic_lock:
            _ic_tasks[task_id].update({
                'status': 'completed', 'completed_at': _time.time(),
                'results': results, 'failed': errs,
            })
    except Exception as e:
        _log.error(f"[ic] {feature_name} 检验失败: {e}")
        with _ic_lock:
            _ic_tasks[task_id].update({'status': 'failed', 'error': str(e)[:300],
                                       'completed_at': _time.time()})


@router.post("/{feature_id}/ic")
def start_feature_ic(feature_id: int, body: IcRequestBody, user: str = Depends(get_current_user)):
    """启动单因子 IC 检验（默认近 3 年，1/5/10/20 前瞻）。"""
    from datetime import timedelta as _td2
    db = get_sync_db()
    try:
        feat = db.execute(_ic_text(
            "SELECT feature_name, target_entity, status, latest_computed_date FROM features WHERE id=:id"
        ), {"id": feature_id}).fetchone()
        db.close()
        if not feat:
            raise HTTPException(404, "特征不存在")
        if feat[1] != 'stock':
            raise HTTPException(400, f"仅支持 stock 实体因子检验（当前 {feat[1]}）")

        val_end = body.val_end or str(_date.today())[:10]
        if body.val_start:
            val_start = body.val_start
        else:
            # 区间右端不超过特征实际数据日期，避免大量空尾部截面
            data_end = str(feat[3])[:10] if feat[3] else val_end
            eff_end = min(val_end, data_end)
            val_start = (_date.fromisoformat(eff_end) - _td2(days=3 * 365)).isoformat()
            val_end = eff_end

        task_id = str(uuid.uuid4())[:8]
        with _ic_lock:
            _ic_tasks[task_id] = {
                'status': 'running', 'feature_id': feature_id, 'feature_name': feat[0],
                'val_start': val_start, 'val_end': val_end, 'horizons': body.horizons,
                'started_at': _time.time(),
            }
        thread = _threading.Thread(target=_run_ic, args=(
            task_id, feature_id, feat[0],
            {'val_start': val_start, 'val_end': val_end,
             'horizons': body.horizons, 'layers': body.layers},
        ), daemon=True)
        thread.start()
        from app.signal import wake_dag_broadcast
        wake_dag_broadcast()
        return {"ok": True, "task_id": task_id, "feature_name": feat[0],
                "val_start": val_start, "val_end": val_end, "status": "started"}
    except HTTPException:
        raise
    except Exception as e:
        db.close() if not db.closed else None
        raise HTTPException(500, str(e))


@router.get("/{feature_id}/ic/task/{task_id}")
def get_ic_task(feature_id: int, task_id: str, user: str = Depends(get_current_user)):
    """IC 检验任务状态轮询。"""
    with _ic_lock:
        t = _ic_tasks.get(task_id)
        if not t or t.get('feature_id') != feature_id:
            raise HTTPException(404, "任务不存在")
        return dict(t)


@router.get("/{feature_id}/ic")
def get_feature_ic(feature_id: int, horizon: int = Query(None, ge=1, le=250),
                   detail: bool = Query(False), user: str = Depends(get_current_user)):
    """该因子历次 IC 检验记录；detail=true 且指定 horizon 时返回画图数据（ic_series/q_returns）。"""
    db = get_sync_db()
    try:
        name = db.execute(_ic_text("SELECT feature_name FROM features WHERE id=:id"),
                          {"id": feature_id}).fetchone()
        if not name:
            raise HTTPException(404, "特征不存在")
        fname = name[0]
        if detail and horizon:
            row = db.execute(_ic_text("""
                SELECT * FROM factor_ic_stats
                WHERE feature_name=:fn AND horizon=:h
                ORDER BY created_at DESC LIMIT 1
            """), {"fn": fname, "h": horizon}).fetchone()
            db.close()
            if not row:
                raise HTTPException(404, "无检验记录")
            cols = row._mapping.keys()
            d = {k: (str(v) if isinstance(v, _date) else v) for k, v in zip(cols, row)}
            d['id'] = row[0]
            # psycopg2 对 JSONB 自动解码为 dict，兼容 str/dict 两种返回
            d['ic_series'] = json.loads(d['ic_series']) if isinstance(d.get('ic_series'), str) else d.get('ic_series')
            d['q_returns'] = json.loads(d['q_returns']) if isinstance(d.get('q_returns'), str) else d.get('q_returns')
            return d
        rows = db.execute(_ic_text("""
            SELECT id, horizon, val_start, val_end, sample_days, avg_names,
                   ic_mean, rank_ic_mean, ic_ir, rank_ic_ir, ic_win_rate, t_stat,
                   direction, created_at
            FROM factor_ic_stats WHERE feature_name=:fn
            ORDER BY horizon, created_at DESC
        """), {"fn": fname}).fetchall()
        db.close()
        out = []
        for r in rows:
            out.append({
                'id': r[0], 'horizon': r[1], 'val_start': str(r[2]), 'val_end': str(r[3]),
                'sample_days': r[4], 'avg_names': float(r[5]) if r[5] is not None else None,
                'ic_mean': float(r[6]) if r[6] is not None else None,
                'rank_ic': float(r[7]) if r[7] is not None else None,
                'icir': float(r[8]) if r[8] is not None else None,
                'rank_ic_ir': float(r[9]) if r[9] is not None else None,
                'win_rate': float(r[10]) if r[10] is not None else None,
                't_stat': float(r[11]) if r[11] is not None else None,
                'direction': r[12], 'created_at': str(r[13]),
            })
        return {"feature_name": fname, "records": out}
    except HTTPException:
        raise
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e))


class IcStatusBody(BaseModel):
    status: str = Field(..., pattern="^(candidate|included|excluded)$")


@router.put("/{feature_id}/ic-status")
def set_ic_status(feature_id: int, body: IcStatusBody, user: str = Depends(get_current_user)):
    """用户决策：纳入/剔除/恢复候选（重算 IC 不覆盖本字段）。"""
    db = get_sync_db()
    try:
        r = db.execute(_ic_text("""
            UPDATE features SET ic_status=:s, ic_decided_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=:id RETURNING feature_name, ic_status
        """), {"s": body.status, "id": feature_id}).fetchone()
        db.commit()
        db.close()
        if not r:
            raise HTTPException(404, "特征不存在")
        return {"ok": True, "feature_name": r[0], "ic_status": r[1]}
    except HTTPException:
        raise
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e))
