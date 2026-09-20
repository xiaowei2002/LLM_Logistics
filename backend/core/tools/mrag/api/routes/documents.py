"""
==========================================================================
知识库文档管理：文档记录落盘 + 列表 / 删除接口

为前端「知识库管理页」补的配套接口，与解析流程解耦：
  - 元数据    uploads/_index.json              （服务重启不丢，不含分块内容）
  - 分块内容  uploads/_chunks/{task_id}.json   （单独存，避免索引文件过大）

解析本身（POST /documents 与后台 _ingest）仍在 api.py 里，
由它调用这里的 record_upload / record_running / record_done / record_failed 登记结果。

接口：
    GET    /documents        文档列表（仅元数据）
    DELETE /documents/{id}   删除文档（原文件 + 分块结果 + 记录）
==========================================================================
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from core.tools.mrag.utils.utils import config, logger

UPLOAD_DIR = config.upload_dir
DOCS_INDEX = UPLOAD_DIR / "_index.json"
CHUNKS_DIR = UPLOAD_DIR / "_chunks"
CHUNKS_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter(tags=["Knowledge Base"])

# task_id -> {task_id, file, size, status, chunk_count, error, uploaded_at, finished_at, stored_as}
# 与 api.py 的 TASKS 指向同一个 dict（api.py 直接导入本模块的 records），避免两处状态不同步
records: Dict[str, Dict[str, Any]] = {}

_LIST_FIELDS = (
    "task_id",
    "file",
    "size",
    "status",
    "chunk_count",
    "error",
    "uploaded_at",
    "finished_at",
)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def decode_filename(name: str) -> str:
    """还原中文文件名。

    multipart 的 filename 由 python-multipart 按 latin-1 解出，
    中文会变成乱码（如 测试文档.txt -> ²âÊÔÎÄµµ.txt）。
    这里按原始字节试 UTF-8（浏览器）再试 GBK（旧客户端），都不行就原样返回。
    """
    for enc in ("utf-8", "gbk"):
        try:
            fixed = name.encode("latin-1").decode(enc)
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if fixed:
            return fixed
    return name


def safe_name(name: str) -> str:
    """只取文件名本身并滤掉危险字符：避免 ../../ 之类的路径穿越写到 uploads 之外。"""
    name = Path(decode_filename(name or "")).name
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(". ")
    return name or "unnamed"


def chunks_path(task_id: str) -> Path:
    return CHUNKS_DIR / f"{task_id}.json"


def save_chunks(task_id: str, chunks: List[Dict]) -> None:
    chunks_path(task_id).write_text(
        json.dumps(chunks, ensure_ascii=False), encoding="utf-8"
    )


def load_chunks(task_id: str) -> Optional[List[Dict]]:
    """服务重启后内存里的 chunks 没了，从磁盘补回来。"""
    path = chunks_path(task_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        logger.warning("分块文件损坏，已忽略：{}", path)
        return None


def _save_index() -> None:
    """元数据落盘（原子替换，避免写一半崩了丢索引）。"""
    data = {tid: {k: v for k, v in item.items() if k != "chunks"} for tid, item in records.items()}
    tmp = DOCS_INDEX.with_name(DOCS_INDEX.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DOCS_INDEX)


def restore_records() -> None:
    """启动时恢复记录；上次没跑完的任务标记为中断。"""
    if not DOCS_INDEX.exists():
        return
    try:
        saved = json.loads(DOCS_INDEX.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        logger.warning("文档索引损坏，已忽略：{}", DOCS_INDEX)
        return
    for tid, item in saved.items():
        if item.get("status") in ("pending", "running"):
            item["status"] = "failed"
            item["error"] = "服务重启，解析中断"
        records[tid] = item
    logger.info("已恢复 {} 条文档记录", len(records))


def record_upload(task_id: str, file_name: str, size: int, stored_as: str) -> None:
    records[task_id] = {
        "task_id": task_id,
        "file": file_name,
        "size": size,
        "status": "pending",
        "stored_as": stored_as,
        "uploaded_at": _now(),
    }
    _save_index()


def record_running(task_id: str) -> None:
    records[task_id]["status"] = "running"
    _save_index()


def record_done(task_id: str, chunks: List[Dict]) -> None:
    records[task_id]["status"] = "done"
    records[task_id]["chunks"] = chunks
    records[task_id]["chunk_count"] = len(chunks)
    records[task_id]["finished_at"] = _now()
    save_chunks(task_id, chunks)
    _save_index()


def record_failed(task_id: str, error: str) -> None:
    records[task_id]["status"] = "failed"
    records[task_id]["error"] = error
    records[task_id]["finished_at"] = _now()
    _save_index()


@router.get("/documents")
async def list_documents():
    """文档列表：按上传时间倒序，只返回元数据（不含分块内容）。"""
    items = sorted(records.values(), key=lambda x: x.get("uploaded_at") or "", reverse=True)
    return {
        "total": len(items),
        "documents": [{k: item.get(k) for k in _LIST_FIELDS} for item in items],
    }


@router.delete("/documents/{task_id}")
async def delete_document(task_id: str):
    """删除文档：原文件 + 分块结果 + 记录一并清掉。"""
    item = records.get(task_id)
    if item is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    if item.get("status") == "running":
        raise HTTPException(status_code=409, detail="文档正在解析，请稍后再试")

    # 原文件：优先按记录的名字删，找不到就按 task_id 前缀兜底
    removed_file = False
    stored = item.get("stored_as")
    if stored:
        target = UPLOAD_DIR / Path(stored).name
        if target.exists():
            target.unlink()
            removed_file = True
    if not removed_file:
        for candidate in UPLOAD_DIR.glob(f"{task_id}_*"):
            candidate.unlink()
            removed_file = True

    chunks_path(task_id).unlink(missing_ok=True)
    records.pop(task_id, None)
    _save_index()

    return {"deleted": task_id, "file": item.get("file"), "file_removed": removed_file}
