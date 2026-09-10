"""
==========================================================================
通用功能函数：读取配置、日志初始化、数据库连接测试等

供 loader.py / api.py 统一引用，避免各处重复读 .env、重复初始化日志。

主要内容：
  - logger        ：统一日志（优先 loguru，控制台 + 滚动文件；无 loguru 退回标准库）
  - config        ：Config 单例，集中从 .env / 环境变量读取所有配置
  - test_db_connection()：测试数据库连接（Milvus / Chroma / MySQL / PostgreSQL，可配置切换）

用法：
    from core.tools.mrag.utils.utils import config, logger, test_db_connection
    logger.info("模型：{}", config.vl_model)
    result = test_db_connection()   # -> {"ok": bool, "type": ..., "message": ...}
==========================================================================
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

load_dotenv()  # 读取同目录 .env


# ==========================================================================
# 第 1 段：日志
# ==========================================================================
def setup_logger(name: str = "multimodal_rag", log_dir: str = "./logs", level: str = "INFO"):
    """初始化统一日志：优先 loguru（控制台 + 滚动文件），无 loguru 退回标准库。"""
    try:
        from loguru import logger as _lg

        _lg.remove()  # 清掉默认 handler，避免重复输出
        fmt = (
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan> - <level>{message}</level>"
        )
        _lg.add(sys.stderr, level=level, format=fmt)
        if log_dir:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            _lg.add(
                str(Path(log_dir) / f"{name}.log"),
                level=level,
                format=fmt,
                rotation="10 MB",
                retention="7 days",
                encoding="utf-8",
            )
        return _lg
    except ImportError:
        import logging

        class _FallbackLogger:
            """兼容 loguru 的 {} 占位符风格，落到标准库 logging。"""

            def _log(self, lvl: int, msg: str, *args, **kwargs) -> None:
                if args:
                    try:
                        msg = msg.format(*args)
                    except Exception:  # noqa: BLE001
                        msg = msg % args
                logging.getLogger(name).log(lvl, msg)

            def info(self, msg, *a, **k):  # noqa: D102
                self._log(logging.INFO, msg, *a, **k)

            def warning(self, msg, *a, **k):  # noqa: D102
                self._log(logging.WARNING, msg, *a, **k)

            def error(self, msg, *a, **k):  # noqa: D102
                self._log(logging.ERROR, msg, *a, **k)

            def debug(self, msg, *a, **k):  # noqa: D102
                self._log(logging.DEBUG, msg, *a, **k)

            def exception(self, msg, *a, **k):  # noqa: D102
                self._log(logging.ERROR, msg, *a, **k)

        logging.basicConfig(
            level=getattr(logging, level.upper(), logging.INFO),
            format="%(levelname)s %(message)s",
        )
        return _FallbackLogger()


logger = setup_logger()


# ==========================================================================
# 第 2 段：配置
# ==========================================================================
def _env_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass
class Config:
    # ---- 千问（DashScope OpenAI 兼容接口）----
    openai_api_key: str = ""
    openai_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    vl_model: str = "qwen-vl-max-latest"  # 视觉模型：图片描述
    llm_model: str = "qwen-plus"  # 文本模型：表格/公式总结

    # ---- 解析后端 ----
    parser_backend: str = "qwen-vl"  # qwen-vl（图片→千问VL，默认）/ mineru（结构化抽取）
    render_dpi: int = 150  # 页面渲染成图片的分辨率

    # ---- MinerU 解析 ----
    mineru_method: str = "auto"  # auto / ocr / txt
    mineru_lang: str = "ch"  # OCR 语言
    mineru_device: str = ""  # 如 "cuda:0"；留空用默认
    mineru_backend: str = ""  # 如 "pipeline"；留空用默认

    # ---- 分块 ----
    chunk_token_size: int = 1200
    chunk_overlap: int = 100

    # ---- 向量嵌入（BGE）----
    embedding_backend: str = "local"  # local（sentence-transformers 本地跑）/ api（OpenAI 兼容，如硅基流动）
    embedding_model: str = "BAAI/bge-m3"  # 中文可选 BAAI/bge-large-zh-v1.5 / bge-small-zh-v1.5
    embedding_api_key: str = ""  # api 后端用（硅基流动 SiliconFlow，免费注册）
    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_dim: int = 0  # 0 = 自动（local 自动检测；api 后端建议手动填 1024）
    embedding_batch_size: int = 32
    hf_endpoint: str = "https://hf-mirror.com"  # HuggingFace 国内镜像；置空则用官方源

    # ---- 多模态描述开关 ----
    enable_image_description: bool = True
    enable_table_summary: bool = True
    enable_equation_summary: bool = True

    # ---- 目录 ----
    output_dir: Path = Path("./output")
    upload_dir: Path = Path("./uploads")

    # ---- 向量 / 知识图谱存储（文件式，师兄 rag.py / graphrag.py 方案）----
    vector_index_dir: Path = Path("./output/.rag_index")
    graph_index_dir: Path = Path("./output/.graphrag_index")
    graph_path: Path = Path("./output/merged_graph.json")

    # ---- 数据库 ----
    db_type: str = "none"  # none / milvus / chroma / mysql / postgresql
    milvus_uri: str = "http://localhost:19530"
    milvus_token: str = ""
    chroma_host: str = ""  # 空 = 本地持久化模式
    chroma_port: int = 8000
    chroma_persist_dir: str = "./chroma_db"
    db_host: str = "localhost"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = ""

    is_windows: bool = field(default_factory=lambda: sys.platform.startswith("win"))

    @classmethod
    def from_env(cls) -> "Config":
        """从环境变量 / .env 构建配置。"""
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            openai_base_url=os.getenv(
                "OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ),
            vl_model=os.getenv("OPENAI_VL_MODEL_NAME", "qwen-vl-max-latest"),
            llm_model=os.getenv("OPENAI_LLM_MODEL_NAME", "qwen-plus"),
            parser_backend=os.getenv("PARSER_BACKEND", "qwen-vl").strip().lower(),
            render_dpi=_env_int("RENDER_DPI", 150),
            mineru_method=os.getenv("MINERU_METHOD", "auto"),
            mineru_lang=os.getenv("MINERU_LANG", "ch"),
            mineru_device=os.getenv("MINERU_DEVICE", ""),
            mineru_backend=os.getenv("MINERU_BACKEND", ""),
            chunk_token_size=_env_int("CHUNK_TOKEN_SIZE", 1200),
            chunk_overlap=_env_int("CHUNK_OVERLAP", 100),
            embedding_backend=os.getenv("EMBEDDING_BACKEND", "local").strip().lower(),
            embedding_model=os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3"),
            embedding_api_key=os.getenv("EMBEDDING_API_KEY", ""),
            embedding_base_url=os.getenv(
                "EMBEDDING_BASE_URL", "https://api.siliconflow.cn/v1"
            ),
            embedding_dim=_env_int("EMBEDDING_DIM", 0),
            embedding_batch_size=_env_int("EMBEDDING_BATCH_SIZE", 32),
            hf_endpoint=os.getenv("HF_ENDPOINT", "https://hf-mirror.com"),
            enable_image_description=_env_bool("ENABLE_IMAGE_DESCRIPTION", True),
            enable_table_summary=_env_bool("ENABLE_TABLE_SUMMARY", True),
            enable_equation_summary=_env_bool("ENABLE_EQUATION_SUMMARY", True),
            output_dir=Path(os.getenv("OUTPUT_DIR", "./output")),
            upload_dir=Path(os.getenv("UPLOAD_DIR", "./uploads")),
            vector_index_dir=Path(os.getenv("VECTOR_INDEX_DIR", "./output/.rag_index")),
            graph_index_dir=Path(os.getenv("GRAPH_INDEX_DIR", "./output/.graphrag_index")),
            graph_path=Path(os.getenv("GRAPH_PATH", "./output/merged_graph.json")),
            db_type=os.getenv("DB_TYPE", "none").strip().lower(),
            milvus_uri=os.getenv("MILVUS_URI", "http://localhost:19530"),
            milvus_token=os.getenv("MILVUS_TOKEN", ""),
            chroma_host=os.getenv("CHROMA_HOST", ""),
            chroma_port=_env_int("CHROMA_PORT", 8000),
            chroma_persist_dir=os.getenv("CHROMA_PERSIST_DIR", "./chroma_db"),
            db_host=os.getenv("DB_HOST", "localhost"),
            db_port=_env_int("DB_PORT", 3306),
            db_user=os.getenv("DB_USER", "root"),
            db_password=os.getenv("DB_PASSWORD", ""),
            db_name=os.getenv("DB_NAME", ""),
        )


config = Config.from_env()  # 单例，全项目共享

# 确保常用目录存在
config.output_dir.mkdir(parents=True, exist_ok=True)
config.upload_dir.mkdir(parents=True, exist_ok=True)


# ==========================================================================
# 第 3 段：数据库连接测试
# ==========================================================================
def test_db_connection(cfg: Optional[Config] = None) -> Dict[str, Any]:
    """测试数据库连接，返回结构化结果。

    Args:
        cfg: 配置对象，缺省用全局 config 单例

    Returns:
        {"ok": bool, "type": str, "message": str, "latency_ms": int, ...}
    """
    cfg = cfg or config
    db_type = (cfg.db_type or "none").strip().lower()
    start = time.perf_counter()

    def _elapsed() -> int:
        return int(round((time.perf_counter() - start) * 1000))

    if db_type == "milvus":
        return _test_milvus(cfg, _elapsed)
    if db_type == "chroma":
        return _test_chroma(cfg, _elapsed)
    if db_type in ("mysql", "postgresql", "postgres", "pgsql"):
        return _test_relational(cfg, db_type, _elapsed)
    if db_type in ("none", ""):
        return {
            "ok": False,
            "type": "none",
            "message": "未配置数据库（DB_TYPE 为空/none），跳过连接测试",
            "latency_ms": _elapsed(),
        }
    return {
        "ok": False,
        "type": db_type,
        "message": f"不支持的数据库类型：{db_type}（支持 milvus/chroma/mysql/postgresql）",
        "latency_ms": _elapsed(),
    }


def _test_milvus(cfg: Config, elapsed) -> Dict[str, Any]:
    try:
        from pymilvus import connections, utility
    except ImportError:
        return {
            "ok": False, "type": "milvus",
            "message": "未安装 pymilvus，请 pip install pymilvus",
            "latency_ms": elapsed(),
        }
    try:
        kwargs: Dict[str, Any] = {"alias": "test_milvus", "uri": cfg.milvus_uri}
        if cfg.milvus_token:
            kwargs["token"] = cfg.milvus_token
        connections.connect(**kwargs)
        version = utility.get_server_version()
        connections.disconnect("test_milvus")
        return {
            "ok": True, "type": "milvus",
            "message": f"Milvus 连接成功，版本 {version}",
            "server_version": version, "latency_ms": elapsed(),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False, "type": "milvus",
            "message": f"Milvus 连接失败：{exc}", "latency_ms": elapsed(),
        }


def _test_chroma(cfg: Config, elapsed) -> Dict[str, Any]:
    try:
        import chromadb
    except ImportError:
        return {
            "ok": False, "type": "chroma",
            "message": "未安装 chromadb，请 pip install chromadb",
            "latency_ms": elapsed(),
        }
    try:
        if cfg.chroma_host:
            client = chromadb.HttpClient(host=cfg.chroma_host, port=cfg.chroma_port)
        else:
            persist = cfg.chroma_persist_dir or "./chroma_db"
            Path(persist).mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=persist)
        heartbeat = client.heartbeat()
        return {
            "ok": True, "type": "chroma",
            "message": f"Chroma 连接成功，heartbeat={heartbeat}",
            "heartbeat": heartbeat, "latency_ms": elapsed(),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False, "type": "chroma",
            "message": f"Chroma 连接失败：{exc}", "latency_ms": elapsed(),
        }


def _test_relational(cfg: Config, db_type: str, elapsed) -> Dict[str, Any]:
    if db_type in ("postgresql", "postgres", "pgsql"):
        try:
            import psycopg2
        except ImportError:
            return {
                "ok": False, "type": "postgresql",
                "message": "未安装 psycopg2-binary，请 pip install psycopg2-binary",
                "latency_ms": elapsed(),
            }
        try:
            conn = psycopg2.connect(
                host=cfg.db_host, port=cfg.db_port, user=cfg.db_user,
                password=cfg.db_password, dbname=cfg.db_name, connect_timeout=5,
            )
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                version = cur.fetchone()[0]
            conn.close()
            return {
                "ok": True, "type": "postgresql",
                "message": f"PostgreSQL 连接成功：{version}",
                "server_version": version, "latency_ms": elapsed(),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False, "type": "postgresql",
                "message": f"PostgreSQL 连接失败：{exc}", "latency_ms": elapsed(),
            }

    # mysql
    try:
        import pymysql
    except ImportError:
        return {
            "ok": False, "type": "mysql",
            "message": "未安装 pymysql，请 pip install pymysql",
            "latency_ms": elapsed(),
        }
    try:
        conn = pymysql.connect(
            host=cfg.db_host, port=cfg.db_port, user=cfg.db_user,
            password=cfg.db_password, database=cfg.db_name, connect_timeout=5,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            version = cur.fetchone()[0]
        conn.close()
        return {
            "ok": True, "type": "mysql",
            "message": f"MySQL 连接成功，版本 {version}",
            "server_version": version, "latency_ms": elapsed(),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False, "type": "mysql",
            "message": f"MySQL 连接失败：{exc}", "latency_ms": elapsed(),
        }


if __name__ == "__main__":
    # 命令行自测：打印配置概览 + 数据库连接测试
    print("=== 配置概览 ===")
    for key in (
        "vl_model", "llm_model", "mineru_method", "mineru_lang",
        "chunk_token_size", "db_type", "output_dir",
    ):
        print(f"  {key} = {getattr(config, key)}")
    print("=== 数据库连接测试 ===")
    print(__import__("json").dumps(test_db_connection(), ensure_ascii=False, indent=2))
