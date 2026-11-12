"""Agent ReAct 运行时——多轮「思考 → 工具 → 观察」循环，SSE 事件流输出。

模型与 key 取自状态页「大模型配置」（app.llm_runtime），工具见 app.agent.tools。
每轮把 assistant 的 tool_calls 与 tool 结果都落库，页面刷新可完整回放。

v2（2026-11）：
- 流式输出（assistant_delta / reasoning_delta），停止在流式期间即可生效；
- 工具结果里的图片写入消息 meta（回放不丢图）；
- 用户上传/粘贴的截图以多模态 image_url 直接送模型（大图自动缩放）；
- 记录 usage/耗时/模型，长会话用 memo 压缩早期上下文；
- 同会话串行，跨会话可并行；停止会杀掉沙箱进程组。
"""
import base64
import io
import json
import os
import threading
import time
from datetime import date
from pathlib import Path

from loguru import logger

from app.agent import media, sandbox
from app.agent.tools import TOOL_SPECS, DAG_NODE_HINT, TABLE_DOCS, execute_tool
from app.agent.skills import skill_index_text

MAX_STEPS = 0               # 单轮工具调用步数上限：0 = 不限制（用户可随时停止）；>0 时作为安全阀
SOFT_STEPS = 40             # 无硬上限时的软提醒阈值（只提示，不中断）
HARD_STEPS = 120            # 无硬上限时的兜底保护，防跑飞烧 token
HISTORY_LIMIT = 40          # 兼容旧配置：单轮最多送的通信条数（实际按 turn 裁剪）
HISTORY_TURNS = 10          # 送给模型的最近用户轮数
CONTEXT_CHAR_BUDGET = 60000 # 上下文预算（字符，约 30k token）
TOOL_CONTEXT_CHARS = 6000   # 单条工具结果回灌模型的上限
MAX_ARG_CHARS = TOOL_CONTEXT_CHARS  # 兼容旧名
COMPACT_TRIGGER = 60        # 未压缩消息超过该数量时触发 memo 压缩
COMPACT_KEEP = 12           # 压缩后保留的最近消息条数
TOOL_RESULT_MAX = 20000     # 工具结果落库上限

# 多模态：用户上传的图片直接作为 image_url 送模型（可用 AGENT_VISION=0 关闭）
VISION_ENABLED = os.environ.get("AGENT_VISION", "1") != "0"
VISION_MAX_IMAGES = 4                 # 单条消息最多直传图片数
VISION_MAX_BYTES = 4 * 1024 ** 2      # 单图源文件上限
VISION_TOTAL_BYTES = 8 * 1024 ** 2    # 单轮图片源文件总量上限
VISION_MAX_SIDE = 1600                # 长边超过则等比缩放（图表文字仍可读）
VISION_HISTORY_TURNS = 2              # 历史里重复带图的最近用户轮数
_IMG_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".webp": "image/webp", ".gif": "image/gif"}

_STOP: dict = {}            # session_id -> threading.Event
_LOCKS: dict = {}           # session_id -> threading.Lock（同会话串行）
_LOCK_GUARD = threading.Lock()

IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.svg', '.gif', '.webp')


def _session_lock(sid: int) -> threading.Lock:
    with _LOCK_GUARD:
        return _LOCKS.setdefault(sid, threading.Lock())


def session_running(sid: int) -> bool:
    ev = _STOP.get(sid)
    return bool(ev and not ev.is_set())


def request_stop(session_id: int) -> bool:
    """请求停止：置事件 + 立刻杀沙箱进程组（LLM 流式期间由循环检查事件）。"""
    ev = _STOP.get(session_id)
    sandbox.terminate_session(session_id)
    if ev:
        ev.set()
        return True
    return False


def workspace_images(session_id: int) -> dict:
    """工作区内图片文件 → {相对路径: mtime}（跳过 pylibs/node 依赖目录）。"""
    ws = sandbox.workspace(session_id)
    out = {}
    for ext in IMAGE_EXTS:
        for f in ws.rglob(f'*{ext}'):
            rel = f.relative_to(ws)
            if any(part in ('pylibs', 'node') for part in rel.parts):
                continue
            try:
                out[str(rel)] = f.stat().st_mtime
            except Exception:
                pass
    return out


def _new_or_changed(before: dict, after: dict) -> list:
    return [name for name, mt in after.items() if before.get(name) != mt]


# ── 多模态图片 ────────────────────────────────────────────────

def _attachment_file(sid: int, name: str):
    """附件名 → 工作区 uploads/ 内的真实文件（越界/不存在返回 None）。"""
    try:
        ws = sandbox.workspace(sid).resolve()
        p = (ws / "uploads" / Path(str(name)).name).resolve()
        if ws not in p.parents or not p.is_file():
            return None
        return p
    except Exception:
        return None


def _image_data_url(path: Path) -> str:
    """图片 → data URL。大图等比缩到 1600px；PNG 文字图优先保留 PNG。"""
    try:
        data = path.read_bytes()
        mime = _IMG_MIME.get(path.suffix.lower())
        if not mime:
            return ""
        out, out_mime = data, mime
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(data))
            im.load()
            resized = max(im.size) > VISION_MAX_SIDE
            if resized:
                im.thumbnail((VISION_MAX_SIDE, VISION_MAX_SIDE), Image.LANCZOS)
            if mime == "image/png":
                # 截图/分时图优先保留 PNG，避免 JPEG 把小字和细线糊掉
                if not resized and len(data) <= 2 * 1024 ** 2:
                    out = data
                else:
                    buf = io.BytesIO()
                    im.save(buf, format="PNG", optimize=True)
                    if len(buf.getvalue()) <= VISION_MAX_BYTES:
                        out = buf.getvalue()
                    else:
                        if im.mode not in ("RGB", "L"):
                            im = im.convert("RGB")
                        buf = io.BytesIO()
                        im.save(buf, format="JPEG", quality=86, optimize=True)
                        out, out_mime = buf.getvalue(), "image/jpeg"
            else:
                if im.mode not in ("RGB", "L"):
                    im = im.convert("RGB")
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=86, optimize=True)
                out, out_mime = buf.getvalue(), "image/jpeg"
        except Exception:
            pass
        if len(out) > VISION_MAX_BYTES:
            return ""
        return f"data:{out_mime};base64," + base64.b64encode(out).decode()
    except Exception:
        return ""


def _user_content(sid: int, text: str, attachments, include_images: bool = True):
    """用户消息 → 纯文本或 OpenAI 多模态 content parts。

    include_images=False 时只保留附件文件名说明（历史轮次不重复传图，省 token）。
    """
    atts = [Path(str(a)).name for a in (attachments or []) if a]
    body = text or ""
    if not atts:
        return body
    note = "\n\n（用户上传的附件：" + "、".join(f"uploads/{a}" for a in atts) + "）"
    if not (VISION_ENABLED and include_images):
        return body + note
    parts = [{"type": "text", "text": body + note}]
    used, total, skipped = 0, 0, []
    for a in atts:
        if Path(a).suffix.lower() not in _IMG_MIME:
            continue
        p = _attachment_file(sid, a)
        if not p:
            skipped.append(a)
            continue
        size = p.stat().st_size
        if (used >= VISION_MAX_IMAGES or size > VISION_MAX_BYTES
                or total + size > VISION_TOTAL_BYTES):
            skipped.append(a)
            continue
        url = _image_data_url(p)
        if not url:
            skipped.append(a)
            continue
        total += size
        used += 1
        parts.append({"type": "image_url", "image_url": {"url": url}})
    if not used:
        return body + note
    if skipped:
        parts[0]["text"] += ("\n（另有图片因数量/体积限制未直接传入："
                             + "、".join(f"uploads/{a}" for a in skipped)
                             + "；可用工作区文件分析）")
    return parts


def _has_image_parts(messages: list) -> bool:
    for m in messages:
        c = m.get("content")
        if isinstance(c, list) and any(p.get("type") == "image_url" for p in c):
            return True
    return False


def _text_only(messages: list) -> list:
    """去掉 image_url parts，保留文本（模型不支持视觉时的降级路径）。"""
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            texts = [p.get("text", "") for p in c if p.get("type") == "text"]
            out.append({**m, "content": "\n".join(t for t in texts if t)
                        + "\n（图片未能传给当前模型，附件仍在会话工作区）"})
        else:
            out.append(m)
    return out


def _is_vision_error(e: Exception) -> bool:
    """只对「格式/内容不支持」类错误降级，429/网络错误原样抛出。"""
    code = getattr(e, "status_code", None)
    if code not in (400, 415, 422):
        return False
    msg = str(e).lower()
    return any(k in msg for k in ("image", "vision", "content", "unsupported", "invalid"))


# ── 数据新鲜度（防「今天」幻觉）────────────────────────────────

_FRESH_CACHE = {"at": 0.0, "text": ""}


def _freshness_text() -> str:
    now = time.time()
    if _FRESH_CACHE["text"] and now - _FRESH_CACHE["at"] < 60:
        return _FRESH_CACHE["text"]
    try:
        from sqlalchemy import text
        from app.db.connection import get_sync_db
        db = get_sync_db()
        try:
            d1 = db.execute(text("SELECT MAX(trade_date) FROM daily_quote")).scalar()
            d2 = db.execute(text("SELECT MAX(trade_date) FROM index_daily_quote")).scalar()
            t1 = db.execute(text("SELECT MAX(published_at) FROM news_item")).scalar()
        finally:
            db.close()
        txt = (f"数据新鲜度：今天 {date.today()}；最新个股日线 {d1 or '无'}；"
               f"最新指数日线 {d2 or '无'}；最新资讯 {str(t1)[:19] if t1 else '无'}。"
               "若用户问「今天/最新」而数据日期明显落后，先说明数据尚未更新，不要假设已有当日行情。")
        _FRESH_CACHE.update(at=now, text=txt)
    except Exception:
        _FRESH_CACHE.update(at=now - 30, text="数据新鲜度：当前不可用（未连数据库），涉及时效的问题需说明不确定性。")
    return _FRESH_CACHE["text"]


def _system_prompt() -> str:
    docs = "\n".join(f"- {t}: {d}" for t, d in TABLE_DOCS.items())
    skills = skill_index_text()
    return f"""你是「K道」个股监测平台的内部数据分析助手。今天是 {date.today()}。

## 你的职责
用平台**本地数据**回答选股、行情、特征、信号、资讯、雪球舆情、组合与流程编排类问题；
需要计算或统计时，优先写 SQL 查库，或用沙箱跑 Python（pandas/numpy/matplotlib 可用）。
不要凭记忆编造行情数字；不确定就先查库或问清楚。

## 可用数据表
{docs}

## 数据新鲜度
{_freshness_text()}

## 平台能力
- DAG 流程编排：{DAG_NODE_HINT}
- 特征库：features 注册表（151 个特征）+ feature_values 长表（查询务必带 trade_date 或 feature_name 限定）
- 你可以创建 DAG 流程草稿（create_dag_flow，默认 draft 不发布会），也可触发已发布流程（run_dag_flow）
- 用户上传的附件保存在会话工作区 uploads/ 目录，用 list_workspace 查看，用 pandas 读取分析

## 技能（Skills）
下面是可用技能清单（名称：描述）。接到某类分析任务时，先看清单里有没有匹配的技能；
命中就用 read_skill 读全文，严格按技能里的步骤与 SQL 模板执行——技能是沉淀好的口径，
避免每次自由发挥导致统计口径漂移。用户要求「以后都这么分析 / 把这套流程记下来」时，
用 save_skill 沉淀成新技能。

{skills}

## 图片与附件
- 用户粘贴/上传的截图会以多模态图片直接附在消息里，你**可以直接看图**；不要假设自己看不到图，
  也不要用像素统计去猜图像内容。看图后直接回答图里的股票、时间、价格、成交量形态等问题。
- 较早轮次的图片可能不会重复传入模型；如果当前消息里没有图而任务依赖它，请让用户重新发送。
- CSV/Excel 等文件在会话工作区 uploads/ 目录，用 list_workspace 查看、pandas 读取。

## 沙箱
- 会话有独立工作区：代码、数据、依赖都保存在里面，跨轮次可用
- Python 可直接 import pandas/numpy；matplotlib 等其它包先 pip_install；Node 用 run_node + npm_install
- 沙箱默认无外网、根目录只读，只有工作区可写（需要联网取数时说明限制，改用本地库）
- 图表用 matplotlib 存 PNG 到工作区后会自动显示在对话里（用户可直接看图），优先给图 + 一句结论

## 回答规范
- 中文回答；结论先行，再给依据（数据来源表/时间窗/样本量）
- 涉及涨跌盈亏的颜色语义遵循 A 股习惯（红涨绿跌），需要给建议时说明口径与局限
- 数字要标注口径（后复权 close_hfq 用于收益计算，close 是真实价）
- 工具调用失败先自查参数，不要连续三次重复同样的失败调用
"""


def _client(session_id: str = "agent"):
    from app.llm_runtime import get_llm_settings, build_client
    llm = get_llm_settings()
    if not llm["api_key"]:
        raise RuntimeError("未配置大模型 API Key（到状态页「大模型配置」填写）")
    return build_client(llm["api_key"], llm["base_url"], session_id=session_id, timeout=180), llm


# ══════════════════════════════════════════════
#  消息存取
# ══════════════════════════════════════════════

def ensure_tables():
    """agent 表兜底（init_db 已建；跨进程/旧库升级时保证可用）。"""
    from app.db.connection import get_sync_db
    from app.db.schema import CREATE_AGENT, _split_statements
    from sqlalchemy import text
    db = get_sync_db()
    try:
        for stmt in _split_statements(CREATE_AGENT.strip()):
            s = stmt.strip()
            if s:
                db.execute(text(s))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"[agent] 建表兜底失败: {e}")
    finally:
        db.close()


def get_session(sid: int) -> dict:
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        r = db.execute(text("""
            SELECT id, title, status, model, memo, memo_until, pinned, created_at, updated_at
            FROM agent_sessions WHERE id=:i"""), {"i": sid}).fetchone()
        if not r:
            return {}
        return {"id": r[0], "title": r[1], "status": r[2], "model": r[3], "memo": r[4],
                "memo_until": int(r[5] or 0), "pinned": bool(r[6]),
                "created_at": str(r[7])[:19], "updated_at": str(r[8])[:19]}
    finally:
        db.close()


def list_sessions(q: str = "", limit: int = 100, offset: int = 0,
                  include_archived: bool = False) -> list:
    """会话列表：支持标题/正文搜索、置顶排序、归档过滤。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    lim = max(1, min(int(limit or 100), 200))
    off = max(0, int(offset or 0))
    kw = f"%{(q or '').strip()}%"
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT s.id, s.title, s.updated_at, s.model, s.pinned, s.status,
                   (SELECT COUNT(*) FROM agent_messages m WHERE m.session_id=s.id) AS n,
                   (SELECT LEFT(m.content, 100) FROM agent_messages m
                     WHERE m.session_id=s.id AND m.role='user' ORDER BY m.id DESC LIMIT 1) AS last_q
            FROM agent_sessions s
            WHERE (:all = 1 OR s.status='active')
              AND (:q = '' OR s.title ILIKE :kw OR EXISTS (
                   SELECT 1 FROM agent_messages m WHERE m.session_id=s.id AND m.content ILIKE :kw))
            ORDER BY s.pinned DESC, s.updated_at DESC
            LIMIT :lim OFFSET :off"""), {"all": 1 if include_archived else 0, "q": (q or "").strip(),
                                         "kw": kw, "lim": lim, "off": off}).fetchall()
        return [{"id": r[0], "title": r[1], "updated_at": str(r[2])[:19], "model": r[3],
                 "pinned": bool(r[4]), "status": r[5], "message_count": r[6],
                 "last_question": r[7] or ""}
                for r in rows]
    finally:
        db.close()


def create_session(title: str = "新会话", model: str = "") -> dict:
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        sid = db.execute(text(
            "INSERT INTO agent_sessions (title, model) VALUES (:t, :m) RETURNING id"),
            {"t": (title or "新会话")[:100], "m": (model or None)}).scalar()
        db.commit()
        return {"id": sid, "title": title or "新会话", "model": model or None}
    finally:
        db.close()


def delete_session(sid: int) -> bool:
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    request_stop(sid)
    db = get_sync_db()
    try:
        db.execute(text("DELETE FROM agent_sessions WHERE id=:i"), {"i": sid})
        db.commit()
        sandbox.reset_workspace(sid)
        return True
    finally:
        db.close()


def rename_session(sid: int, title: str) -> bool:
    return update_session(sid, title=title)


def update_session(sid: int, title=None, model=None, status=None,
                   pinned=None, memo=None, memo_until=None) -> bool:
    """局部更新会话（只改传入字段；model 传空串表示恢复全局模型）。"""
    sets, params = [], {"i": sid}
    if title is not None:
        sets.append("title=:t")
        params["t"] = (title or "新会话")[:100]
    if model is not None:
        sets.append("model=:m")
        params["m"] = (model or None)
    if status is not None:
        sets.append("status=:st")
        params["st"] = status
    if pinned is not None:
        sets.append("pinned=:p")
        params["p"] = bool(pinned)
    if memo is not None:
        sets.append("memo=:memo")
        params["memo"] = memo or None
    if memo_until is not None:
        sets.append("memo_until=:mu")
        params["mu"] = int(memo_until or 0)
    if not sets:
        return True
    sets.append("updated_at=CURRENT_TIMESTAMP")
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        db.execute(text(f"UPDATE agent_sessions SET {', '.join(sets)} WHERE id=:i"), params)
        db.commit()
        return True
    finally:
        db.close()


def truncate_from(sid: int, message_id: int) -> int:
    """删除 message_id 及其之后的消息（编辑/重答的前置动作）。返回删除条数。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        n = db.execute(text(
            "DELETE FROM agent_messages WHERE session_id=:s AND id>=:m"),
            {"s": sid, "m": int(message_id)}).rowcount
        # 被删区间可能落在 memo 覆盖范围内，摘要一并失效
        db.execute(text("""UPDATE agent_sessions SET memo=NULL, memo_until=0,
                           updated_at=CURRENT_TIMESTAMP
                           WHERE id=:s AND memo_until>=:m"""), {"s": sid, "m": int(message_id)})
        db.commit()
        return int(n or 0)
    finally:
        db.close()


def branch_session(sid: int, message_id: int, title: str = "") -> dict:
    """把会话截至 message_id 的内容复制成新会话（分支探索）。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        base = db.execute(text("SELECT title, model FROM agent_sessions WHERE id=:s"),
                          {"s": sid}).fetchone()
        if not base:
            return {"error": "会话不存在"}
        name = (title or f"{base[0]}（分支）")[:100]
        new_id = db.execute(text(
            "INSERT INTO agent_sessions (title, model) VALUES (:t, :m) RETURNING id"),
            {"t": name, "m": base[1]}).scalar()
        n = db.execute(text("""
            INSERT INTO agent_messages (session_id, role, content, tool_calls,
                                        tool_call_id, tool_name, meta, created_at)
            SELECT :new, role, content, tool_calls, tool_call_id, tool_name, meta, created_at
            FROM agent_messages WHERE session_id=:s AND id<=:m ORDER BY id"""),
            {"new": new_id, "s": sid, "m": int(message_id)}).rowcount
        db.commit()
        return {"ok": True, "id": new_id, "title": name, "message_count": int(n or 0)}
    except Exception as e:
        db.rollback()
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    finally:
        db.close()


def list_messages(sid: int) -> list:
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT id, role, content, tool_calls, tool_call_id, tool_name, meta, created_at
            FROM agent_messages WHERE session_id=:s ORDER BY id"""), {"s": sid}).fetchall()
        out = []
        for r in rows:
            meta = (json.loads(r[6]) if isinstance(r[6], str) else (r[6] or {}))
            # 回放时重新签发图片 URL（旧签名过期也不影响）
            if isinstance(meta, dict) and meta.get("images"):
                meta["images"] = media.sign_images(sid, meta["images"])
            out.append({
                "id": r[0], "role": r[1], "content": r[2] or "",
                "tool_calls": (json.loads(r[3]) if isinstance(r[3], str) else (r[3] or [])),
                "tool_call_id": r[4], "tool_name": r[5],
                "meta": meta or {},
                "created_at": str(r[7])[:19],
            })
        return out
    finally:
        db.close()


def _save(sid: int, role: str, content: str = "", tool_calls=None,
          tool_call_id=None, tool_name=None, meta=None) -> int:
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        mid = db.execute(text("""
            INSERT INTO agent_messages (session_id, role, content, tool_calls, tool_call_id, tool_name, meta)
            VALUES (:s, :r, :c, :tc, :tcid, :tn, :m) RETURNING id
        """), {"s": sid, "r": role, "c": content,
               "tc": json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
               "tcid": tool_call_id, "tn": tool_name,
               "m": json.dumps(meta, ensure_ascii=False) if meta else None}).scalar()
        db.execute(text("UPDATE agent_sessions SET updated_at=CURRENT_TIMESTAMP WHERE id=:s"), {"s": sid})
        db.commit()
        return mid
    finally:
        db.close()


def _history_for_model(sid: int, memo_until: int = 0) -> list:
    """DB 消息 → OpenAI messages。

    按「用户轮」切分后再裁剪，保证 assistant(tool_calls) 与 tool 结果不被拆散；
    较早轮次的工具结果压缩成一行摘要，控制上下文体积。
    """
    msgs = [m for m in list_messages(sid) if m["id"] > int(memo_until or 0)]
    turns, cur = [], None
    for m in msgs:
        if m["role"] == "user":
            cur = [m]
            turns.append(cur)
        elif cur is not None:
            cur.append(m)
    if not turns:
        turns = [[m] for m in msgs]
    turns = turns[-HISTORY_TURNS:]

    # 只有最近若干轮重复带图（当前轮一定带），避免每轮都重传历史截图烧 token
    user_ids = [t[0]["id"] for t in turns if t and t[0]["role"] == "user"]
    vision_ids = set(user_ids[-VISION_HISTORY_TURNS:])

    def _render(m, compress_tools, include_images):
        if m["role"] == "assistant" and m["tool_calls"]:
            item = {"role": "assistant", "content": m["content"] or None,
                    "tool_calls": m["tool_calls"]}
        elif m["role"] == "tool":
            body = m["content"] or ""
            if compress_tools:
                body = json.dumps({"compressed": True, "summary": body[:300]}, ensure_ascii=False)
            else:
                body = body[:TOOL_CONTEXT_CHARS]
            item = {"role": "tool", "tool_call_id": m["tool_call_id"], "content": body}
        else:
            body = m["content"] or ""
            if m["role"] == "user":
                atts = (m.get("meta") or {}).get("attachments") or []
                content = _user_content(sid, body, atts, include_images=include_images)
                if atts and not include_images:
                    prefix = content if isinstance(content, str) else body
                    content = prefix + "\n（较早轮次的图片未重复传入；如任务依赖该图，请让用户重新发送）"
                item = {"role": "user", "content": content}
            else:
                item = {"role": m["role"], "content": body[:8000]}
        return item

    def _msg_size(item) -> int:
        c = item.get("content")
        if isinstance(c, list):
            n = 0
            for p in c:
                if p.get("type") == "text":
                    n += len(p.get("text") or "")
                elif p.get("type") == "image_url":
                    n += 8000       # 图片按 token 折算成字符预算（约 2~4k token）
            return n
        return len(c or "") + (300 if item.get("tool_calls") else 0)

    # 旧轮次工具结果压缩；按预算从最旧轮次开始丢
    out_turns = []
    for i, t in enumerate(turns):
        old = i < len(turns) - 1
        out_turns.append([_render(m, compress_tools=old,
                                  include_images=(m["id"] in vision_ids)) for m in t])
    total = sum(_msg_size(m) for t in out_turns for m in t)
    while len(out_turns) > 1 and total > CONTEXT_CHAR_BUDGET:
        dropped = out_turns.pop(0)
        total -= sum(_msg_size(m) for m in dropped)
    return [m for t in out_turns for m in t]


def _auto_title(sid: int, first_text: str) -> bool:
    """首轮自动标题（先截断占位，LLM 标题在后台补）。返回是否本次写入。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        t = db.execute(text("SELECT title FROM agent_sessions WHERE id=:s"), {"s": sid}).scalar()
        if t == "新会话":
            db.execute(text("UPDATE agent_sessions SET title=:t WHERE id=:s"),
                       {"t": first_text.strip().replace("\n", " ")[:30] or "新会话", "s": sid})
            db.commit()
            return True
    except Exception:
        pass
    finally:
        db.close()
    return False


def _auto_title_llm(sid: int, first_text: str, client, model: str):
    """后台把截断标题换成更短的中文摘要标题；失败保持原样。"""
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content":
                       "把下面的用户问题压缩成不超过 12 个汉字的中文标题，只输出标题：\n" + first_text[:500]}],
            temperature=0.2, max_tokens=32, timeout=20,
        )
        title = (r.choices[0].message.content or "").strip().strip('"').strip("《》")[:30]
        if not title:
            return
        from sqlalchemy import text
        from app.db.connection import get_sync_db
        db = get_sync_db()
        try:
            cur = db.execute(text("SELECT title FROM agent_sessions WHERE id=:s"), {"s": sid}).scalar()
            placeholder = first_text.strip().replace("\n", " ")[:30] or "新会话"
            if cur == placeholder:
                db.execute(text("UPDATE agent_sessions SET title=:t WHERE id=:s"),
                           {"t": title, "s": sid})
                db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"[agent] LLM 标题失败 sid={sid}: {type(e).__name__}")


def _maybe_compact(sid: int, model: str):
    """消息过多时把较早内容压成 memo，后续轮次只带 memo + 最近若干条。"""
    try:
        sess = get_session(sid)
        if not sess:
            return
        msgs = list_messages(sid)
        memo_until = int(sess.get("memo_until") or 0)
        pending = [m for m in msgs if m["id"] > memo_until]
        if len(pending) <= COMPACT_TRIGGER:
            return
        older = pending[:-COMPACT_KEEP]
        if not older:
            return
        lines = []
        for m in older:
            body = (m["content"] or "").replace("\n", " ")[:600]
            if m["role"] == "tool":
                body = "[工具结果] " + body[:300]
            lines.append(f"{m['role']}: {body}")
        transcript = "\n".join(lines)[:30000]
        client, llm = _client(f"agent-{sid}")
        r = client.chat.completions.create(
            model=model or llm["model"],
            messages=[{"role": "user", "content":
                       "把下面的分析对话压缩成不超过 800 字的中文摘要，保留：用户目标、已确认的口径/参数、"
                       "关键查询结果与结论、未完成的待办。只输出摘要：\n\n" + transcript}],
            temperature=0.2, max_tokens=900, timeout=90,
        )
        memo = (r.choices[0].message.content or "").strip()
        if memo:
            update_session(sid, memo=memo, memo_until=older[-1]["id"])
            logger.info(f"[agent] 会话 {sid} 上下文已压缩至消息 {older[-1]['id']}")
    except Exception as e:
        logger.debug(f"[agent] memo 压缩跳过 sid={sid}: {type(e).__name__}: {e}")


# ══════════════════════════════════════════════
#  反馈
# ══════════════════════════════════════════════

def save_feedback(sid: int, message_id: int, rating: int, comment: str = "") -> dict:
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    rating = 1 if int(rating) > 0 else -1
    db = get_sync_db()
    try:
        q = db.execute(text("""
            SELECT content FROM agent_messages WHERE session_id=:s AND role='user'
              AND (:m = 0 OR id <= :m) ORDER BY id DESC LIMIT 1"""),
            {"s": sid, "m": int(message_id or 0)}).scalar() or ""
        fid = db.execute(text("""
            INSERT INTO agent_feedback (session_id, message_id, rating, comment, question)
            VALUES (:s, :m, :r, :c, :q) RETURNING id"""),
            {"s": sid, "m": int(message_id or 0), "r": rating,
             "c": (comment or "")[:1000], "q": (q or "")[:2000]}).scalar()
        db.commit()
        return {"ok": True, "id": fid, "rating": rating}
    finally:
        db.close()


def list_feedback(limit: int = 100, rating: int = 0) -> list:
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    lim = max(1, min(int(limit or 100), 500))
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT f.id, f.session_id, f.message_id, f.rating, f.comment, f.question, f.created_at,
                   s.title
            FROM agent_feedback f LEFT JOIN agent_sessions s ON s.id=f.session_id
            WHERE (:r = 0 OR f.rating = :r)
            ORDER BY f.created_at DESC LIMIT :lim"""), {"r": int(rating or 0), "lim": lim}).fetchall()
        return [{"id": r[0], "session_id": r[1], "message_id": r[2], "rating": r[3],
                 "comment": r[4], "question": r[5], "created_at": str(r[6])[:19],
                 "session_title": r[7]} for r in rows]
    finally:
        db.close()


# ══════════════════════════════════════════════
#  主循环
# ══════════════════════════════════════════════

def _usage_dict(u) -> dict:
    if not u:
        return {}
    if isinstance(u, dict):
        get = u.get
    else:
        get = lambda k, d=None: getattr(u, k, d)
    out = {}
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        v = get(k)
        if v is not None:
            out[k] = int(v)
    return out


def _add_usage(total: dict, u: dict):
    for k in ("prompt_tokens", "completion_tokens", "total_tokens",
              "estimated_total_tokens"):
        if u and u.get(k) is not None:
            total[k] = int(total.get(k) or 0) + int(u[k])


def _est_tokens(messages: list) -> int:
    """无 usage 时的粗略估算（中文按 2 字符/token，偏保守）。"""
    n = len(json.dumps(messages, ensure_ascii=False, default=str))
    return max(1, n // 2)


def stream_agent(sid: int, user_content: str, attachments=None):
    """生成事件字典（API 层包装为 SSE）。同会话串行，支持中断。"""
    lock = _session_lock(sid)
    if not lock.acquire(blocking=False):
        yield {"type": "error", "error": "该会话正在处理上一条消息，请稍候或先点停止"}
        return
    stop_ev = threading.Event()
    _STOP[sid] = stop_ev
    try:
        sess = get_session(sid) or {}
        user_meta = {"attachments": list(attachments)} if attachments else None
        user_mid = _save(sid, "user", user_content, meta=user_meta)
        title_changed = _auto_title(sid, user_content)
        client, llm = _client(f"agent-{sid}")
        model = (sess.get("model") or llm["model"] or "").strip()
        llm = {**llm, "model": model}
        yield {"type": "start", "session_id": sid, "model": model, "user_message_id": user_mid}

        messages = [{"role": "system", "content": _system_prompt()}]
        if sess.get("memo"):
            messages.append({"role": "system",
                             "content": "本会话较早内容的摘要（延续上下文用）：\n" + sess["memo"]})
        messages += _history_for_model(sid, memo_until=sess.get("memo_until") or 0)

        if title_changed is True:
            threading.Thread(target=_auto_title_llm,
                             args=(sid, user_content, client, model), daemon=True).start()

        usage_total = {}
        t_turn = time.time()
        step = 0
        while True:
            step += 1
            if MAX_STEPS and step > MAX_STEPS:
                yield {"type": "assistant", "content": f"（已达到 {MAX_STEPS} 步工具调用上限，先给出当前结论）"}
                _save(sid, "assistant", f"（已达到 {MAX_STEPS} 步工具调用上限）")
                yield {"type": "done", "steps": step - 1, "usage": usage_total}
                return
            if not MAX_STEPS and step > HARD_STEPS:
                yield {"type": "assistant", "content": f"（已达到 {HARD_STEPS} 步兜底上限，先给出当前结论）"}
                _save(sid, "assistant", f"（已达到 {HARD_STEPS} 步兜底上限）")
                yield {"type": "done", "steps": step - 1, "usage": usage_total}
                return
            if not MAX_STEPS and step == SOFT_STEPS + 1:
                yield {"type": "notice", "text": f"已连续调用 {SOFT_STEPS} 步工具，仍在继续（可随时停止）"}
            if stop_ev.is_set():
                yield {"type": "stopped"}
                return

            t0 = time.time()
            content_parts, reasoning_parts = [], []
            tool_acc = {}
            usage = {}
            try:
                from app.llm_metrics import track

                def _call(msgs):
                    return client.chat.completions.create(
                        model=model, messages=msgs, tools=TOOL_SPECS,
                        tool_choice="auto", temperature=0.3, max_tokens=4096,
                        stream=True,
                    )

                with track(model):
                    try:
                        resp = _call(messages)
                    except Exception as ve:
                        # 纯文本模型/网关不支持 image_url 时降级重试，不打断整轮对话
                        if _has_image_parts(messages) and _is_vision_error(ve):
                            logger.warning(f"[agent] 视觉输入不被支持，降级文本模式: {ve}")
                            messages = _text_only(messages)
                            yield {"type": "notice",
                                   "text": "当前模型/网关不支持直接读图，已退回文本模式；图片仍在会话工作区"}
                            resp = _call(messages)
                        else:
                            raise
                    if hasattr(resp, "choices"):
                        # 非流式客户端（测试替身/老网关）：一次性拿完整消息
                        msg = resp.choices[0].message
                        content_parts.append((getattr(msg, "content", None) or "").strip())
                        reasoning_parts.append((getattr(msg, "reasoning_content", None) or "").strip())
                        usage = _usage_dict(getattr(resp, "usage", None))
                        for tc in (getattr(msg, "tool_calls", None) or []):
                            tool_acc[len(tool_acc)] = {
                                "id": tc.id, "type": "function",
                                "function": {"name": tc.function.name,
                                             "arguments": tc.function.arguments or "{}"}}
                    else:
                        for chunk in resp:
                            if stop_ev.is_set():
                                try:
                                    resp.close()
                                except Exception:
                                    pass
                                yield {"type": "stopped"}
                                return
                            u = _usage_dict(getattr(chunk, "usage", None))
                            if u:
                                usage = u
                            for ch in (getattr(chunk, "choices", None) or []):
                                d = getattr(ch, "delta", None)
                                if d is None:
                                    continue
                                r = (getattr(d, "reasoning_content", None)
                                     or getattr(d, "reasoning", None))
                                if r:
                                    reasoning_parts.append(r)
                                    yield {"type": "reasoning_delta", "text": r, "step": step}
                                c = getattr(d, "content", None)
                                if c:
                                    content_parts.append(c)
                                    yield {"type": "assistant_delta", "text": c, "step": step}
                                for tc in (getattr(d, "tool_calls", None) or []):
                                    idx = getattr(tc, "index", 0) or 0
                                    acc = tool_acc.setdefault(idx, {
                                        "id": "", "type": "function",
                                        "function": {"name": "", "arguments": ""}})
                                    if getattr(tc, "id", None):
                                        acc["id"] = tc.id
                                    fn = getattr(tc, "function", None)
                                    if fn is not None:
                                        if getattr(fn, "name", None):
                                            acc["function"]["name"] = fn.name
                                        if getattr(fn, "arguments", None):
                                            acc["function"]["arguments"] += fn.arguments
            except Exception as e:
                msg = f"{type(e).__name__}: {str(e)[:300]}"
                logger.warning(f"[agent] LLM 调用失败 sid={sid}: {msg}")
                yield {"type": "error", "error": f"模型调用失败：{msg}"}
                return

            content = "".join(content_parts).strip()
            reasoning = "".join(reasoning_parts).strip()
            tool_calls = [tool_acc[i] for i in sorted(tool_acc)
                          if tool_acc[i].get("id") or tool_acc[i]["function"].get("name")]
            _add_usage(usage_total, usage)
            elapsed_ms = int((time.time() - t0) * 1000)
            if not usage:
                usage = {"estimated_total_tokens": _est_tokens(messages) + max(1, len(content) // 2),
                         "estimated": True}
                _add_usage(usage_total, usage)

            meta = {"step": step, "elapsed_ms": elapsed_ms, "model": model,
                    "usage": usage or None, "reasoning": reasoning or None}
            mid = _save(sid, "assistant", content, tool_calls=tool_calls or None, meta=meta)
            messages.append({"role": "assistant", "content": content or None,
                             **({"tool_calls": tool_calls} if tool_calls else {})})
            if content or reasoning:
                yield {"type": "assistant", "content": content, "reasoning": reasoning,
                       "step": step, "message_id": mid, "elapsed_ms": elapsed_ms,
                       "usage": usage, "model": model}

            if not tool_calls:
                yield {"type": "done", "message_id": mid, "steps": step,
                       "usage": usage_total, "elapsed_ms": int((time.time() - t_turn) * 1000)}
                # 压缩判断放在后台（内部会查库并自行判断条数，未超阈值立即返回）
                threading.Thread(target=_maybe_compact, args=(sid, model), daemon=True).start()
                return

            for tc in tool_calls:
                name = tc["function"]["name"]
                raw = tc["function"]["arguments"]
                try:
                    args = json.loads(raw) if raw else {}
                except Exception:
                    args = {}
                yield {"type": "tool_call", "id": tc["id"], "name": name, "args": args, "step": step}
                if stop_ev.is_set():
                    yield {"type": "stopped"}
                    return
                tt = time.time()
                _img_before = workspace_images(sid) if name in ('run_python', 'run_node') else None
                result = execute_tool(sid, name, args)
                elapsed = int((time.time() - tt) * 1000)
                ok = not (isinstance(result, dict) and (result.get("error") or result.get("ok") is False))
                images = []
                if _img_before is not None:
                    for img in _new_or_changed(_img_before, workspace_images(sid))[:6]:
                        images.append({"name": img, "path": img})
                res_str = json.dumps(result, ensure_ascii=False, default=str)
                _save(sid, "tool", res_str[:TOOL_RESULT_MAX], tool_call_id=tc["id"], tool_name=name,
                      meta={"ok": ok, "elapsed_ms": elapsed,
                            "images": images or None,
                            "truncated": bool(isinstance(result, dict) and result.get("truncated"))})
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": res_str[:TOOL_CONTEXT_CHARS]})
                yield {"type": "tool_result", "id": tc["id"], "name": name, "ok": ok,
                       "elapsed_ms": elapsed, "images": media.sign_images(sid, images),
                       "truncated": bool(isinstance(result, dict) and result.get("truncated")),
                       "preview": res_str[:TOOL_RESULT_MAX]}
                if stop_ev.is_set():
                    yield {"type": "stopped"}
                    return

    except Exception as e:
        logger.exception(f"[agent] 运行异常 sid={sid}")
        yield {"type": "error", "error": f"{type(e).__name__}: {str(e)[:300]}"}
    finally:
        _STOP.pop(sid, None)
        lock.release()
