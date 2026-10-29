"""Agent ReAct 运行时——多轮「思考 → 工具 → 观察」循环，SSE 事件流输出。

模型与 key 取自状态页「大模型配置」（app.llm_runtime），工具见 app.agent.tools。
每轮把 assistant 的 tool_calls 与 tool 结果都落库，页面刷新可完整回放。
"""
import json
import threading
import time
from datetime import date

from loguru import logger

from app.agent import sandbox
from app.agent.tools import TOOL_SPECS, DAG_NODE_HINT, TABLE_DOCS, execute_tool
from app.agent.skills import skill_index_text

MAX_STEPS = 12
HISTORY_LIMIT = 40          # 送给模型的最近消息条数（含工具轨迹）
MAX_ARG_CHARS = 6000        # 单条工具结果入模型前的上限

_STOP: dict = {}            # session_id -> threading.Event
_LOCKS: dict = {}           # session_id -> threading.Lock（同会话串行）
_LOCK_GUARD = threading.Lock()


def _session_lock(sid: int) -> threading.Lock:
    with _LOCK_GUARD:
        return _LOCKS.setdefault(sid, threading.Lock())


def request_stop(session_id: int) -> bool:
    ev = _STOP.get(session_id)
    if ev:
        ev.set()
        return True
    return False


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

## 平台能力
- DAG 流程编排：{DAG_NODE_HINT}
- 特征库：features 注册表（151 个特征）+ feature_values 长表（查询务必带 trade_date 或 feature_name 限定）
- 你可以创建 DAG 流程草稿（create_dag_flow，默认 draft 不发布会），也可触发已发布流程（run_dag_flow）

## 技能（Skills）
下面是可用技能清单（名称：描述）。接到某类分析任务时，先看清单里有没有匹配的技能；
命中就用 read_skill 读全文，严格按技能里的步骤与 SQL 模板执行——技能是沉淀好的口径，
避免每次自由发挥导致统计口径漂移。用户要求「以后都这么分析 / 把这套流程记下来」时，
用 save_skill 沉淀成新技能。

{skills}

## 沙箱
- 会话有独立工作区：代码、数据、依赖都保存在里面，跨轮次可用
- Python 可直接 import pandas/numpy；需要其它包用 pip_install；Node 用 run_node + npm_install
- 图表可用 matplotlib 存 PNG 到工作区（无法直接展示，可用文字/数据表呈现结论）

## 回答规范
- 中文回答；结论先行，再给依据（数据来源表/时间窗/样本量）
- 涉及涨跌盈亏的颜色语义遵循 A 股习惯（红涨绿跌），需要给建议时说明口径与局限
- 数字要标注口径（后复权 close_hfq 用于收益计算，close 是真实价）
- 工具调用失败先自查参数，不要连续三次重复同样的失败调用
"""


def _client():
    from openai import OpenAI
    from app.llm_runtime import get_llm_settings
    llm = get_llm_settings()
    if not llm["api_key"]:
        raise RuntimeError("未配置大模型 API Key（到状态页「大模型配置」填写）")
    return OpenAI(api_key=llm["api_key"], base_url=llm["base_url"], timeout=180), llm


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


def list_sessions() -> list:
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT s.id, s.title, s.updated_at,
                   (SELECT COUNT(*) FROM agent_messages m WHERE m.session_id=s.id) AS n
            FROM agent_sessions s WHERE s.status='active'
            ORDER BY s.updated_at DESC LIMIT 100""")).fetchall()
        return [{"id": r[0], "title": r[1], "updated_at": str(r[2])[:19], "message_count": r[3]}
                for r in rows]
    finally:
        db.close()


def create_session(title: str = "新会话") -> dict:
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        sid = db.execute(text(
            "INSERT INTO agent_sessions (title) VALUES (:t) RETURNING id"),
            {"t": (title or "新会话")[:100]}).scalar()
        db.commit()
        return {"id": sid, "title": title or "新会话"}
    finally:
        db.close()


def delete_session(sid: int) -> bool:
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        db.execute(text("DELETE FROM agent_sessions WHERE id=:i"), {"i": sid})
        db.commit()
        sandbox.reset_workspace(sid)
        return True
    finally:
        db.close()


def rename_session(sid: int, title: str) -> bool:
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        db.execute(text("UPDATE agent_sessions SET title=:t, updated_at=CURRENT_TIMESTAMP WHERE id=:i"),
                   {"t": (title or "新会话")[:100], "i": sid})
        db.commit()
        return True
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
            out.append({
                "id": r[0], "role": r[1], "content": r[2] or "",
                "tool_calls": (json.loads(r[3]) if isinstance(r[3], str) else (r[3] or [])),
                "tool_call_id": r[4], "tool_name": r[5],
                "meta": (json.loads(r[6]) if isinstance(r[6], str) else (r[6] or {})),
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


def _history_for_model(sid: int) -> list:
    """DB 消息 → OpenAI messages；裁到最近 N 条且不切断 tool 配对。"""
    msgs = list_messages(sid)
    trimmed = msgs[-HISTORY_LIMIT:] if len(msgs) > HISTORY_LIMIT else msgs
    while trimmed and trimmed[0]["role"] == "tool":
        trimmed.pop(0)
    out = []
    for m in trimmed:
        if m["role"] == "assistant" and m["tool_calls"]:
            out.append({"role": "assistant", "content": m["content"] or None,
                        "tool_calls": m["tool_calls"]})
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"],
                        "content": (m["content"] or "")[:MAX_ARG_CHARS]})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


def _auto_title(sid: int, first_text: str):
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    db = get_sync_db()
    try:
        t = db.execute(text("SELECT title FROM agent_sessions WHERE id=:s"), {"s": sid}).scalar()
        if t == "新会话":
            db.execute(text("UPDATE agent_sessions SET title=:t WHERE id=:s"),
                       {"t": first_text.strip().replace("\n", " ")[:30] or "新会话", "s": sid})
            db.commit()
    except Exception:
        pass
    finally:
        db.close()


# ══════════════════════════════════════════════
#  主循环
# ══════════════════════════════════════════════

def stream_agent(sid: int, user_content: str):
    """生成事件字典（API 层包装为 SSE）。同会话串行，支持中断。"""
    lock = _session_lock(sid)
    if not lock.acquire(blocking=False):
        yield {"type": "error", "error": "该会话正在处理上一条消息，请稍候或先点停止"}
        return
    stop_ev = threading.Event()
    _STOP[sid] = stop_ev
    try:
        _save(sid, "user", user_content)
        _auto_title(sid, user_content)
        client, llm = _client()
        yield {"type": "start", "session_id": sid, "model": llm["model"]}

        messages = [{"role": "system", "content": _system_prompt()}] + _history_for_model(sid)

        for step in range(1, MAX_STEPS + 1):
            if stop_ev.is_set():
                yield {"type": "stopped"}
                return
            t0 = time.time()
            try:
                from app.llm_metrics import track
                with track(llm["model"]):
                    resp = client.chat.completions.create(
                        model=llm["model"], messages=messages, tools=TOOL_SPECS,
                        tool_choice="auto", temperature=0.3, max_tokens=4096,
                    )
            except Exception as e:
                msg = f"{type(e).__name__}: {str(e)[:300]}"
                logger.warning(f"[agent] LLM 调用失败 sid={sid}: {msg}")
                yield {"type": "error", "error": f"模型调用失败：{msg}"}
                return

            msg = resp.choices[0].message
            content = (msg.content or "").strip()
            tool_calls = []
            for tc in (msg.tool_calls or []):
                tool_calls.append({"id": tc.id, "type": "function",
                                   "function": {"name": tc.function.name,
                                                "arguments": tc.function.arguments or "{}"}})

            # 记录 assistant（有工具调用时 content 可能为空）
            mid = _save(sid, "assistant", content, tool_calls=tool_calls or None,
                        meta={"step": step, "elapsed_ms": int((time.time() - t0) * 1000)})
            messages.append({"role": "assistant", "content": content or None,
                             **({"tool_calls": tool_calls} if tool_calls else {})})
            if content:
                yield {"type": "assistant", "content": content, "step": step}

            if not tool_calls:
                yield {"type": "done", "message_id": mid, "steps": step}
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
                result = execute_tool(sid, name, args)
                elapsed = int((time.time() - tt) * 1000)
                ok = not (isinstance(result, dict) and (result.get("error") or result.get("ok") is False))
                # 工具结果落库（给页面回放）+ 回灌模型
                res_str = json.dumps(result, ensure_ascii=False, default=str)
                _save(sid, "tool", res_str[:20000], tool_call_id=tc["id"], tool_name=name,
                      meta={"ok": ok, "elapsed_ms": elapsed})
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": res_str[:MAX_ARG_CHARS]})
                yield {"type": "tool_result", "id": tc["id"], "name": name, "ok": ok,
                       "elapsed_ms": elapsed,
                       "preview": res_str[:1500] + ("…" if len(res_str) > 1500 else "")}

        yield {"type": "assistant", "content": f"（已达到 {MAX_STEPS} 步工具调用上限，先给出当前结论）"}
        _save(sid, "assistant", f"（已达到 {MAX_STEPS} 步工具调用上限）")
        yield {"type": "done", "steps": MAX_STEPS}
    except Exception as e:
        logger.exception(f"[agent] 运行异常 sid={sid}")
        yield {"type": "error", "error": f"{type(e).__name__}: {str(e)[:300]}"}
    finally:
        _STOP.pop(sid, None)
        lock.release()
