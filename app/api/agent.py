"""AI 问答 Agent API——会话管理 + SSE 流式对话 + 中断。

会话与消息轨迹落 agent_sessions/agent_messages；工具与沙箱见 app.agent。
"""
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth.auth import optional_auth

router = APIRouter(prefix="/api/agent", tags=["agent"])


class SessionBody(BaseModel):
    title: str = "新会话"


class ChatBody(BaseModel):
    content: str


@router.get("/sessions")
def sessions(user: str = Depends(optional_auth)):
    from app.agent.runtime import list_sessions
    return {"items": list_sessions()}


@router.post("/sessions")
def new_session(body: SessionBody = SessionBody(), user: str = Depends(optional_auth)):
    from app.agent.runtime import create_session
    return create_session(body.title)


@router.patch("/sessions/{sid}")
def patch_session(sid: int, body: SessionBody, user: str = Depends(optional_auth)):
    from app.agent.runtime import rename_session
    rename_session(sid, body.title)
    return {"ok": True}


@router.delete("/sessions/{sid}")
def del_session(sid: int, user: str = Depends(optional_auth)):
    from app.agent.runtime import delete_session
    delete_session(sid)
    return {"ok": True}


@router.get("/sessions/{sid}/messages")
def messages(sid: int, user: str = Depends(optional_auth)):
    from app.agent.runtime import list_messages
    return {"items": list_messages(sid)}


@router.post("/sessions/{sid}/stop")
def stop(sid: int, user: str = Depends(optional_auth)):
    from app.agent.runtime import request_stop
    return {"ok": request_stop(sid)}


_ALLOWED_IMG = ('.png', '.jpg', '.jpeg', '.svg', '.gif', '.webp')


def _safe_workspace_file(sid: int, rel: str):
    """把请求路径解析为会话工作区内文件；越界/非图片返回 None。"""
    import re as _re
    from pathlib import Path as _P
    if not _re.match(r"^[A-Za-z0-9._/-]+$", rel or ""):
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
def session_file(sid: int, rel: str, user: str = Depends(optional_auth)):
    """会话工作区图片（Agent 生成的图表等）；仅图片、路径限定在工作区内。"""
    from fastapi.responses import FileResponse
    f = _safe_workspace_file(sid, rel)
    if not f:
        raise HTTPException(404, "文件不存在")
    return FileResponse(str(f))


@router.get("/info")
def info(user: str = Depends(optional_auth)):
    """页面初始化：模型配置状态 + 工具清单（UI 展示用）。"""
    from app.agent.runtime import MAX_STEPS
    from app.agent.tools import TOOL_SPECS
    from app.llm_runtime import get_llm_settings
    llm = get_llm_settings()
    return {
        "configured": bool(llm["api_key"]),
        "model": llm["model"],
        "max_steps": MAX_STEPS,
        "tools": [{"name": t["function"]["name"], "desc": t["function"]["description"]}
                  for t in TOOL_SPECS],
    }


def _sse(gen):
    for ev in gen:
        yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"


@router.post("/sessions/{sid}/chat")
def chat(sid: int, body: ChatBody, user: str = Depends(optional_auth)):
    """流式对话。SSE 事件：start/assistant/tool_call/tool_result/done/error/stopped。"""
    from app.agent.runtime import stream_agent
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "消息不能为空")
    return StreamingResponse(
        _sse(stream_agent(sid, content)),
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
