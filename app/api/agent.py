"""AI 问答 Agent API——会话管理 + SSE 流式对话 + 中断 + 反馈。

会话与消息轨迹落 agent_sessions/agent_messages；工具与沙箱见 app.agent。
"""
import asyncio
import json
import queue
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth.auth import optional_auth

router = APIRouter(prefix="/api/agent", tags=["agent"])


class SessionCreate(BaseModel):
    title: str = "新会话"
    model: str = ""


class SessionPatch(BaseModel):
    title: str | None = None
    model: str | None = None
    status: str | None = None
    pinned: bool | None = None


class ChatBody(BaseModel):
    content: str
    attachments: list[str] = []


class RewindBody(BaseModel):
    message_id: int


class BranchBody(BaseModel):
    message_id: int
    title: str = ""


class FeedbackBody(BaseModel):
    message_id: int = 0
    rating: int
    comment: str = ""


@router.get("/sessions")
def sessions(q: str = "", limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
             include_archived: bool = False, user: str = Depends(optional_auth)):
    from app.agent.runtime import list_sessions
    return {"items": list_sessions(q=q, limit=limit, offset=offset,
                                   include_archived=include_archived)}


@router.post("/sessions")
def new_session(body: SessionCreate = SessionCreate(), user: str = Depends(optional_auth)):
    from app.agent.runtime import create_session
    return create_session(body.title, body.model)


@router.patch("/sessions/{sid}")
def patch_session(sid: int, body: SessionPatch, user: str = Depends(optional_auth)):
    from app.agent.runtime import update_session
    update_session(sid, title=body.title, model=body.model,
                   status=body.status, pinned=body.pinned)
    return {"ok": True}


@router.delete("/sessions/{sid}")
def del_session(sid: int, user: str = Depends(optional_auth)):
    from app.agent.runtime import delete_session
    delete_session(sid)
    return {"ok": True}


@router.get("/sessions/{sid}/status")
def session_status(sid: int, user: str = Depends(optional_auth)):
    """运行状态（页面刷新后据此决定是否轮询追平进度）。"""
    from app.agent.runtime import get_session, session_running
    return {"running": session_running(sid), "session": get_session(sid)}


@router.post("/sessions/{sid}/rewind")
def rewind(sid: int, body: RewindBody, user: str = Depends(optional_auth)):
    """删除指定消息及其之后内容（编辑后重发 / 重新生成的前置动作）。"""
    from app.agent.runtime import truncate_from
    return {"ok": True, "deleted": truncate_from(sid, body.message_id)}


@router.post("/sessions/{sid}/branch")
def branch(sid: int, body: BranchBody, user: str = Depends(optional_auth)):
    """把会话截至 message_id 的内容复制成新会话（分支）。"""
    from app.agent.runtime import branch_session
    r = branch_session(sid, body.message_id, body.title)
    if r.get("error"):
        raise HTTPException(400, r["error"])
    return r


@router.post("/sessions/{sid}/feedback")
def feedback(sid: int, body: FeedbackBody, user: str = Depends(optional_auth)):
    from app.agent.runtime import save_feedback
    return save_feedback(sid, body.message_id, body.rating, body.comment)


@router.get("/feedback")
def feedback_list(limit: int = Query(100, ge=1, le=500), rating: int = 0,
                  user: str = Depends(optional_auth)):
    from app.agent.runtime import list_feedback
    return {"items": list_feedback(limit=limit, rating=rating)}


@router.get("/sessions/{sid}/messages")
def messages(sid: int, user: str = Depends(optional_auth)):
    from app.agent.runtime import list_messages
    return {"items": list_messages(sid)}


@router.post("/sessions/{sid}/upload")
async def upload(sid: int, request: Request, name: str = Query("upload.bin"),
                 user: str = Depends(optional_auth)):
    """上传附件到会话工作区 uploads/（原始字节流，不依赖 python-multipart）。"""
    from app.agent.sandbox import save_upload
    data = await request.body()
    r = save_upload(sid, name, data)
    if r.get("error"):
        raise HTTPException(400, r["error"])
    return r


@router.post("/sessions/{sid}/stop")
def stop(sid: int, user: str = Depends(optional_auth)):
    from app.agent.runtime import request_stop
    return {"ok": request_stop(sid)}


_ALLOWED_IMG = ('.png', '.jpg', '.jpeg', '.svg', '.gif', '.webp')


def _safe_workspace_file(sid: int, rel: str):
    """把请求路径解析为会话工作区内文件；越界/非图片返回 None。"""
    import re as _re
    if not _re.match(r"^[A-Za-z0-9._/\-\u4e00-\u9fff]+$", rel or ""):
        return None
    from app.agent.sandbox import workspace
    ws = workspace(sid).resolve()
    try:
        target = (ws / rel).resolve()
    except Exception:
        return None
    if ws not in target.parents or target.suffix.lower() not in _ALLOWED_IMG or not target.is_file():
        return None
    return target


@router.get("/sessions/{sid}/files/{rel:path}")
def session_file(sid: int, rel: str, token: str = "", sig: str = "",
                 authorization: str = ""):
    """会话工作区图片（Agent 生成的图表等）；仅图片、路径限定在工作区内。

    支持两种凭证：短时签名 `sig`（消息里自动签发，避免登录 Token 进 URL）与
    旧的 `token`/Authorization 头（兼容手动打开的链接）。
    """
    from fastapi.responses import FileResponse
    from app.agent import media
    from app.auth.auth import verify_token
    if sig:
        if not media.verify_sig(sid, rel, sig):
            raise HTTPException(401, "签名无效或已过期")
    else:
        bearer = authorization[7:] if authorization.lower().startswith("bearer ") else ""
        t = token or bearer
        if not t:
            raise HTTPException(401, "请提供认证 Token")
        try:
            verify_token(t)
        except Exception:
            raise HTTPException(401, "无效或过期的 Token")
    f = _safe_workspace_file(sid, rel)
    if not f:
        raise HTTPException(404, "文件不存在")
    return FileResponse(str(f))


@router.get("/info")
def info(user: str = Depends(optional_auth)):
    """页面初始化：模型配置状态 + 工具清单（UI 展示用）。"""
    from app.agent.runtime import MAX_STEPS, SOFT_STEPS, HARD_STEPS
    from app.agent.tools import TOOL_SPECS
    from app.llm_runtime import get_llm_settings
    llm = get_llm_settings()
    return {
        "configured": bool(llm["api_key"]),
        "model": llm["model"],
        "models": llm.get("models") or ([llm["model"]] if llm["model"] else []),
        "max_steps": MAX_STEPS,
        "soft_steps": SOFT_STEPS,
        "hard_steps": HARD_STEPS,
        "tools": [{"name": t["function"]["name"], "desc": t["function"]["description"]}
                  for t in TOOL_SPECS],
    }


def _sse_line(ev) -> str:
    return f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"


async def _sse(gen):
    """把同步事件生成器搬到线程里，主协程每 15s 发一次心跳注释。

    这样长时间 LLM/工具调用期间连接不会被代理/浏览器判成空闲断开，也能在
    客户端断线时继续把结果落库（页面重进后轮询追平）。
    """
    q: queue.Queue = queue.Queue()

    def worker():
        try:
            for ev in gen:
                q.put(("ev", ev))
        except Exception as e:  # 生成器自身异常兜底
            q.put(("ev", {"type": "error", "error": f"{type(e).__name__}: {str(e)[:300]}"}))
        finally:
            q.put(("end", None))

    threading.Thread(target=worker, daemon=True, name="agent-sse").start()
    while True:
        try:
            kind, ev = await asyncio.to_thread(q.get, True, 15)
        except queue.Empty:
            yield ": ping\n\n"
            continue
        if kind == "end":
            break
        yield _sse_line(ev)


@router.post("/sessions/{sid}/chat")
def chat(sid: int, body: ChatBody, user: str = Depends(optional_auth)):
    """流式对话。SSE 事件：start/reasoning_delta/assistant_delta/assistant/
    tool_call/tool_result/notice/done/error/stopped。"""
    from app.agent.runtime import stream_agent
    from app.agent.sandbox import workspace
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "消息不能为空")
    # 附件只接受工作区 uploads/ 下真实存在的文件名，避免把任意路径写进上下文
    atts = []
    up = workspace(sid) / "uploads"
    for raw in (body.attachments or [])[:10]:
        name = Path(str(raw)).name
        if name and (up / name).is_file():
            atts.append(name)
    return StreamingResponse(
        _sse(stream_agent(sid, content, attachments=atts)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# ═══════════════════════════════════════════════
#  技能（Skills）管理
# ═══════════════════════════════════════════════

class SkillBody(BaseModel):
    name: str = ""
    title: str = ""
    description: str = ""
    content: str = ""


@router.get("/skills")
def skills_list(user: str = Depends(optional_auth)):
    from app.agent.skills import list_skills
    return {"items": list_skills()}


@router.get("/skills/{name}")
def skill_get(name: str, file: str = "", user: str = Depends(optional_auth)):
    from app.agent.skills import read_skill
    r = read_skill(name, file)
    if r.get("error"):
        raise HTTPException(404, r["error"])
    return r


@router.post("/skills")
def skill_save(body: SkillBody, user: str = Depends(optional_auth)):
    from app.agent.skills import save_skill
    r = save_skill(body.name, body.description, body.content, body.title)
    if r.get("error"):
        raise HTTPException(400, r["error"])
    return r


class SkillImportBody(BaseModel):
    repo_url: str
    slug: str = ""


@router.post("/skills/import/preview")
def skill_import_preview(body: SkillImportBody, user: str = Depends(optional_auth)):
    """拉取 GitHub 技能仓库并返回预览（不落库，人工审核后确认导入）。"""
    from app.agent.skills import import_github_preview
    r = import_github_preview(body.repo_url)
    if r.get("error"):
        raise HTTPException(400, r["error"])
    return r


@router.post("/skills/import/confirm")
def skill_import_confirm(body: SkillImportBody, user: str = Depends(optional_auth)):
    """确认入库到 data/skills/<slug>（覆盖同名用户技能，内置技能拒绝）。"""
    from app.agent.skills import import_github_confirm
    r = import_github_confirm(body.repo_url, body.slug)
    if r.get("error"):
        raise HTTPException(400, r["error"])
    return r


@router.delete("/skills/{name}")
def skill_delete(name: str, user: str = Depends(optional_auth)):
    from app.agent.skills import delete_skill
    r = delete_skill(name)
    if r.get("error"):
        raise HTTPException(400, r["error"])
    return r
