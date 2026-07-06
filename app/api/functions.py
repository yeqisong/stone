"""函数管理 API（v2.0 重构 — 迭代 1.1）。"""
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional, List
import json
import re

from app.db.connection import get_sync_db
from fastapi import APIRouter, HTTPException, Query, Body
from pydantic import BaseModel
from typing import Optional, List, Dict
import json
import re

router = APIRouter(prefix="/functions", tags=["functions"])


class CreateFunction(BaseModel):
    name: str
    display_name: str = ""
    description: str = ""
    category: str = "other"
    parameters: list = []
    source_code: str
    lookback: int = 5
    dependencies: list = []


@router.get("")
def list_functions(
    category: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    """函数列表（分页+筛选）。"""
    db = get_sync_db()
    try:
        where = ["1=1"]
        params = {}
        if category and category != "all":
            where.append("category = :cat")
            params["cat"] = category
        if status and status != "all":
            where.append("status = :st")
            params["st"] = status
        if search:
            where.append("(name ILIKE :s OR display_name ILIKE :s)")
            params["s"] = f"%{search}%"

        total = db.execute(text(f"SELECT COUNT(*) FROM functions WHERE {' AND '.join(where)}"), params).scalar() or 0
        rows = db.execute(text(f"""
            SELECT id,name,display_name,description,category,status,version,avg_runtime_ms,is_builtin,created_at,lookback,parameters
            FROM functions WHERE {' AND '.join(where)}
            ORDER BY is_builtin DESC, created_at DESC
            LIMIT :lim OFFSET :off
        """), {**params, "lim": page_size, "off": (page-1)*page_size}).fetchall()

        items = []
        for r in rows:
            items.append({
                "id": r[0], "name": r[1], "display_name": r[2], "description": r[3],
                "category": r[4], "status": r[5], "version": r[6],
                "avg_runtime_ms": round(r[7], 1) if r[7] else None,
                "is_builtin": r[8], "created_at": str(r[9])[:19] if r[9] else None,
                "lookback": r[10],
                "parameters": json.loads(r[11]) if isinstance(r[11], str) else (r[11] or []),
            })
        db.close()
        return {"items": items, "total": total, "page": page, "page_size": page_size}
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e))


@router.post("")
def create_function(body: CreateFunction):
    """新增函数（存为草稿）。"""
    if not re.match(r'^[a-z][a-z0-9_]{1,63}$', body.name):
        raise HTTPException(400, "函数名格式非法，需小写字母开头、仅含字母数字下划线")
    db = get_sync_db()
    try:
        # 检查名称冲突
        exist = db.execute(text("SELECT id FROM functions WHERE name=:n"), {"n": body.name}).fetchone()
        if exist:
            raise HTTPException(409, f"函数名 '{body.name}' 已被占用")

        # 动态计算 lookback：取参数中所有 numeric 类型默认值的最大值
        lookback = body.lookback
        for p in (body.parameters or []):
            if isinstance(p, dict) and p.get('type') == 'numeric':
                try: lookback = max(lookback, int(float(p.get('default', lookback))))
                except: pass

        db.execute(text("""
            INSERT INTO functions (name,display_name,description,category,parameters,source_code,lookback,dependencies,status)
            VALUES (:n,:dn,:desc,:cat,:p,:s,:l,:d,'draft')
        """), {"n": body.name, "dn": body.display_name, "desc": body.description, "cat": body.category,
               "p": json.dumps(list(body.parameters or [])), "s": body.source_code, "l": lookback,
               "d": json.dumps(body.dependencies or [])})
        db.commit()
        db.close()
        return {"ok": True, "name": body.name, "status": "draft"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        db.close()
        raise HTTPException(500, str(e))


@router.get("/{func_id}")
def get_function(func_id: int):
    """函数详情。"""
    db = get_sync_db()
    try:
        r = db.execute(text("""
            SELECT id,name,display_name,description,category,parameters,source_code,lookback,dependencies,is_builtin,status,version,avg_runtime_ms,created_at,updated_at
            FROM functions WHERE id=:id
        """), {"id": func_id}).fetchone()
        if not r:
            raise HTTPException(404, "函数不存在")
        db.close()
        return {
            "id": r[0], "name": r[1], "display_name": r[2], "description": r[3],
            "category": r[4], "parameters": json.loads(r[5]) if isinstance(r[5], str) else r[5],
            "source_code": r[6], "lookback": r[7],
            "dependencies": json.loads(r[8]) if isinstance(r[8], str) else r[8],
            "is_builtin": r[9], "status": r[10], "version": r[11],
            "avg_runtime_ms": round(r[12], 1) if r[12] else None,
            "created_at": str(r[13])[:19] if r[13] else None,
            "updated_at": str(r[14])[:19] if r[14] else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e))


@router.put("/{func_id}")
@router.post("/ai-chat")
def ai_chat(body: Dict = Body(...)):
    """AI 辅助生成：将编码规则 + 已有函数 + 用户需求发给 DeepSeek。"""
    try:
        from app.ai.deepseek_client import chat as ds_chat, is_available
        if not is_available():
            raise HTTPException(503, "DeepSeek API 暂不可用")
        messages = body.get("messages", [])
        if not messages:
            raise HTTPException(400, "缺少 messages")
        reply = ds_chat(messages)
        return {"ok": True, "content": reply}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"AI 调用失败: {str(e)[:200]}")


@router.post("/test-run-temp")
def test_run_temp(body: dict):
    """临时试运行（无需先保存）：直接传 source_code + parameters 即可测试。"""
    return _do_test_run(body.get("source_code",""), body.get("parameters",[]), body.get("category","other"))


@router.post("/{func_id}/test-run")
def _do_test_run(source_code, parameters, category):
    """共享试运行逻辑。"""
    import ast as _ast
    import subprocess as _sp
    import tempfile as _tf
    import os as _os
    import time as _time
    import pandas as _pd
    import numpy as _np
    import re as _re

    code = source_code
    # 从代码中解析实际函数名
    m = _re.search(r'def\s+(\w+)\s*\(', code)
    if not m:
        return {"ok": False, "error": "未找到有效的函数定义", "status": "failed"}
    func_name = m.group(1)
    params = parameters if isinstance(parameters, list) else (json.loads(parameters) if isinstance(parameters, str) else [])

    # AST 安全扫描
    try:
        tree = _ast.parse(code)
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.Import, _ast.ImportFrom)):
                raise HTTPException(400, "函数体禁止使用 import 语句")
            if isinstance(node, _ast.Call):
                if isinstance(node.func, _ast.Name) and node.func.id in ('eval','exec','open','__import__'):
                    raise HTTPException(400, f"检测到危险调用: {node.func.id}")
    except SyntaxError as e:
        return {"ok": False, "error": f"语法错误: {e.msg}", "status": "failed"}

    # 解析参数（省略，和原 test_run_function 一致）...
    OLD_TO_NEW = {'field':'series','price':'series','volume':'series','return':'series',
                  'ratio':'series','numeric':'scalar','industry':'series','stock_pool':'matrix'}
    SCALAR_DEFAULTS = {'int': '5', 'float': '0.05', 'percent': '0.08'}
    numeric_args = {}
    series_args = []
    matrix_args = []
    for p in params:
        raw_type = p.get("type", "scalar")
        ptype = OLD_TO_NEW.get(raw_type, raw_type)
        if ptype in ('series', 'vector'):
            col = f"col_{len(series_args)}"
            series_args.append((p["name"], col))
        elif ptype == 'matrix':
            col = f"mat_{len(matrix_args)}"
            matrix_args.append((p["name"], col))
        else:
            val = p.get("default")
            if val is not None and val != '':
                try:
                    fv = float(val)
                    numeric_args[p["name"]] = int(fv) if fv == int(fv) else fv
                except: pass

    n_rows = 200
    col_defs = []
    all_args = ""
    for i, (name, col) in enumerate(series_args):
        col_defs.append(f'    "{col}": np.random.randn(N).cumsum() + 100,')
        if i == 0: all_args += (", " if all_args else "") + f"df['{col}']"
        else: all_args += (", " if all_args else "") + f"{name}=df['{col}']"
    mat_blocks = []
    for name, col in matrix_args:
        mat_blocks.append(f'{name} = pd.DataFrame({{"x": np.random.randn({n_rows}).cumsum() + 100, "y": np.random.choice(["a","b","c"], {n_rows})}})')
        all_args += (", " if all_args else "") + f"{name}={name}"
    for k, v in numeric_args.items():
        all_args += (", " if all_args else "") + f"{k}={json.dumps(v)}"

    test_script = f"""
import pandas as pd
import numpy as np
import json, time
np.random.seed(42)

{code}

N = {n_rows}
df = pd.DataFrame({{
{chr(10).join(col_defs)}
}})
{chr(10).join(mat_blocks)}
t0 = time.perf_counter()
result = {func_name}({all_args})
elapsed = (time.perf_counter() - t0) * 1000

if hasattr(result, 'dropna'):
    preview = result.dropna().head(10).tolist()
elif hasattr(result, '__len__'):
    preview = list(result[:10])
else:
    preview = [float(result)]
preview = [round(float(x), 4) if x is not None else None for x in preview[:10]]
print(json.dumps({{"elapsed_ms": round(elapsed, 1), "preview": preview, "rows": len(result) if hasattr(result, '__len__') else 1}}))
"""
    with _tf.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(test_script)
        tmp_path = f.name
    try:
        proc = _sp.run(["python3", tmp_path], capture_output=True, text=True, timeout=5)
        if proc.returncode != 0:
            return {"ok": False, "error": proc.stderr[:500] or proc.stdout[:500], "status": "failed"}
        result = json.loads(proc.stdout.strip())
        elapsed = result["elapsed_ms"]
        status = "passed" if elapsed < 500 else ("warning" if elapsed < 2000 else "failed")
        return {"ok": True, "status": status, "elapsed_ms": elapsed, "preview": result["preview"], "rows": result["rows"]}
    except _sp.TimeoutExpired:
        return {"ok": False, "error": "执行超时（>5s）", "status": "failed"}
    finally:
        try: _os.unlink(tmp_path)
        except: pass


def test_run_function(func_id: int, body: dict):
    """沙箱试运行（需先保存）。"""
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT source_code,parameters FROM functions WHERE id=:id"), {"id": func_id}).fetchone()
        if not r:
            raise HTTPException(404, "函数不存在")
        code, params_raw = r[0], r[1]
        params = json.loads(params_raw) if isinstance(params_raw, str) else (params_raw or [])
        db.close()
        return _do_test_run(code, params, body.get("category","other"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"试运行失败: {str(e)[:200]}")

@router.delete("/{func_id}")
@router.get("/{func_id}/versions")
def list_versions(func_id: int):
    """版本历史列表。"""
    db = get_sync_db()
    try:
        rows = db.execute(text(
            "SELECT id,version,change_log,created_at FROM function_versions WHERE function_id=:id ORDER BY version DESC"
        ), {"id": func_id}).fetchall()
        db.close()
        return {"versions": [{"id": r[0], "version": r[1], "change_log": r[2] or '', "created_at": str(r[3])[:19] if r[3] else None} for r in rows]}
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e))


@router.post("/{func_id}/rollback/{version}")
def rollback_function(func_id: int, version: int):
    """回滚到指定版本。"""
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT is_builtin FROM functions WHERE id=:id"), {"id": func_id}).fetchone()
        if not r:
            raise HTTPException(404, "函数不存在")
        if r[0]:
            raise HTTPException(400, "系统内置函数不可回滚")
        snap = db.execute(text(
            "SELECT source_code,parameters FROM function_versions WHERE function_id=:id AND version=:v"
        ), {"id": func_id, "v": version}).fetchone()
        if not snap:
            raise HTTPException(404, "版本不存在")
        db.execute(text(
            "UPDATE functions SET source_code=:s, parameters=:p, updated_at=CURRENT_TIMESTAMP WHERE id=:id"
        ), {"s": snap[0], "p": snap[1], "id": func_id})
        db.commit()
        db.close()
        return {"ok": True, "version": version}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        db.close()
        raise HTTPException(500, str(e))


@router.get("/{func_id}/diff/{v1}/{v2}")
def diff_versions(func_id: int, v1: int, v2: int):
    """版本 diff 视图（1.3）。返回两个版本源码的逐行对比。"""
    db = get_sync_db()
    try:
        s1 = db.execute(text("SELECT source_code FROM function_versions WHERE function_id=:id AND version=:v"),
                        {"id": func_id, "v": v1}).fetchone()
        s2 = db.execute(text("SELECT source_code FROM function_versions WHERE function_id=:id AND version=:v"),
                        {"id": func_id, "v": v2}).fetchone()
        db.close()
        if not s1 or not s2:
            raise HTTPException(404, "版本不存在")
        import difflib
        diff = list(difflib.unified_diff(
            (s1[0] or "").splitlines(), (s2[0] or "").splitlines(),
            fromfile=f"v{v1}", tofile=f"v{v2}", lineterm=""
        ))
        return {"ok": True, "v1": v1, "v2": v2, "diff": diff}
    except HTTPException:
        db.close(); raise
    except Exception as e:
        db.close(); raise HTTPException(500, str(e))


def delete_function(func_id: int):
    """删除自定义函数（仅草稿状态可删）。"""
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT is_builtin,status FROM functions WHERE id=:id"), {"id": func_id}).fetchone()
        if not r:
            raise HTTPException(404, "函数不存在")
        if r[0]:
            raise HTTPException(400, "系统内置函数不可删除")
        db.execute(text("DELETE FROM functions WHERE id=:id"), {"id": func_id})
        db.commit()
        db.close()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        db.close()
        raise HTTPException(500, str(e))


def update_function(func_id: int, body: dict):
    """编辑函数。"""
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT is_builtin,status FROM functions WHERE id=:id"), {"id": func_id}).fetchone()
        if not r:
            raise HTTPException(404, "函数不存在")
        if r[0]:
            raise HTTPException(400, "系统内置函数不可编辑")

        sets = []
        params = {"id": func_id}
        for f in ['display_name','description','category','parameters','source_code','lookback','dependencies','status']:
            if f in body:
                sets.append(f"{f}=:{f}")
                params[f] = json.dumps(body[f]) if f in ('parameters','dependencies') else body[f]

        if not sets:
            raise HTTPException(400, "无修改内容")

        # 发布时保存快照
        is_publishing = body.get("status") == "published"
        if is_publishing:
            # 获取当前版本号 +1
            cur = db.execute(text("SELECT version,source_code,parameters FROM functions WHERE id=:id"), {"id": func_id}).fetchone()
            new_ver = (cur[0] or 0) + 1
            sets.append(f"version={new_ver}")
            params["ver"] = new_ver
            # 插入版本快照
            db.execute(text(
                "INSERT INTO function_versions (function_id, version, source_code, parameters) VALUES (:id, :ver, :code, :prm)"
            ), {"id": func_id, "ver": new_ver, "code": cur[1], "prm": cur[2]})

        sets.append("updated_at=CURRENT_TIMESTAMP")
        db.execute(text(f"UPDATE functions SET {','.join(sets)} WHERE id=:id"), params)
        db.commit()
        db.close()
        return {"ok": True, "id": func_id, "version": new_ver if is_publishing else None}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        db.close()
        raise HTTPException(500, str(e))
