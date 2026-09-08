"""对话流式接口：封装大模型调用，以 SSE 返回正文与推理增量。"""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.llm import get_llm

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str
    # 纯文本，或多模态内容数组（OpenAI 视觉格式：[{type:"text"...},{type:"image_url"...}]）
    content: str | list


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    deepThink: bool = True


def _sse(payload: dict | str) -> str:
    """拼装一条 SSE 事件。"""
    data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return f"data: {data}\n\n"


async def _stream(request: ChatRequest) -> AsyncIterator[str]:
    client = get_llm()
    messages = [{"role": m.role, "content": m.content} for m in request.messages]
    # 深度思考开关经 extra_body 透传给 OpenAI 兼容接口（智谱用 thinking 字段）
    thinking = {"type": "enabled" if request.deepThink else "disabled"}

    try:
        async for chunk in client.stream(messages, extra_body={"thinking": thinking}):
            payload: dict[str, str] = {}
            if chunk.reasoning_content:
                payload["reasoning"] = chunk.reasoning_content
            if chunk.content:
                payload["content"] = chunk.content
            if payload:
                yield _sse(payload)
        yield _sse("[DONE]")
    except Exception as exc:  # noqa: BLE001 - 流已建立，错误只能以事件形式回传
        yield _sse({"error": str(exc)})


@router.post("/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        _stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
