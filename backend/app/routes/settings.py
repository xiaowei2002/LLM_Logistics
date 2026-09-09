"""API Key 保存接口：前端保存时传一次，聊天请求不再携带。"""

from fastapi import APIRouter
from pydantic import BaseModel

from core.llm.api_key_store import save_api_key

router = APIRouter(prefix="/settings", tags=["settings"])


class ApiKeyUpdate(BaseModel):
    api_key: str


@router.put("/api-key")
async def update_api_key(update: ApiKeyUpdate) -> dict:
    save_api_key(update.api_key)
    return {"ok": True}
