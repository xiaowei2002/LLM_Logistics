"""
==========================================================================
多模态文档解析与分块

核心思路：把文档转成图片，交给千问视觉大模型去
「看图解析 + 分块」，而不是靠 MinerU 抽结构化文本。两条后端可用 .env 切换：

    PARSER_BACKEND=qwen-vl   （默认）图片 -> 千问 VL 解析分块
    PARSER_BACKEND=mineru    MinerU 结构化抽取 -> 分块 -> 千问描述多模态

完整流水线（process_file 一条龙，一步步走）：

    输入文件（pdf / 图片 / Word / Excel / PPT / txt / md）
      │
      ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ 第 1 步：数据加载（Data Loading）                            │
  │   按扩展名分发，把「文件」统一变成模型能看的「图片」           │
  │     - PDF             -> pymupdf 逐页渲染 PNG                │
  │     - Word/Excel/PPT  -> LibreOffice 转 PDF -> 逐页渲染 PNG  │
  │     - 图片            -> 直接用                               │
  │     - txt/md          -> 直接读文本（无需转图）               │
  │   （mineru 后端：文件 -> MinerU -> content_list 结构化块）     │
  └─────────────────────────────────────────────────────────────┘
      │  图片列表（或 content_list）
      ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ 第 2 步：分块（Chunking）                                    │
  │   qwen-vl 后端：每页图片 -> 千问 VL 输出结构化 JSON           │
  │                 （heading / text / table markdown），再按      │
  │                 token 预算二次切块                            │
  │   mineru 后端：separate_content 拆纯文本 + 多模态项，token 切块│
  └─────────────────────────────────────────────────────────────┘
      │  文本块 + 多模态项
      ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ 第 3 步：多模态理解（Multimodal Understanding，可选）         │
  │   - 图片/图表 -> 千问 VL（qwen-vl-max-latest）生成详细描述    │
  │   - 表格/公式 -> MinerU 已渲染成图，交千问 VL 描述            │
  │   - 无渲染图时 -> 退回千问文本模型（qwen-plus）总结/解释      │
  └─────────────────────────────────────────────────────────────┘
      │
      ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ 第 4 步：输出                                                │
  │   统一 Chunk 列表（可 JSON 序列化，交给接口 / 下游向量库）     │
  └─────────────────────────────────────────────────────────────┘

运行：
    from core.tools.mrag.document.loader import process_file
    chunks = process_file("demo.pdf")
    for c in chunks:
        print(c["type"], c["content"][:100])

配置：backend/.env（参考 .env.example）
==========================================================================
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.tools.mrag.utils.utils import config, logger

# ==========================================================================
# 配置（由 utils.Config 统一从 .env 读取，这里建模块级别名）
# ==========================================================================
OPENAI_API_KEY = config.openai_api_key
OPENAI_BASE_URL = config.openai_base_url
VL_MODEL = config.vl_model  # 视觉模型：图片描述 / 页面解析
LLM_MODEL = config.llm_model  # 文本模型：表格/公式总结（兜底）

PARSER_BACKEND = config.parser_backend  # qwen-vl（默认） / mineru
RENDER_DPI = config.render_dpi  # 页面渲染成图片的分辨率

MINERU_METHOD = config.mineru_method
MINERU_LANG = config.mineru_lang
MINERU_DEVICE = config.mineru_device
MINERU_BACKEND = config.mineru_backend

CHUNK_TOKEN_SIZE = config.chunk_token_size
CHUNK_OVERLAP = config.chunk_overlap

ENABLE_IMAGE_DESCRIPTION = config.enable_image_description
ENABLE_TABLE_SUMMARY = config.enable_table_summary
ENABLE_EQUATION_SUMMARY = config.enable_equation_summary

OUTPUT_DIR = config.output_dir

_IS_WINDOWS = config.is_windows


# ╔═══════════════════════════════════════════════════════════════════════╗
# ║  第 1 步：数据加载（Data Loading）                                     ║
# ║  输入：文件路径   输出：图片列表（qwen-vl）/ content_list（mineru）     ║
# ╚═══════════════════════════════════════════════════════════════════════╝

# ---- 1.1 支持的文件格式 ----
PDF_FORMATS = {".pdf"}
IMAGE_FORMATS = {".png", ".jpeg", ".jpg", ".bmp", ".tiff", ".tif", ".gif", ".webp"}
OFFICE_FORMATS = {".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}
TEXT_FORMATS = {".txt", ".md"}

# 子集：可被「direct 后端」直接用已装库（python-docx / python-pptx / openpyxl）结构化读取的新格式。
# 老格式 .doc/.ppt/.xls 需 LibreOffice（或 xlrd）转换，direct 后端会给出明确报错。
WORD_FORMATS = {".docx"}
PPT_FORMATS = {".pptx"}
EXCEL_FORMATS = {".xlsx", ".xlsm"}

SUPPORTED_FORMATS = PDF_FORMATS | IMAGE_FORMATS | OFFICE_FORMATS | TEXT_FORMATS


# ---- 1.2 异常 ----
class MinerUNotInstalledError(RuntimeError):
    pass


class LibreOfficeNotInstalledError(RuntimeError):
    pass


# ---- 1.3 渲染成图片（qwen-vl 后端用：文件 -> 图片列表） ----
def libreoffice_command_candidates() -> List[str]:
    """返回 LibreOffice 可执行文件候选（Windows 也探测常见安装位置）。"""
    command_names = ["libreoffice", "soffice"]
    candidates: List[str] = []
    for name in command_names:
        resolved = shutil.which(name)
        if resolved:
            candidates.append(resolved)
        candidates.append(name)
    if _IS_WINDOWS:
        for name in ("soffice.exe", "libreoffice.exe"):
            resolved = shutil.which(name)
            if resolved:
                candidates.append(resolved)
            candidates.append(name)
        for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
            program_files = os.environ.get(env_name)
            if not program_files:
                continue
            lo_program = Path(program_files) / "LibreOffice" / "program"
            for exe in ("soffice.exe", "libreoffice.exe"):
                exe_path = lo_program / exe
                if exe_path.exists():
                    candidates.append(str(exe_path))
    deduped: List[str] = []
    seen = set()
    for c in candidates:
        norm = os.path.normcase(c)
        if norm not in seen:
            seen.add(norm)
            deduped.append(c)
    return deduped


def convert_office_to_pdf(doc_path: Path, output_dir: Path) -> Path:
    """Office（doc/docx/ppt/pptx/xls/xlsx）先经 LibreOffice 转成 PDF。"""
    doc_path = Path(doc_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    final_pdf = output_dir / f"{doc_path.stem}.pdf"
    if final_pdf.exists() and final_pdf.stat().st_size > 100:
        logger.info("Office 已转换过，跳过：{}", final_pdf.name)
        return final_pdf

    logger.info("用 LibreOffice 转换 {} 为 PDF…", doc_path.name)
    commands = libreoffice_command_candidates()
    last_error: Optional[str] = None

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        success = False
        for cmd in commands:
            try:
                convert_cmd = [
                    cmd, "--headless", "--convert-to", "pdf",
                    "--outdir", str(tmp_path), str(doc_path),
                ]
                kwargs = {
                    "capture_output": True,
                    "text": True,
                    "timeout": int(os.getenv("LIBREOFFICE_CONVERT_TIMEOUT", "600")),
                    "encoding": "utf-8",
                    "errors": "ignore",
                }
                if _IS_WINDOWS:
                    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                result = subprocess.run(convert_cmd, **kwargs)
                if result.returncode == 0:
                    success = True
                    break
                last_error = result.stderr
            except FileNotFoundError:
                continue
            except subprocess.TimeoutExpired:
                last_error = "LibreOffice 转换超时"
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)

        if not success:
            raise LibreOfficeNotInstalledError(
                f"Office 文档 {doc_path.name} 转 PDF 失败。请安装 LibreOffice "
                "（https://www.libreoffice.org/download/），或手动转成 PDF。"
                f" 细节：{last_error}"
            )

        pdf_files = list(tmp_path.glob("*.pdf"))
        if not pdf_files or pdf_files[0].stat().st_size < 100:
            raise RuntimeError(f"{doc_path.name} 转出的 PDF 为空或损坏")

        shutil.copy2(pdf_files[0], final_pdf)

    logger.info("已生成 PDF：{}", final_pdf)
    return final_pdf


def _import_pymupdf():
    """pymupdf 新旧包名兼容（新版本 import pymupdf，旧版本 import fitz）。"""
    try:
        import pymupdf
        return pymupdf
    except ImportError:
        import fitz
        return fitz


def render_pdf_to_images(pdf_path: Path, output_dir: Path, dpi: int = RENDER_DPI) -> List[Path]:
    """把 PDF 逐页渲染成 PNG 图片（对齐师兄的 pdf2json）。"""
    pymupdf = _import_pymupdf()
    pdf_path = Path(pdf_path)
    img_dir = output_dir / f"{pdf_path.stem}_images"
    img_dir.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open(str(pdf_path))
    paths: List[Path] = []
    try:
        for page_no in range(len(doc)):
            pix = doc[page_no].get_pixmap(dpi=dpi)
            png = pix.tobytes("png")
            p = img_dir / f"page_{page_no + 1:04d}.png"
            p.write_bytes(png)
            paths.append(p)
    finally:
        doc.close()

    logger.info("{} 已渲染为 {} 页图片 -> {}", pdf_path.name, len(paths), img_dir)
    return paths


def render_to_images(file_path: Path, output_dir: Path, dpi: int = RENDER_DPI) -> List[Path]:
    """第 1 步（qwen-vl 后端）统一入口：文件 -> 图片列表。"""
    file_path = Path(file_path)
    ext = file_path.suffix.lower()

    if ext in PDF_FORMATS:
        return render_pdf_to_images(file_path, output_dir, dpi)
    if ext in IMAGE_FORMATS:
        return [file_path]
    if ext in OFFICE_FORMATS:
        logger.warning("检测到 Office 文档（{}），先经 LibreOffice 转 PDF 再渲染", ext)
        pdf_path = convert_office_to_pdf(file_path, output_dir)
        return render_pdf_to_images(pdf_path, output_dir, dpi)
    if ext in TEXT_FORMATS:
        return []  # 纯文本无需渲染成图
    raise NotImplementedError(f"不支持的文件类型：{ext}")


# ---- 1.4 MinerU 输出解析（mineru 后端用：v2 content_list -> 扁平块） ----
class MineruContentListV2Error(ValueError):
    """无法把值解释为 MinerU v2 content list。"""


MINERU_V2_LAYOUT_TYPES = frozenset(
    {"page_header", "page_footer", "page_number", "page_aside_text", "page_footnote"}
)
_TEXT_TYPES = frozenset({"title", "paragraph", "abstract", "phonetic", "text"})
_LIST_TYPES = frozenset({"list", "index"})
_INLINE_EQUATION_TYPES = frozenset({"equation_inline", "inline_equation"})
_REFERENCE_TYPES = frozenset({"ref_text"})

_KNOWN_BLOCK_TYPES = frozenset(
    {
        *_TEXT_TYPES,
        *_LIST_TYPES,
        *_INLINE_EQUATION_TYPES,
        *_REFERENCE_TYPES,
        "image",
        "table",
        "equation_interline",
        "chart",
        "code",
        "algorithm",
        *MINERU_V2_LAYOUT_TYPES,
    }
)


def _cjk(char: str) -> bool:
    return "㐀" <= char <= "鿿"


def _text_value(value: Any) -> str:
    """抽取可见文本，同时保留行内公式语义。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        span_type = value.get("type")
        if isinstance(span_type, str) and span_type in _INLINE_EQUATION_TYPES:
            eq = _text_value(value.get("content", value.get("text", "")))
            if not eq:
                return ""
            return eq if eq.startswith("$") else f"${eq}$"
        content = value.get("content")
        if content not in (None, ""):
            text = _text_value(content)
            if text:
                return text
        children = value.get("children")
        if children:
            return _text_value(children)
        for key in (
            "title_content",
            "paragraph_content",
            "math_content",
            "code_content",
            "algorithm_content",
            "text",
        ):
            if key in value:
                text = _text_value(value[key])
                if text:
                    return text
        return ""
    if isinstance(value, (list, tuple)):
        return _join_text_parts([_text_value(part) for part in value])
    return str(value).strip()


def _join_text_parts(parts: List[str]) -> str:
    result = ""
    for part in parts:
        if not part:
            continue
        if (
            result
            and not result[-1].isspace()
            and not part[0].isspace()
            and _needs_separator(result[-1], part[0])
        ):
            result += " "
        result += part
    return result.strip()


def _needs_separator(left: str, right: str) -> bool:
    if _cjk(left) or _cjk(right):
        return False
    return not (right in ",.;:!?)]}" or left in "([{")


def _caption_values(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        text = _text_value(value).strip()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        result: List[str] = []
        for entry in value:
            result.extend(_caption_values(entry))
        return result
    text = _text_value(value).strip()
    return [text] if text else []


def _list_items(value: Any) -> List[str]:
    if not isinstance(value, (list, tuple)):
        text = _text_value(value)
        return [text] if text else []
    result: List[str] = []
    for entry in value:
        if isinstance(entry, dict):
            entry = entry.get("item_content", entry.get("content", entry.get("text", entry)))
        text = _text_value(entry).strip()
        if text:
            result.append(text)
    return result


def _source_path(source: Any) -> str:
    if isinstance(source, dict):
        source = source.get("path", source.get("source", source.get("url", "")))
    if isinstance(source, str):
        source = source.strip()
        basename = source.replace("\\", "/").rsplit("/", 1)[-1]
        if basename in ("", ".", ".."):
            return ""
        return source
    return ""


def _positive_int(value: Any) -> Optional[int]:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _page_idx(value: Any, fallback: int) -> int:
    try:
        return int(value) if value is not None else fallback
    except (TypeError, ValueError):
        return fallback


def _finish_item(item: Dict, block: Dict, page_idx: int, block_type: str) -> Dict:
    item["page_idx"] = _page_idx(block.get("page_idx"), page_idx)
    item["_mineru_v2_type"] = block_type
    bbox = block.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        item["bbox"] = list(bbox)
    anchor = block.get("anchor")
    if isinstance(anchor, str) and anchor.strip():
        item["anchor"] = anchor.strip()
    return item


def _convert_block(block: Dict, page_idx: int, include_layout_blocks: bool) -> Optional[Dict]:
    block_type = block.get("type")
    if not isinstance(block_type, str) or not block_type.strip():
        return None
    block_type = block_type.strip()

    if block_type not in _KNOWN_BLOCK_TYPES:
        logger.warning("跳过不支持的 MinerU 块类型: {}", block_type)
        return None

    if block_type in MINERU_V2_LAYOUT_TYPES:
        if not include_layout_blocks:
            return None
        content = block.get("content", {})
        if not isinstance(content, dict):
            content = {}
        text = _text_value(content.get(f"{block_type}_content", content.get("text", content))).strip()
        if not text:
            return None
        return _finish_item({"type": block_type, "text": text}, block, page_idx, block_type)

    content = block.get("content", {})
    if not isinstance(content, dict):
        content = {"content": content} if content not in (None, "") else {}

    if block_type in _INLINE_EQUATION_TYPES:
        eq = _text_value(content.get("math_content", content.get("text", content))).strip()
        if not eq:
            return None
        if not eq.startswith("$"):
            eq = f"${eq}$"
        return _finish_item({"type": "text", "text": eq}, block, page_idx, block_type)

    if block_type in _TEXT_TYPES:
        text = _text_value(content.get(f"{block_type}_content", content.get("text", content.get("content", content)))).strip()
        if not text:
            return None
        item: Dict[str, Any] = {"type": "text", "text": text}
        if block_type == "title":
            level = _positive_int(content.get("level", block.get("level")))
            if level is not None:
                item["text_level"] = level
        return _finish_item(item, block, page_idx, block_type)

    if block_type in _LIST_TYPES:
        items = _list_items(content.get("list_items", []))
        if not items:
            fallback = _text_value(content.get("text", content)).strip()
            if fallback:
                items = [fallback]
        if not items:
            return None
        item: Dict[str, Any] = {"type": "text", "text": "\n".join(items), "list_items": items}
        return _finish_item(item, block, page_idx, block_type)

    if block_type in _REFERENCE_TYPES:
        text = _text_value(content.get("ref_text_content", content.get("text", content.get("content", content)))).strip()
        if not text:
            return None
        return _finish_item({"type": "text", "text": text, "list_type": "reference_list"}, block, page_idx, block_type)

    if block_type == "image":
        item: Dict[str, Any] = {"type": "image", "img_path": ""}
        image_path = _source_path(content.get("image_source"))
        if image_path:
            item["img_path"] = image_path
        visual = _text_value(content.get("content", "")).strip()
        if visual:
            item["content"] = visual
        caps = _caption_values(content.get("image_caption"))
        if caps:
            item["image_caption"] = caps
        return _finish_item(item, block, page_idx, block_type)

    if block_type == "table":
        item: Dict[str, Any] = {"type": "table"}
        table_path = _source_path(content.get("image_source"))
        if table_path:
            item["img_path"] = table_path
        body = content.get("html")
        if body in (None, ""):
            body = content.get("table_body", content.get("table_data"))
        if body not in (None, ""):
            item["table_body"] = body
        caps = _caption_values(content.get("table_caption"))
        if caps:
            item["table_caption"] = caps
        return _finish_item(item, block, page_idx, block_type)

    if block_type == "equation_interline":
        item: Dict[str, Any] = {"type": "equation"}
        eq = _text_value(content.get("math_content", content.get("text", content.get("latex", "")))).strip()
        if eq:
            item["text"] = eq
        fmt = content.get("math_type", content.get("text_format"))
        if isinstance(fmt, str) and fmt:
            item["text_format"] = fmt
        eq_path = _source_path(content.get("image_source"))
        if eq_path:
            item["img_path"] = eq_path
        return _finish_item(item, block, page_idx, block_type)

    if block_type == "chart":
        item: Dict[str, Any] = {"type": "chart"}
        chart_path = _source_path(content.get("image_source"))
        if chart_path:
            item["img_path"] = chart_path
        caps = _caption_values(content.get("chart_caption"))
        if caps:
            item["chart_caption"] = caps
        return _finish_item(item, block, page_idx, block_type)

    if block_type in {"code", "algorithm"}:
        key = "code_content" if block_type == "code" else "algorithm_content"
        code = _text_value(content.get(key, content.get("content", ""))).strip()
        item: Dict[str, Any] = {"type": "code", "sub_type": block_type, "code_body": code}
        if code:
            item["content"] = code
        return _finish_item(item, block, page_idx, block_type)

    raise AssertionError(f"未处理的 MinerU 块类型: {block_type}")


def convert_mineru_content_list_v2(
    payload: Any, *, include_layout_blocks: bool = False
) -> List[Dict[str, Any]]:
    """把 MinerU 的按页分组 v2 输出（list[list[dict]]）转成扁平块列表。"""
    if not isinstance(payload, list) or not payload:
        raise MineruContentListV2Error("MinerU content_list_v2 必须是非空的分页列表")

    converted: List[Dict[str, Any]] = []
    has_block = False
    for page_idx, page in enumerate(payload):
        if not isinstance(page, list):
            raise MineruContentListV2Error(f"MinerU content_list_v2 第 {page_idx} 页必须是列表")
        for block in page:
            has_block = True
            if not isinstance(block, dict):
                raise MineruContentListV2Error(
                    f"MinerU content_list_v2 块 {page_idx}:{block} 必须是对象"
                )
            item = _convert_block(block, page_idx, include_layout_blocks)
            if item is not None:
                converted.append(item)

    if not has_block:
        raise MineruContentListV2Error("MinerU content_list_v2 不含任何内容块")
    if not converted:
        raise MineruContentListV2Error("MinerU content_list_v2 不含受支持的内容块")
    return converted


# ---- 1.5 MinerU 解析器（mineru 后端用：调 CLI + 读回结果） ----
class MinerUParser:
    """MinerU 文档解析器（CLI 封装）。"""

    def __init__(
        self,
        method: str = MINERU_METHOD,
        lang: str = MINERU_LANG,
        device: str = MINERU_DEVICE,
        backend: str = MINERU_BACKEND,
    ) -> None:
        self.method = method
        self.lang = lang
        self.device = device
        self.backend = backend

    def check_installation(self) -> bool:
        """检查 MinerU 是否可用。"""
        try:
            result = subprocess.run(
                ["mineru", "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                creationflags=subprocess.CREATE_NO_WINDOW if _IS_WINDOWS else 0,
            )
            logger.info("MinerU 版本：{}", result.stdout.strip().splitlines()[0] if result.stdout.strip() else "?")
            return result.returncode == 0
        except FileNotFoundError:
            logger.warning("未检测到 mineru 命令")
            return False

    def _run_mineru(self, input_path: Path, output_dir: Path, method: str) -> None:
        """执行 mineru CLI 命令。"""
        cmd = ["mineru", "-p", str(input_path), "-o", str(output_dir), "-m", method]
        if self.backend:
            cmd.extend(["-b", self.backend])
        if self.lang:
            cmd.extend(["-l", self.lang])
        if self.device:
            cmd.extend(["-d", self.device])

        logger.info("执行 mineru：{}", " ".join(cmd))
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if _IS_WINDOWS else 0,
            )
            assert process.stdout is not None
            for line in iter(process.stdout.readline, ""):
                if line.strip():
                    logger.debug("[MinerU] {}", line.strip())
            process.stdout.close()
            return_code = process.wait()
            if return_code != 0:
                raise MinerUNotInstalledError(
                    f"mineru 命令失败（退出码 {return_code}）。请确认已安装：pip install -U 'mineru[core]'"
                )
        except FileNotFoundError:
            raise MinerUNotInstalledError(
                "未找到 mineru 命令。请安装：pip install -U 'mineru[core]'"
            )

    def _find_output_files(self, output_dir: Path, file_stem: str, method: str) -> Tuple[Optional[Path], Path]:
        """定位 MinerU 输出（v2 优先，legacy 兜底），返回 (json_path, images_base_dir)。"""
        file_stem_subdir = output_dir / file_stem
        search_dirs = [output_dir]
        if file_stem_subdir.is_dir():
            search_dirs.extend(
                d for d in sorted(file_stem_subdir.iterdir(), key=lambda p: p.name)
                if d.is_dir()
            )
            if (file_stem_subdir / method).is_dir():
                search_dirs.insert(1, file_stem_subdir / method)

        candidate_names = (
            f"{file_stem}_content_list_v2.json",
            "content_list_v2.json",
            f"{file_stem}_content_list.json",
            "content_list.json",
        )
        for directory in search_dirs:
            for name in candidate_names:
                candidate = directory / name
                if candidate.is_file():
                    return candidate, candidate.parent

        fallback = file_stem_subdir if file_stem_subdir.is_dir() else output_dir
        return None, fallback

    def _read_output_files(
        self, output_dir: Path, file_stem: str, method: str, include_layout_blocks: bool = False
    ) -> List[Dict]:
        """读 MinerU 输出 -> 扁平 content_list（并修正图片绝对路径）。"""
        json_file, images_base_dir = self._find_output_files(output_dir, file_stem, method)
        if json_file is None or not json_file.is_file():
            raise FileNotFoundError(
                f"未在 {output_dir} 下找到 MinerU 的 content_list 输出。"
                "请确认 MinerU 正常运行并生成了 *_content_list.json。"
            )

        logger.info("读取 MinerU 输出：{}", json_file)
        raw = json.loads(json_file.read_text(encoding="utf-8"))

        is_v2 = isinstance(raw, list) and raw and isinstance(raw[0], list)
        if is_v2:
            content_list = convert_mineru_content_list_v2(
                raw, include_layout_blocks=include_layout_blocks
            )
        else:
            content_list = raw

        if not isinstance(content_list, list):
            raise ValueError("MinerU content_list 必须是 JSON 数组")

        # 字段别名归一化 + 图片路径转绝对路径
        alias_map = {"img_caption": "image_caption", "img_footnote": "image_footnote"}
        for item in content_list:
            if not isinstance(item, dict):
                continue
            for old, new in alias_map.items():
                if old in item and new not in item:
                    item[new] = item[old]
            for field in ("img_path", "table_img_path", "equation_img_path"):
                if field in item and item[field]:
                    abs_path = (images_base_dir / item[field]).resolve()
                    if abs_path.is_relative_to(images_base_dir.resolve()) or abs_path.exists():
                        item[field] = str(abs_path)
                    else:
                        logger.warning("图片路径越界，跳过：{}", item[field])
                        item[field] = ""

        return content_list

    def parse_pdf(self, pdf_path: Path, output_dir: Path, method: Optional[str] = None) -> List[Dict]:
        method = method or self.method
        self._run_mineru(pdf_path, output_dir, method)
        return self._read_output_files(output_dir, pdf_path.stem, method)

    def parse_image(self, image_path: Path, output_dir: Path) -> List[Dict]:
        mineru_supported = {".png", ".jpeg", ".jpg"}
        input_path = image_path
        temp_file: Optional[Path] = None
        if image_path.suffix.lower() not in mineru_supported:
            try:
                from PIL import Image

                with Image.open(image_path) as img:
                    if img.mode in ("RGBA", "LA", "P"):
                        img = img.convert("RGB")
                    temp_file = output_dir / f"{image_path.stem}_preview.png"
                    img.save(temp_file, "PNG")
                input_path = temp_file
                logger.info("图片已预处理为 PNG：{}", temp_file)
            except ImportError:
                logger.warning("未安装 Pillow，无法预处理 {}，直接交给 MinerU", image_path.name)

        try:
            self._run_mineru(input_path, output_dir, "ocr")
            return self._read_output_files(output_dir, input_path.stem, "ocr")
        finally:
            if temp_file is not None and temp_file.exists():
                temp_file.unlink()

    def parse_office_doc(self, doc_path: Path, output_dir: Path) -> List[Dict]:
        pdf_path = convert_office_to_pdf(doc_path, output_dir)
        return self.parse_pdf(pdf_path, output_dir)

    def parse_text_file(self, text_path: Path) -> List[Dict]:
        text = text_path.read_text(encoding="utf-8", errors="ignore")
        blocks: List[Dict] = []
        for para in re.split(r"\n\s*\n", text):
            para = para.strip()
            if para:
                blocks.append({"type": "text", "text": para, "page_idx": 0})
        return blocks

    def parse_document(self, file_path: Path, output_dir: Path) -> List[Dict]:
        """mineru 后端第 1 步统一入口：按扩展名分发。"""
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在：{file_path}")
        ext = file_path.suffix.lower()

        if ext in PDF_FORMATS:
            return self.parse_pdf(file_path, output_dir)
        if ext in IMAGE_FORMATS:
            return self.parse_image(file_path, output_dir)
        if ext in OFFICE_FORMATS:
            logger.warning("检测到 Office 文档（{}），需先经 LibreOffice 转 PDF", ext)
            return self.parse_office_doc(file_path, output_dir)
        if ext in TEXT_FORMATS:
            return self.parse_text_file(file_path)
        raise NotImplementedError(f"不支持的文件类型：{ext}")


# ---- 1.6 结构化直接加载（direct 后端用：Excel / PPT / docx -> markdown） ----
# 不用 LibreOffice、不用 pymupdf，直接用已安装的 openpyxl / python-pptx / python-docx
# 把 Office 文档读成结构化 markdown，再统一交给第 2 步按 token 分块。
# 这正是 RAG-Anything「先解析成结构化 markdown，再分块」的思路，且对 Excel/PPT
# 能保留表格单元格、幻灯片结构，不像「转 PDF 渲染图片」那样丢失结构。

def _rows_to_markdown_table(rows: List[List[Any]], first_row_as_header: bool = True) -> str:
    """把二维表（Excel 区域 / PPT 表格 / docx 表格）转成 markdown 表格字符串。"""
    rows = [[str(c) if c is not None else "" for c in row] for row in rows]
    rows = [r for r in rows if any(c.strip() for c in r)]  # 去掉全空行
    if not rows:
        return ""
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]  # 对齐列数

    def _esc(cell: str) -> str:
        return cell.replace("\n", " ").replace("|", "\\|").strip()

    header = [_esc(c) for c in rows[0]] if first_row_as_header else [""] * ncol
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * ncol) + " |",
    ]
    body_start = 1 if first_row_as_header else 0
    for row in rows[body_start:]:
        lines.append("| " + " | ".join(_esc(c) for c in row) + " |")
    return "\n".join(lines)


def load_excel_to_markdown(file_path: Path) -> str:
    """Excel -> markdown：每个工作表一张 markdown 表格（openpyxl 读 .xlsx/.xlsm）。"""
    ext = file_path.suffix.lower()

    if ext == ".xls":
        # 老格式：用 pandas（需要 xlrd 引擎）
        try:
            import pandas as pd
        except ImportError:
            raise RuntimeError("解析 .xls 需安装 xlrd：pip install xlrd")
        parts: List[str] = []
        try:
            sheets = pd.read_excel(file_path, sheet_name=None, header=None)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"解析 .xls 失败（可能需要 xlrd：pip install xlrd）：{exc}")
        for name, df in sheets.items():
            table = _rows_to_markdown_table(df.astype(str).values.tolist())
            if table:
                parts.append(f"## 工作表：{name}")
                parts.append(table)
        return "\n\n".join(parts)

    import openpyxl

    wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
    parts: List[str] = []
    try:
        for ws in wb.worksheets:
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            table = _rows_to_markdown_table(rows)
            if table:
                parts.append(f"## 工作表：{ws.title}")
                parts.append(table)
    finally:
        wb.close()
    return "\n\n".join(parts)


def load_ppt_to_markdown(file_path: Path, images_dir: Optional[Path] = None) -> str:
    """PPT -> markdown：逐页提取标题、正文、表格、备注，并导出图片到 images_dir。"""
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    prs = Presentation(str(file_path))
    parts: List[str] = []
    img_counter = 0

    for idx, slide in enumerate(prs.slides, 1):
        parts.append(f"## 幻灯片 {idx}")
        for shape in slide.shapes:
            try:
                if shape.has_text_frame:
                    text = shape.text_frame.text.strip()
                    if not text:
                        continue
                    is_title = False
                    try:
                        is_title = bool(
                            shape.is_placeholder and shape.placeholder_format.idx == 0
                        )
                    except Exception:  # noqa: BLE001
                        is_title = False
                    if is_title:
                        parts.append(f"### {text}")
                    else:
                        parts.append(text)

                if getattr(shape, "has_table", False):
                    rows = [[cell.text for cell in row.cells] for row in shape.table.rows]
                    table = _rows_to_markdown_table(rows)
                    if table:
                        parts.append(table)

                if shape.shape_type == MSO_SHAPE_TYPE.PICTURE and images_dir is not None:
                    try:
                        image = shape.image
                        ext = (image.ext or "png").lstrip(".")
                        img_counter += 1
                        out = images_dir / f"ppt_{idx}_{img_counter}.{ext}"
                        out.write_bytes(image.blob)
                        parts.append(f"![幻灯片 {idx} 图片 {img_counter}]({out.name})")
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                continue

        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parts.append(f"备注：{notes}")

    return "\n\n".join(parts)


def load_docx_to_markdown(file_path: Path, images_dir: Optional[Path] = None) -> str:
    """docx -> markdown：段落（按样式转标题）、表格、图片。"""
    from docx import Document
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(file_path))
    md: List[str] = []
    img_counter = 0

    for element in doc.element.body:
        if isinstance(element, CT_P):
            para = Paragraph(element, doc)
            text = para.text.strip()
            style = (para.style.name if para.style else "") or ""
            if text:
                if style.startswith("Heading 1"):
                    md.append(f"\n# {text}")
                elif style.startswith("Heading 2"):
                    md.append(f"\n## {text}")
                elif style.startswith("Heading 3"):
                    md.append(f"\n### {text}")
                elif style.startswith("Heading 4"):
                    md.append(f"\n#### {text}")
                else:
                    md.append(text)

            if images_dir is not None:
                for run in para.runs:
                    for drawing in run.element.findall(
                        ".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}drawing"
                    ):
                        for blip in drawing.findall(
                            ".//{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
                        ):
                            embed = blip.get(
                                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
                            )
                            if not embed:
                                continue
                            try:
                                part = doc.part.related_parts[embed]
                                img_counter += 1
                                out = images_dir / f"docx_img_{img_counter}.png"
                                out.write_bytes(part.blob)
                                md.append(f"![图片 {img_counter}]({out.name})")
                            except Exception:  # noqa: BLE001
                                pass

        elif isinstance(element, CT_Tbl):
            table = Table(element, doc)
            rows = [[cell.text for cell in row.cells] for row in table.rows]
            md.append(_rows_to_markdown_table(rows))

    return "\n\n".join(md)


def extract_pdf_text(pdf_path: Path) -> str:
    """本地提取 PDF 纯文本（direct 后端用，不需要 key）。

    只抽「文字层」，不识别版式/表格/图片；扫描版 PDF（无文字层）抽不到东西，
    那种需要 qwen-vl 后端做 OCR。
    """
    try:
        pymupdf = _import_pymupdf()
    except ImportError:
        raise RuntimeError("提取 PDF 文本需要 pymupdf，请先：pip install pymupdf")

    doc = pymupdf.open(str(pdf_path))
    parts: List[str] = []
    try:
        for i, page in enumerate(doc):
            text = page.get_text().strip()
            if text:
                parts.append(f"\n<!-- 第 {i + 1} 页 -->\n{text}")
    finally:
        doc.close()
    return "\n".join(parts)


# ╔═══════════════════════════════════════════════════════════════════════╗
# ║  第 2 步：分块（Chunking）                                             ║
# ║  qwen-vl：图片 -> 千问 VL 结构化 JSON -> token 分块                    ║
# ║  mineru：content_list -> 纯文本 + 多模态项 -> token 分块                ║
# ║  direct ：结构化 markdown -> token 分块                                 ║
# ╚═══════════════════════════════════════════════════════════════════════╝

# ---- 2.1 千问客户端（第 2 步解析与第 3 步描述共用） ----
class QwenClient:
    """千问调用封装（DashScope OpenAI 兼容接口）。"""

    def __init__(self) -> None:
        self.available = bool(OPENAI_API_KEY)
        if not self.available:
            logger.warning("未配置 OPENAI_API_KEY，多模态描述将降级为原始结构")
            self.vl = None
            self.llm = None
            return
        from openai import OpenAI

        self.vl = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
        self.llm = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)

    def _chat(self, client, model: str, messages: List[Dict], temperature: float = 0.0) -> str:
        resp = client.chat.completions.create(model=model, messages=messages, temperature=temperature)
        return (resp.choices[0].message.content or "").strip()

    def _image_data_uri(self, image_path: Path) -> str:
        mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
        return f"data:{mime};base64," + base64.b64encode(image_path.read_bytes()).decode("ascii")

    def _describe_vl(self, image_path: str, prompt: str) -> str:
        """把一张图片交给千问 VL，返回回复文本（所有图片任务的公共入口）。"""
        if not self.available or self.vl is None:
            return ""
        image_path = Path(image_path)
        if not image_path.exists():
            logger.warning("图片不存在，跳过：{}", image_path)
            return ""
        data_uri = self._image_data_uri(image_path)
        try:
            return self._chat(
                self.vl,
                VL_MODEL,
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_uri}},
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("千问 VL 调用失败：{}", exc)
            return ""

    def chat_vl(self, image_path: str, prompt: str) -> str:
        """图片 + prompt -> 原始回复（结构化解析用，返回 JSON 文本）。"""
        return self._describe_vl(image_path, prompt)

    def describe_image(self, image_path: str, section_path: str = "", captions: List[str] = None, neighbor_text: str = "") -> str:
        """图片/图表描述：把图片交给千问 VL，结合章节与邻近正文。"""
        prompt = (
            "你是专业的图像分析专家。请详细描述这张图片的内容，用于知识检索。要求：\n"
            "1. 描述整体构图、布局与结构；\n"
            "2. 逐字转录所有文字标签（标题、标注、坐标轴、节点名、图例等）；\n"
            "3. 对图表/流程图/示意图：列出每个节点、边、关系与连接方向；\n"
            "4. 对照片/插图：识别物体、人物、文字与视觉元素；\n"
            "5. 说明元素之间的关系与含义；\n"
            "6. 使用具体名称，不要用代词。\n\n"
            f"文档章节路径：{section_path or '无'}\n"
            f"图片标题：{', '.join(captions) if captions else '无'}\n"
            f"邻近正文：{neighbor_text or '无'}\n\n"
            "请直接输出中文详细描述，不要输出 JSON。"
        )
        return self._describe_vl(image_path, prompt)

    def describe_table_image(self, table_img_path: str, table_body: str = "", caption: str = "", section_path: str = "") -> str:
        """表格描述：把 MinerU 渲染出的表格图片交给千问 VL。"""
        prompt = (
            "你是专业的数据分析师。请分析这张表格图片，说明其结构、关键数据与结论，"
            "并逐字转录表头与关键单元格。\n\n"
            f"文档章节路径：{section_path or '无'}\n"
            f"表格标题：{caption or '无'}\n"
            f"表格结构（辅助参考）：\n{table_body or '无'}\n\n"
            "请直接输出中文简洁总结，不要输出 JSON。"
        )
        return self._describe_vl(table_img_path, prompt)

    def describe_equation_image(self, eq_img_path: str, eq_text: str = "", eq_format: str = "", section_path: str = "") -> str:
        """公式描述：把 MinerU 渲染出的公式图片交给千问 VL。"""
        prompt = (
            "你是数学家。请解释这张公式图片的含义、变量与应用场景。\n\n"
            f"文档章节路径：{section_path or '无'}\n"
            f"公式（辅助参考）：{eq_text or '无'}\n"
            f"格式：{eq_format or 'latex'}\n\n"
            "请直接输出中文简洁解释，不要输出 JSON。"
        )
        return self._describe_vl(eq_img_path, prompt)

    # ---- 文本模型兜底（多模态项没有渲染图时用） ----
    def summarize_table(self, table_body: str, caption: str = "") -> str:
        if not self.available or self.llm is None:
            return ""
        prompt = (
            "你是专业的数据分析师。请分析下面的表格，说明其结构、关键数据与结论。\n\n"
            f"表格标题：{caption or '无'}\n表格内容：\n{table_body}\n\n"
            "请直接输出中文简洁总结，不要输出 JSON。"
        )
        try:
            return self._chat(self.llm, LLM_MODEL, [{"role": "user", "content": prompt}])
        except Exception as exc:  # noqa: BLE001
            logger.warning("表格总结失败：{}", exc)
            return ""

    def explain_equation(self, equation_text: str, equation_format: str = "") -> str:
        if not self.available or self.llm is None:
            return ""
        prompt = (
            "你是数学家。请解释下面公式的含义、变量与应用场景。\n\n"
            f"公式：{equation_text}\n格式：{equation_format or 'latex'}\n\n"
            "请直接输出中文简洁解释，不要输出 JSON。"
        )
        try:
            return self._chat(self.llm, LLM_MODEL, [{"role": "user", "content": prompt}])
        except Exception as exc:  # noqa: BLE001
            logger.warning("公式解释失败：{}", exc)
            return ""


# ---- 2.2 qwen-vl 结构化解析（图片 -> 千问 VL -> heading/text/table） ----
# 对齐师兄 pdf2json.py 的 prompt：模型看图识别版式、OCR 文字、解析表格，一步完成解析+分块
STRUCT_PROMPT = """你是一个文档结构化解析专家。这是某文档的第 {page_no} 页（图片）。请识别页面内容并输出结构化 JSON。

先判断本页类型：
- cover：封面、书名页、版权页
- toc：目录页
- frontmatter：前置说明页，如前言、序、引言、编委名单等
- backmatter：后置说明页，如参考文献、索引、致谢、后记等
- empty：空白页，或仅含页眉、页脚、页码等版式元素
- content：含实质正文

规则：
1. 若类型为 cover / toc / frontmatter / backmatter / empty，输出 {{"skip": true, "type": "cover|toc|frontmatter|backmatter|empty", "title": "", "blocks": []}}，不要提取任何正文。
2. 若为 content：跳过页眉、页脚、页码；把正文拆分为若干 block：
   - heading：章节标题，附 level（1=章, 2=节, 3=条, 4=款）
   - text：正文段落，保留编号、定义、数据等关键信息，不要改写
   - table：表格，用 markdown 表格表示（| 表头 | ... | 与 |---| 分隔行）
   注意：含规范性/资料性技术内容的附录（appendix）应视为 content 并正常提取，不要当作 backmatter 跳过。
3. title 字段：仅当本页明确出现文档主标题（封面或首页大标题）时才填写，否则留空字符串 ""。
4. 只输出 JSON，不要任何解释文字。

输出格式示例：
{{"skip": false, "type": "content", "title": "", "blocks": [{{"type": "heading", "level": 2, "text": "3.1 物流"}}, {{"type": "text", "content": "根据实际需要……"}}]}}"""


def _parse_json(text: str) -> Dict:
    """剥离可能的 markdown 代码围栏后解析 JSON，失败时降级为整段文本。"""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            "skip": False,
            "type": "content",
            "title": "",
            "blocks": [{"type": "text", "content": text}],
        }


def parse_page_image(qwen: QwenClient, image_path: Path, page_no: int) -> Dict:
    """调用千问 VL 解析单页图片，返回结构化 blocks。"""
    prompt = STRUCT_PROMPT.format(page_no=page_no)
    raw = qwen.chat_vl(str(image_path), prompt)
    return _parse_json(raw)


def assemble_sections(page_records: List[Dict]) -> List[Dict]:
    """把逐页 block 合并成扁平的 section 列表（heading + content），对齐师兄 _assemble。"""
    sections: List[Dict] = []
    current: Optional[Dict] = None

    for record in page_records:
        for block in record.get("blocks", []):
            btype = block.get("type")
            if btype == "heading":
                if current and current["content"].strip():
                    sections.append(current)
                current = {
                    "heading": block.get("text", ""),
                    "level": block.get("level", 1),
                    "content": "",
                }
            elif btype in ("text", "table"):
                if current is None:
                    current = {"heading": "", "level": 0, "content": ""}
                current["content"] += block.get("content", "") + "\n"

    if current and current["content"].strip():
        sections.append(current)

    return sections


def sections_to_chunks(
    sections: List[Dict],
    source: str,
    chunk_token_size: int = CHUNK_TOKEN_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
) -> List["Chunk"]:
    """把 section 列表按 token 预算切成文本 Chunk，附带章节路径。"""
    chunks: List["Chunk"] = []
    heading_chain: List[Tuple[int, str]] = []

    for section in sections:
        heading = str(section.get("heading", "") or "").strip()
        level = int(section.get("level", 0) or 0)
        content = str(section.get("content", "") or "").strip()

        if heading:
            while heading_chain and heading_chain[-1][0] >= level:
                heading_chain.pop()
            heading_chain.append((level, heading))
        section_path = " > ".join(t for _, t in heading_chain)

        text = (heading + "\n" + content) if heading else content
        for part in split_text_into_chunks(text, chunk_token_size, chunk_overlap):
            chunks.append(
                Chunk(
                    chunk_id=_chunk_id(part),
                    type="text",
                    content=part,
                    page_idx=0,
                    section_path=section_path,
                    metadata={"source": source},
                )
            )
    return chunks


# ---- 2.3 mineru 后端：拆分纯文本与多模态项 ----
def extract_section_path_from_content_list(content_list: List[Dict], current_index: int) -> str:
    """用前置标题块重建层级章节路径，如「第三章 > 物流成本」。"""
    if not content_list or current_index is None:
        return ""
    heading_chain: List[Tuple[int, str]] = []
    for item in content_list[: max(0, int(current_index))]:
        if not isinstance(item, dict) or item.get("type", "text") != "text":
            continue
        text = str(item.get("text", "") or "").strip()
        if not text:
            continue
        try:
            level = int(item.get("text_level", 0) or 0)
        except (TypeError, ValueError):
            continue
        if level <= 0:
            continue
        while heading_chain and heading_chain[-1][0] >= level:
            heading_chain.pop()
        heading_chain.append((level, text))
    return " > ".join(t for _, t in heading_chain)


def extract_neighbor_text_from_content_list(
    content_list: List[Dict], current_index: int, window_size: int = 3
) -> str:
    """收集某 item 附近的文本块，作为图片描述的上下文。"""
    if not content_list or current_index is None:
        return ""
    try:
        idx = int(current_index)
    except (TypeError, ValueError):
        return ""
    if idx < 0 or idx >= len(content_list):
        return ""
    start = max(0, idx - window_size)
    end = min(len(content_list), idx + window_size + 1)
    parts: List[str] = []
    for pos in range(start, end):
        if pos == idx:
            continue
        item = content_list[pos]
        if isinstance(item, dict) and item.get("type", "text") == "text":
            text = str(item.get("text", "") or "").strip()
            if text:
                parts.append(text)
    return " ".join(parts)


def separate_content(content_list: List[Dict]) -> Tuple[str, List[Dict]]:
    """把扁平块列表拆成「纯文本」和「多模态项」。"""
    text_parts: List[str] = []
    multimodal_items: List[Dict] = []

    for index, item in enumerate(content_list):
        content_type = item.get("type", "text")
        if content_type == "text":
            text = str(item.get("text", "") or "")
            if text.strip():
                text_parts.append(text)
        else:
            m_item = dict(item)
            m_item.setdefault("_content_list_index", index)
            if content_type in ("image", "chart"):
                m_item.setdefault(
                    "_section_path", extract_section_path_from_content_list(content_list, index)
                )
                m_item.setdefault(
                    "_neighbor_text", extract_neighbor_text_from_content_list(content_list, index)
                )
            multimodal_items.append(m_item)

    text_content = "\n\n".join(text_parts)
    logger.info(
        "第 2 步·拆分：纯文本 {} 字符，多模态项 {} 个",
        len(text_content),
        len(multimodal_items),
    )
    return text_content, multimodal_items


# ---- 2.4 文本按 token 预算切块（段落优先） ----
try:
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")
    _TIKTOKEN_AVAILABLE = True
except Exception:  # noqa: BLE001
    _ENC = None
    _TIKTOKEN_AVAILABLE = False


def _count_tokens(text: str) -> int:
    if _TIKTOKEN_AVAILABLE:
        return len(_ENC.encode(text, disallowed_special=()))
    cjk = sum(1 for ch in text if "㐀" <= ch <= "鿿")
    latin = len(text) - cjk
    return cjk + max(1, latin // 4)


def split_text_into_chunks(
    text: str, chunk_token_size: int = CHUNK_TOKEN_SIZE, overlap_token_size: int = CHUNK_OVERLAP
) -> List[str]:
    """把纯文本按 token 预算切成块，优先在段落/句子边界断开。"""
    text = (text or "").strip()
    if not text:
        return []
    if _count_tokens(text) <= chunk_token_size:
        return [text]

    sentences: List[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if _count_tokens(para) > chunk_token_size:
            sentences.extend(s.strip() for s in re.split(r"(?<=[。！？.!?])\s*", para) if s.strip())
        else:
            sentences.append(para)

    chunks: List[str] = []
    current = ""
    for sent in sentences:
        if not sent:
            continue
        candidate = sent if not current else current + "\n" + sent
        if _count_tokens(candidate) > chunk_token_size and current:
            chunks.append(current.strip())
            if overlap_token_size > 0:
                tail = _tail_by_tokens(current, overlap_token_size)
                current = tail + "\n" + sent
            else:
                current = sent
        else:
            current = candidate
    if current.strip():
        chunks.append(current.strip())
    return chunks


def _tail_by_tokens(text: str, token_budget: int) -> str:
    if _TIKTOKEN_AVAILABLE:
        tokens = _ENC.encode(text, disallowed_special=())
        if len(tokens) <= token_budget:
            return text
        return _ENC.decode(tokens[-token_budget:])
    return text[-token_budget * 4:]


# ╔═══════════════════════════════════════════════════════════════════════╗
# ║  第 3 步：多模态理解（Multimodal Understanding）                       ║
# ║  输入：多模态项   输出：带千问描述的多模态 Chunk（mineru 后端用）       ║
# ╚═══════════════════════════════════════════════════════════════════════╝

# ---- 3.1 表格/公式/标题的格式化辅助 ----
def normalize_caption_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def get_table_body(item: Dict) -> Any:
    if item.get("table_body") not in (None, ""):
        return item.get("table_body")
    if item.get("table_data") not in (None, ""):
        return item.get("table_data")
    return item.get("text", "")


def format_table_body(table_body: Any) -> str:
    """把表格体序列化为 markdown 文本，供模型读取。"""
    if isinstance(table_body, str):
        return table_body
    if isinstance(table_body, list):
        if not table_body:
            return ""
        if all(isinstance(row, (list, tuple)) for row in table_body):
            rows = ["| " + " | ".join(str(cell) for cell in row) + " |" for row in table_body]
            col_count = max(len(row) for row in table_body)
            rows.insert(1, "| " + " | ".join(["---"] * col_count) + " |")
            return "\n".join(rows)
        return "\n".join(str(row) for row in table_body)
    return str(table_body)


def get_equation_text_and_format(item: Dict) -> Tuple[str, str]:
    text = str(item.get("text", "") or "").strip()
    latex = str(item.get("latex", "") or "").strip()
    equation = str(item.get("equation", "") or "").strip()
    fmt = str(item.get("text_format", "") or "").strip()

    if text:
        return text, fmt
    if latex:
        return latex, fmt or "latex"
    if equation:
        return equation, fmt
    return "", fmt


# ---- 3.2 多模态项 -> 描述 Chunk ----
def _process_multimodal_item(item: Dict, qwen: QwenClient) -> Optional["Chunk"]:
    """把单个多模态项转成一个描述 Chunk（图片优先交千问 VL）。"""
    item_type = item.get("type", "")
    page_idx = item.get("page_idx", 0)
    section_path = item.get("_section_path", "")

    if item_type in ("image", "chart"):
        img_path = item.get("img_path", "")
        captions = normalize_caption_list(item.get("image_caption", item.get("chart_caption")))
        neighbor = item.get("_neighbor_text", "")
        description = ""
        if ENABLE_IMAGE_DESCRIPTION and img_path and Path(img_path).exists():
            description = qwen.describe_image(img_path, section_path, captions, neighbor)

        body_parts = []
        if captions:
            body_parts.append("标题：" + "，".join(captions))
        if description:
            body_parts.append("描述：" + description)
        elif not description and img_path:
            body_parts.append(f"（图片未描述，路径：{img_path}）")
        content = "\n".join(body_parts) if body_parts else item.get("content", "")
        if not content.strip():
            return None
        return Chunk(
            chunk_id=_chunk_id(content),
            type=item_type,
            content=content,
            page_idx=page_idx,
            section_path=section_path,
            metadata={"img_path": img_path, "captions": captions},
        )

    if item_type == "table":
        # 优先：MinerU 已把表格渲染成图片 -> 交千问 VL 描述
        table_body = format_table_body(get_table_body(item))
        caption = ", ".join(normalize_caption_list(item.get("table_caption")))
        table_img = item.get("img_path", "")
        description = ""
        if ENABLE_TABLE_SUMMARY and table_img and Path(table_img).exists():
            description = qwen.describe_table_image(table_img, table_body, caption, section_path)
        if not description and ENABLE_TABLE_SUMMARY and table_body:
            description = qwen.summarize_table(table_body, caption)  # 无渲染图 -> 文本模型
        parts = []
        if caption:
            parts.append("标题：" + caption)
        parts.append("结构：\n" + table_body)
        if description:
            parts.append("描述：" + description)
        content = "\n".join(parts)
        return Chunk(
            chunk_id=_chunk_id(content),
            type="table",
            content=content,
            page_idx=page_idx,
            section_path=section_path,
            metadata={"table_body": table_body, "caption": caption, "img_path": table_img},
        )

    if item_type == "equation":
        # 优先：MinerU 已把公式渲染成图片 -> 交千问 VL 描述
        eq_text, eq_fmt = get_equation_text_and_format(item)
        eq_img = item.get("img_path", "")
        explanation = ""
        if ENABLE_EQUATION_SUMMARY and eq_img and Path(eq_img).exists():
            explanation = qwen.describe_equation_image(eq_img, eq_text, eq_fmt, section_path)
        if not explanation and ENABLE_EQUATION_SUMMARY and eq_text:
            explanation = qwen.explain_equation(eq_text, eq_fmt)  # 无渲染图 -> 文本模型
        parts = [f"公式：{eq_text}"]
        if eq_fmt:
            parts.append(f"格式：{eq_fmt}")
        if explanation:
            parts.append("解释：" + explanation)
        content = "\n".join(parts)
        return Chunk(
            chunk_id=_chunk_id(content),
            type="equation",
            content=content,
            page_idx=page_idx,
            section_path=section_path,
            metadata={"equation": eq_text, "format": eq_fmt, "img_path": eq_img},
        )

    # code / 其他：直接保留原始内容
    content = item.get("content", "") or item.get("code_body", "") or str(item.get("text", ""))
    if not content.strip():
        return None
    return Chunk(
        chunk_id=_chunk_id(content),
        type=item_type,
        content=content,
        page_idx=page_idx,
        section_path=section_path,
        metadata={k: v for k, v in item.items() if not k.startswith("_")},
    )


# ╔═══════════════════════════════════════════════════════════════════════╗
# ║  第 4 步：输出                                                         ║
# ║  Chunk 数据结构 + 主流程 process_file（双后端分派）                     ║
# ╚═══════════════════════════════════════════════════════════════════════╝

@dataclass
class Chunk:
    chunk_id: str
    type: str  # text / image / table / equation / chart / code
    content: str  # 文本内容 或 多模态描述
    page_idx: int = 0
    section_path: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "type": self.type,
            "content": self.content,
            "page_idx": self.page_idx,
            "section_path": self.section_path,
            "metadata": self.metadata,
        }


def _chunk_id(content: str, prefix: str = "chunk-") -> str:
    return prefix + hashlib.md5(content.encode("utf-8")).hexdigest()


def process_file(
    file_path: str,
    output_dir: Optional[str] = None,
    chunk_token_size: int = CHUNK_TOKEN_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
) -> List[Dict]:
    """解析并分块一个文件，返回 Chunk dict 列表（可直接 JSON 序列化）。

    按 .env 的 PARSER_BACKEND 选择后端：
      - qwen-vl（默认）：文件 -> 渲染图片 -> 千问 VL 解析分块（师兄路线，需 pymupdf + API key）
      - mineru：文件 -> MinerU 结构化抽取 -> 分块 -> 千问描述多模态（需安装 MinerU）
      - direct：Excel/PPT/docx/txt/md -> 结构化 markdown -> token 分块（纯本地，无需额外依赖）
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在：{file_path}")
    ext = file_path.suffix.lower()
    if ext not in SUPPORTED_FORMATS:
        raise NotImplementedError(f"不支持的文件类型：{ext}")

    out_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    qwen = QwenClient()

    if PARSER_BACKEND == "mineru":
        return _process_file_mineru(file_path, out_dir, qwen, chunk_token_size, chunk_overlap)
    if PARSER_BACKEND == "direct":
        return _process_file_direct(file_path, out_dir, chunk_token_size, chunk_overlap)
    return _process_file_qwenvl(file_path, out_dir, qwen, chunk_token_size, chunk_overlap)


def _process_file_qwenvl(
    file_path: Path, out_dir: Path, qwen: QwenClient, chunk_token_size: int, chunk_overlap: int
) -> List[Dict]:
    """qwen-vl 后端（默认，师兄路线）：文件 -> 图片 -> 千问 VL 解析分块。"""
    ext = file_path.suffix.lower()
    source = str(file_path)

    # ---- 第 1 步：数据加载（文件 -> 图片 / 文本） ----
    logger.info("【第 1 步】数据加载（qwen-vl 后端）：{}", file_path.name)
    if ext in TEXT_FORMATS:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        logger.info("【第 1 步】完成：纯文本 {} 字符（无需渲染）", len(text))
        # ---- 第 2 步：分块（文本直接 token 切块） ----
        logger.info("【第 2 步】分块：文本按 token 预算切块")
        chunks = [
            Chunk(
                chunk_id=_chunk_id(part),
                type="text",
                content=part,
                page_idx=0,
                metadata={"source": source},
            )
            for part in split_text_into_chunks(text, chunk_token_size, chunk_overlap)
        ]
    elif ext in IMAGE_FORMATS:
        # 纯图片：第 1 步直接就是图；第 2/3 步合一 —— 千问 VL 描述
        logger.info("【第 1 步】完成：单张图片，直接交给千问 VL 描述")
        description = qwen.describe_image(str(file_path), "", [], "") if ENABLE_IMAGE_DESCRIPTION else ""
        content = ("描述：" + description) if description else f"（图片，路径：{file_path}）"
        chunks = [
            Chunk(
                chunk_id=_chunk_id(content),
                type="image",
                content=content,
                page_idx=0,
                metadata={"img_path": source, "captions": []},
            )
        ]
    else:
        # PDF / Office：逐页渲染 -> 千问 VL 逐页解析 -> section -> token 分块
        images = render_to_images(file_path, out_dir, RENDER_DPI)
        logger.info("【第 1 步】完成：渲染出 {} 页图片", len(images))

        # ---- 第 2 步：分块（每页图片 -> 千问 VL 结构化 blocks -> section -> token 切块） ----
        logger.info("【第 2 步】分块：{} 页图片交千问 VL 解析", len(images))
        page_records: List[Dict] = []
        for i, img in enumerate(images):
            record = parse_page_image(qwen, img, i + 1)
            record["page"] = i + 1
            page_records.append(record)
            status = "跳过" if record.get("skip") else "完成"
            logger.info("  第 {}/{} 页 {}", i + 1, len(images), status)

        sections = assemble_sections(page_records)
        chunks = sections_to_chunks(sections, source, chunk_token_size, chunk_overlap)

    # ---- 第 3 步：多模态理解（qwen-vl 后端已在第 2 步顺带完成表格/图片的识别） ----
    # 纯文本/页面型无需额外描述；纯图片已在上面描述过。

    # ---- 第 4 步：输出 ----
    n_text = sum(1 for c in chunks if c.type == "text")
    n_modal = sum(1 for c in chunks if c.type != "text")
    logger.info("【第 4 步】输出：共 {} 个 Chunk（文本 {} / 多模态 {}）", len(chunks), n_text, n_modal)
    return [c.to_dict() for c in chunks]


def _process_file_mineru(
    file_path: Path, out_dir: Path, qwen: QwenClient, chunk_token_size: int, chunk_overlap: int
) -> List[Dict]:
    """mineru 后端：文件 -> MinerU 结构化抽取 -> 分块 -> 千问描述多模态。"""
    source = str(file_path)
    parser = MinerUParser()

    # ---- 第 1 步：数据加载（文件 -> content_list） ----
    logger.info("【第 1 步】数据加载（mineru 后端）：{}", file_path.name)
    content_list = parser.parse_document(file_path, out_dir)
    logger.info("【第 1 步】完成，得到 {} 个内容块", len(content_list))

    # ---- 第 2 步：分块（content_list -> 纯文本 + 多模态项 -> token 切块） ----
    logger.info("【第 2 步】分块：拆分纯文本与多模态块")
    text_content, multimodal_items = separate_content(content_list)

    chunks: List[Chunk] = []
    for text_chunk in split_text_into_chunks(text_content, chunk_token_size, chunk_overlap):
        chunks.append(
            Chunk(
                chunk_id=_chunk_id(text_chunk),
                type="text",
                content=text_chunk,
                page_idx=0,
                metadata={"source": source},
            )
        )

    # ---- 第 3 步：多模态理解（图片/表格/公式 -> 千问 VL 描述） ----
    logger.info("【第 3 步】多模态理解：{} 个多模态项交千问", len(multimodal_items))
    for item in multimodal_items:
        chunk = _process_multimodal_item(item, qwen)
        if chunk is not None:
            chunk.metadata["source"] = source
            chunks.append(chunk)

    # 独立图片兜底：若整张图没被 MinerU 识别成 image 块，也补一次千问 VL 描述
    if file_path.suffix.lower() in IMAGE_FORMATS and not any(c.type in ("image", "chart") for c in chunks):
        description = qwen.describe_image(str(file_path), "", [], "")
        if description:
            chunks.append(
                Chunk(
                    chunk_id=_chunk_id(description),
                    type="image",
                    content="描述：" + description,
                    page_idx=0,
                    metadata={"img_path": source, "captions": []},
                )
            )

    # ---- 第 4 步：输出 ----
    n_text = sum(1 for c in chunks if c.type == "text")
    n_modal = sum(1 for c in chunks if c.type != "text")
    logger.info("【第 4 步】输出：共 {} 个 Chunk（文本 {} / 多模态 {}）", len(chunks), n_text, n_modal)
    return [c.to_dict() for c in chunks]


def _process_file_direct(
    file_path: Path, out_dir: Path, chunk_token_size: int, chunk_overlap: int
) -> List[Dict]:
    """direct 后端：Excel/PPT/docx/txt/md -> 结构化 markdown -> token 分块。

    纯本地，不需要 LibreOffice / pymupdf / API key。PDF 和纯图片交给
    qwen-vl / mineru 后端（这里给出明确提示，避免静默失败）。
    """
    ext = file_path.suffix.lower()
    source = str(file_path)
    stem = file_path.stem
    images_dir = out_dir / f"{stem}_images"
    images_dir.mkdir(parents=True, exist_ok=True)

    logger.info("【第 1 步】数据加载（direct 后端，纯本地结构化）：{}", file_path.name)

    if ext in EXCEL_FORMATS or ext == ".xls":
        markdown = load_excel_to_markdown(file_path)
    elif ext in PPT_FORMATS:
        markdown = load_ppt_to_markdown(file_path, images_dir)
    elif ext in WORD_FORMATS:
        markdown = load_docx_to_markdown(file_path, images_dir)
    elif ext in TEXT_FORMATS:
        markdown = file_path.read_text(encoding="utf-8", errors="ignore")
    elif ext in PDF_FORMATS:
        markdown = extract_pdf_text(file_path)
        if not markdown.strip():
            raise RuntimeError(
                "PDF 没有可提取的文字层（可能是扫描版）。请改用 qwen-vl 后端做 OCR，"
                "或安装 pymupdf 后重试（pip install pymupdf）。"
            )
    elif ext in IMAGE_FORMATS:
        raise RuntimeError(
            "direct 后端不解析纯图片内容。请改用 qwen-vl 后端（配置 OPENAI_API_KEY 用千问 VL 描述）。"
        )
    elif ext in (".doc", ".ppt"):
        raise RuntimeError(
            f"{ext} 是老格式，direct 后端不支持。请用 Word/WPS 转存为 .docx/.pptx，"
            "或安装 LibreOffice 后改用 qwen-vl 后端。"
        )
    else:
        raise NotImplementedError(f"不支持的文件类型：{ext}")

    if not markdown.strip():
        logger.warning("【第 1 步】完成：未提取到任何内容")
        return []

    logger.info("【第 1 步】完成：结构化 markdown {} 字符", len(markdown))

    # ---- 第 2 步：分块（markdown 按 token 预算切块） ----
    logger.info("【第 2 步】分块：markdown 按 token 预算切块")
    chunks = [
        Chunk(
            chunk_id=_chunk_id(part),
            type="text",
            content=part,
            page_idx=0,
            metadata={"source": source},
        )
        for part in split_text_into_chunks(markdown, chunk_token_size, chunk_overlap)
    ]
    logger.info("【输出】共 {} 个 Chunk", len(chunks))
    return [c.to_dict() for c in chunks]


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="多模态解析与分块")
    ap.add_argument("input", help="输入文件或目录")
    ap.add_argument("--output-dir", default=None, help="中间产物目录")
    ap.add_argument("--json", default=None, help="结果写入该 JSON 文件")
    args = ap.parse_args()

    input_path = Path(args.input)
    files = sorted(input_path.rglob("*")) if input_path.is_dir() else [input_path]
    all_chunks: List[Dict] = []
    for f in files:
        if f.is_file() and f.suffix.lower() in SUPPORTED_FORMATS:
            logger.info("处理：{}", f)
            all_chunks.extend(process_file(str(f), args.output_dir))

    print(json.dumps(all_chunks, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(all_chunks, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("结果已写入 {}", args.json)
