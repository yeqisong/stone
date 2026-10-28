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
