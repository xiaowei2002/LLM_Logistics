"""
多模态 RAG 服务（基于 joyagent-jdgenie 的 mrag）

运行（在 backend 目录下）：
    python -m uvicorn core.tools.mrag.api.app:app --port 8000
接口文档： http://127.0.0.1:8000/docs

路由与 joyagent 保持一致：
    /v1/documents/*        知识库、文件的增删查（见 routes/document.py）
    /v1/tool/mragQuery     Agentic RAG 问答（SSE 流式）
另外：
    /v1/documents/list_file_chunks   查看单个文件的文本分块与图片
    /storage/files/*                 本地存储的文件下载
    /health                          健康检查
"""
from pathlib import Path

from dotenv import load_dotenv

# mrag 各模块在导入时就读取环境变量，必须先加载 backend/.env
load_dotenv(Path(__file__).resolve().parents[4] / ".env")

import json  # noqa: E402
import os  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from datetime import datetime  # noqa: E402
from typing import List, Optional  # noqa: E402

from fastapi import APIRouter, FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from sse_starlette import EventSourceResponse  # noqa: E402

from core.tools.mrag.api.routes.document import router as document_router  # noqa: E402
from core.tools.mrag.query import AgenticRAG  # noqa: E402
from core.tools.mrag.storage import VectorStore  # noqa: E402
from core.tools.mrag.storage.models.kb_model import KBModel  # noqa: E402
from core.tools.mrag.storage.store_factory import get_kb_store  # noqa: E402
from core.tools.mrag.utils.logger_utils import logger  # noqa: E402
from core.tools.mrag.utils.oss_utils import router as storage_router  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    # joyagent 由 init_db 脚本建集合；这里启动时自动建（已存在则跳过），并保证默认知识库存在
    VectorStore().create_all_collections()
    kb_id = os.getenv("DEFAULT_KB_ID", "1")
    now = datetime.now()
    if get_kb_store().create_kb(KBModel(kb_id=kb_id, kb_name="默认知识库", kb_desc="汽车物流多模态知识库",
                                        chunk_type="fixed_size", create_time=now, modify_time=now)):
        logger.info(f"已创建默认知识库 {kb_id}")
    yield


app = FastAPI(title="多模态 RAG 服务", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

api_router = APIRouter(prefix="/v1")
api_router.include_router(document_router, tags=["mrag documents"])


class MultimodalRAGRequest(BaseModel):
    question: str = ""
    image_urls: List[str] = Field(default_factory=list)
    kb_id: Optional[str] = ""


@api_router.post("/tool/mragQuery", tags=["mrag query"])
async def mrag_query(request: MultimodalRAGRequest):
    """Agentic RAG 问答：多轮检索 + 重排 + LLM/VLM 生成，SSE 逐块返回 OpenAI 格式的 chunk"""
    kb_id = request.kb_id or os.getenv("DEFAULT_KB_ID")
    logger.info(f"kb_id:{kb_id}")
    agent = AgenticRAG(kb_id=kb_id, n_round=3)

    def generator():
        try:
            for chunk in agent.run(request.question, request.image_urls):
                yield chunk.model_dump_json()
        except Exception as e:
            # 中途出错时以同样的 chunk 格式把原因发给前端，避免流直接断开、页面无提示
            logger.exception(f"mragQuery 失败: {e}")
            yield json.dumps({"choices": [{"delta": {"content": f"\n\n[检索问答出错：{e}]"}}]}, ensure_ascii=False)

        yield "[DONE]"

    return EventSourceResponse(generator())


class ListFileChunksRequest(BaseModel):
    kb_id: str
    file_id: str


@api_router.post("/documents/list_file_chunks", tags=["mrag documents"])
async def list_file_chunks(request: ListFileChunksRequest):
    """按文件列出文本分块（去掉子分块增强带来的重复）与解析出的图片"""
    store = VectorStore()
    conditions = {"kb_id": request.kb_id, "file_id": request.file_id}

    def scroll_all(collection):
        points, offset = [], None
        while True:
            batch, offset = store.base_store.scroll_vectors(collection, limit=256, offset=offset,
                                                            filter_conditions=conditions)
            points.extend(batch)
            if not offset:
                return points

    texts = {}
    for point in scroll_all(store.config.text_collection):
        payload = point["payload"]
        texts.setdefault(payload.get("text"), payload.get("file_sorted", ""))

    def order(item):
        suffix = item[1].rsplit("-", 1)[-1]
        return int(suffix) if suffix.isdigit() else 0

    images = []
    for point in scroll_all(store.config.image_collection):
        payload = point["payload"]
        if payload.get("image_url") and payload["image_url"] not in images:
            images.append(payload["image_url"])

    return {
        "code": 200,
        "msg": "success",
        "data": {
            "texts": [text for text, _ in sorted(texts.items(), key=order) if text],
            "images": images,
        },
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


app.include_router(api_router)
app.include_router(storage_router)
