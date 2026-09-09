"""
==========================================================================
多模态 RAG —— FastAPI 接口
（配合 loader.py 使用：上传文档/图片 -> 后台解析分块 -> 查询结果）

运行（在本文件所在目录打开终端）：
    uvicorn api:app --reload
然后浏览器打开： http://127.0.0.1:8000/docs

接口：
    POST /documents       上传文件，返回 task_id（后台解析分块）
    GET  /tasks/{id}      查询解析进度与分块结果
    GET  /health          健康检查
    GET  /formats         列出支持的文件类型
==========================================================================
"""
import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from utils import config, logger, test_db_connection
from loader import (
    SUPPORTED_FORMATS,
    MinerUParser,
    MinerUNotInstalledError,
    LibreOfficeNotInstalledError,
    process_file,
)

UPLOAD_DIR = config.upload_dir
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

TASKS: dict = {}  # task_id -> {status, file, chunks, error}
_sem = asyncio.Semaphore(1)  # 同时只解析一份文档，避免 MinerU/API 并发过高


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时按所选后端检查依赖，提前暴露环境问题
    backend = config.parser_backend
    if backend == "mineru":
        parser = MinerUParser()
        if not parser.check_installation():
            logger.warning(
                "PARSER_BACKEND=mineru，但未检测到 MinerU。请安装：pip install -U 'mineru[core]'"
            )
    else:
        if not config.openai_api_key:
            logger.warning(
                "PARSER_BACKEND=qwen-vl，但未配置 OPENAI_API_KEY，解析会失败。"
                "请在 .env 里填写 DashScope 的 API Key。"
            )
        try:
            import pymupdf  # noqa: F401
        except ImportError:
            logger.warning("PARSER_BACKEND=qwen-vl，但未安装 pymupdf，请 pip install pymupdf")
    yield


app = FastAPI(
    title="多模态 RAG 解析接口",
    description="MinerU 多模态解析 + 分块 + 千问图片描述",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _ingest(task_id: str, file_path: str) -> None:
    """后台任务：解析并分块（阻塞操作放到线程池，不堵住上传请求）。"""
    async with _sem:
        TASKS[task_id]["status"] = "running"
        try:
            chunks = await asyncio.to_thread(process_file, file_path)
            TASKS[task_id]["status"] = "done"
            TASKS[task_id]["chunks"] = chunks
            TASKS[task_id]["chunk_count"] = len(chunks)
        except (MinerUNotInstalledError, LibreOfficeNotInstalledError) as exc:
            TASKS[task_id]["status"] = "failed"
            TASKS[task_id]["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            TASKS[task_id]["status"] = "failed"
            TASKS[task_id]["error"] = f"{type(exc).__name__}: {exc}"


@app.post("/documents")
async def upload(file: UploadFile = File(...)):
    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型 {suffix}，支持：{', '.join(sorted(SUPPORTED_FORMATS))}",
        )

    save_path = UPLOAD_DIR / f"{uuid.uuid4().hex}_{file.filename}"
    save_path.write_bytes(await file.read())  # 先落盘，解析器需要文件路径

    task_id = uuid.uuid4().hex
    TASKS[task_id] = {"status": "pending", "file": file.filename}
    asyncio.create_task(_ingest(task_id, str(save_path)))  # 后台开始解析
    return {"task_id": task_id, "status": "pending"}


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    task = TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task 不存在")
    return task


class ParseBody(BaseModel):
    file_path: str  # 服务器上已存在的文件绝对路径（可选，用于本地文件直接解析）


@app.post("/parse")
async def parse_local(body: ParseBody):
    """直接解析服务器本地文件，同步返回分块结果（适合脚本/调试）。"""
    path = Path(body.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在：{body.file_path}")
    try:
        chunks = await asyncio.to_thread(process_file, str(path))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")
    return {"file": body.file_path, "chunk_count": len(chunks), "chunks": chunks}


@app.get("/formats")
async def formats():
    return {"supported_formats": sorted(SUPPORTED_FORMATS)}


@app.get("/db/test")
async def db_test():
    """测试数据库连接（Milvus/Chroma/MySQL/PostgreSQL，由 .env 的 DB_TYPE 切换）。"""
    return test_db_connection()


@app.get("/health")
async def health():
    return {"status": "ok"}
