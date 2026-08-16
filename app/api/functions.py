"""函数管理 API（v2.0 重构 — 迭代 1.1）。"""
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional, List, Dict
import json
import re
import ast

from app.db.connection import get_sync_db
from app.auth.auth import get_current_user

router = APIRouter(prefix="/functions", tags=["functions"])


# ── 安全校验：自定义函数必须是纯向量化代码 ──

_FORBIDDEN_NODES = {ast.For, ast.While, ast.AsyncFor, ast.AsyncWith}
_FORBIDDEN_IMPORTS = {'os', 'subprocess', 'sys', 'shutil', 'socket', 'requests', 'http'}

# 危险调用/属性（含 getattr 字符串绕过路径）—— 禁止直接调用，也禁止作为属性链成分
_DANGEROUS_CALLS = {'eval', 'exec', 'open', '__import__', 'compile', 'globals', 'locals',
                    'vars', 'getattr', 'setattr', 'hasattr', 'memoryview', 'type',
                    '__builtins__', '__class__', '__bases__', '__subclasses__',
                    '__globals__', '__code__', '__getattribute__', 'super', 'dir'}


def _ast_security_scan(tree) -> Optional[str]:
    """AST 安全扫描：递归检查所有 Call/Attribute/Name 节点。

    返回错误信息，None 表示通过。
    注意：字符串参数（如 getattr(__builtins__, 'eval')）无法被 AST 检查，
    因此 getattr/setattr/__import__ 等必须从函数名层直接封禁。
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return "函数体禁止使用 import 语句"
        if isinstance(node, ast.Call):
            # 直接调用名（Name 或属性链末端）命中危险名单 → 拦截
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in _DANGEROUS_CALLS:
                return f"检测到危险调用: {fn.id}"
        # 递归检查属性链（任意深度），拦截含危险成分的链
        if isinstance(node, ast.Attribute):
            parts = []
            cur = node
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
            elif isinstance(cur, ast.Call):
                parts.append('<call>')
            full = '.'.join(reversed(parts))
            if any(d in parts for d in _DANGEROUS_CALLS):
                return f"检测到危险属性链: {full}"
    return None


def _validate_function_safety(source_code: str) -> Optional[str]:
    """校验自定义函数安全性。返回错误信息字符串，合法返回 None。

    规则：
    1. 禁止 for / while 循环（必须用 pandas/numpy 向量化操作）
    2. 禁止危险模块导入（os/subprocess/sys/socket 等）
    3. 必须定义至少一个函数
    """
    if not source_code or not source_code.strip():
        return "函数源码不能为空"

    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        return f"Python 语法错误: {e.msg} (行 {e.lineno})"

    has_function = False

    for node in ast.walk(tree):
        # 检查 for / while
        if type(node) in _FORBIDDEN_NODES:
            return f"禁止使用 {type(node).__name__} 循环。请使用 pandas/numpy 向量化操作（如 .rolling(), np.where() 等）。"

        # 检查函数定义
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            has_function = True

        # 检查危险 import
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split('.')[0] in _FORBIDDEN_IMPORTS:
                    return f"禁止导入模块 '{alias.name}'。自定义函数仅允许使用 pandas/numpy 及安全内置模块。"

        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.split('.')[0] in _FORBIDDEN_IMPORTS:
                return f"禁止导入模块 '{node.module}'。"

    # 通用 AST 安全扫描（危险调用/属性链，含 getattr 字符串绕过）
    scan_err = _ast_security_scan(tree)
    if scan_err:
        return scan_err

    if not has_function:
        return "源码中未检测到函数定义。请至少定义一个函数（函数名需与注册名一致）。"

    return None


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
def create_function(body: CreateFunction, user: str = Depends(get_current_user)):
    """新增函数（存为草稿）。"""
    if not re.match(r'^[a-z][a-z0-9_]{1,63}$', body.name):
        raise HTTPException(400, "函数名格式非法，需小写字母开头、仅含字母数字下划线")

    # 安全校验：禁止 for/while 循环 + 危险 import
    err = _validate_function_safety(body.source_code)
    if err:
        raise HTTPException(400, err)

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
def update_function(func_id: int, body: dict, user: str = Depends(get_current_user)):
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

        # 安全校验：如果修改了源码或正在发布，重新校验
        src = body.get("source_code")
        is_publishing = body.get("status") == "published"
        if src is not None or is_publishing:
            current_src = src
            if current_src is None:
                r2 = db.execute(text("SELECT source_code FROM functions WHERE id=:id"), {"id": func_id}).fetchone()
                current_src = r2[0] if r2 else ""
            err = _validate_function_safety(current_src)
            if err:
                db.close()
                raise HTTPException(400, err)

        if is_publishing:
            cur = db.execute(text("SELECT version,source_code,parameters FROM functions WHERE id=:id"), {"id": func_id}).fetchone()
            new_ver = (cur[0] or 0) + 1
            sets.append(f"version={new_ver}")
            params["ver"] = new_ver
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
def test_run_temp(body: dict, user: str = Depends(get_current_user)):
    """临时试运行（无需先保存）：直接传 source_code + parameters 即可测试。"""
    return _do_test_run(body.get("source_code",""), body.get("parameters",[]), body.get("category","other"))


@router.post("/{func_id}/test-run")
def test_run_function(func_id: int, body: dict, user: str = Depends(get_current_user)):
    """沙箱试运行（需先保存）。"""
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT source_code,parameters,category FROM functions WHERE id=:id"), {"id": func_id}).fetchone()
        if not r:
            raise HTTPException(404, "函数不存在")
        code, params_raw, cat = r[0], r[1], r[2]
        params = json.loads(params_raw) if isinstance(params_raw, str) else (params_raw or [])
        db.close()
        return _do_test_run(code, params, cat or "other")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"试运行失败: {str(e)[:200]}")


def _do_test_run(source_code, parameters, category):
    """共享试运行逻辑。"""
    import sys as _sys
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

    # AST 安全扫描（共享扫描器：封禁 import/危险调用/属性链/getattr 绕过）
    try:
        tree = _ast.parse(code)
    except SyntaxError as e:
        return {"ok": False, "error": f"语法错误: {e.msg}", "status": "failed"}
    scan_err = _ast_security_scan(tree)
    if scan_err:
        return {"ok": False, "error": scan_err, "status": "failed"}

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

    # 如果参数列表为空，从函数签名自动解析：第一个参数非 df → 标量，df → 序列
    if not params and not series_args and not numeric_args and not matrix_args:
        sig_match = _re.search(r'def\s+\w+\s*\((.*?)\)', code)
        if sig_match:
            sig_args = [a.strip() for a in sig_match.group(1).split(',') if a.strip()]
            for i, arg in enumerate(sig_args):
                name = arg.split('=')[0].strip()
                if i == 0 or name == 'df':
                    col = f"col_{len(series_args)}"
                    series_args.append((name, col))
                else:
                    default_val = arg.split('=')[1].strip() if '=' in arg else None
                    if default_val:
                        try:
                            fv = float(default_val)
                            numeric_args[name] = int(fv) if fv == int(fv) else fv
                        except:
                            pass

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
        # 用当前解释器（venv，含 pandas/numpy）+ -I 隔离模式（跳过用户 site/PYTHONPATH）
        # 配合 AST 扫描双保险；cwd 固定在 /tmp 空目录，避免读写项目文件
        proc = _sp.run([_sys.executable, "-I", tmp_path], capture_output=True, text=True, timeout=15,
                       cwd="/tmp", env={"PATH": "/usr/bin:/bin"})
        if proc.returncode != 0:
            return {"ok": False, "error": proc.stderr[:500] or proc.stdout[:500], "status": "failed"}
        result = json.loads(proc.stdout.strip())
        elapsed = result["elapsed_ms"]
        status = "passed" if elapsed < 500 else ("warning" if elapsed < 2000 else "failed")
        return {"ok": True, "status": status, "elapsed_ms": elapsed, "preview": result["preview"], "rows": result["rows"]}
    except _sp.TimeoutExpired:
        return {"ok": False, "error": "执行超时（>15s）", "status": "failed"}
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
def rollback_function(func_id: int, version: int, user: str = Depends(get_current_user)):
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


@router.delete("/{func_id}")
def delete_function(func_id: int, user: str = Depends(get_current_user)):
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



