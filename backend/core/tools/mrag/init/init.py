"""
==========================================================================
建库 + 问答编排：把 document/embedding/storage/query/retrieval/rerank/generation
串成一条完整管线（对齐师兄 LogisticsKG 的 src/main.py + RAG/utils/service.py）：

  - build(files)      解析分块 → 向量索引 + 知识图谱（实体/关系抽取），落盘
  - ensure_index()    懒加载已有索引（对外提供便捷入口）
  - ask(question,...) 查询 → 检索 → 重排 → 生成（一次性）
  - ask_stream(...)   同上，流式（status/meta/token/done）
  - status()          报告索引/图谱就绪状态

问答入口统一从这里走，API 层只做参数转发，避免业务散落。
==========================================================================
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from core.tools.mrag.init.graph_builder import build_graph
from core.tools.mrag.query.query import build_query
from core.tools.mrag.utils.utils import config, logger


def _collect_files(files: Union[str, Path, List[Union[str, Path]]]) -> List[Path]:
    """把输入（单个文件 / 目录 / 列表）规整成支持格式的文件列表。"""
    from core.tools.mrag.document.loader import SUPPORTED_FORMATS

    if isinstance(files, (str, Path)):
        files = [Path(files)]
    else:
        files = [Path(f) for f in files]

    out: List[Path] = []
    for f in files:
        if f.is_dir():
            out.extend(
                sorted(p for p in f.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_FORMATS)
            )
        elif f.is_file() and f.suffix.lower() in SUPPORTED_FORMATS:
            out.append(f)
        else:
            logger.warning("跳过不存在的路径或非支持格式：{}", f)
    return out


class RAGPipeline:
    """建库 + 问答编排：懒加载 Retriever / Generator，支持注入假件自测。"""

    def __init__(self, retriever: Any = None, generator: Any = None):
        self._retriever = retriever
        self._generator = generator

    @property
    def retriever(self):
        if self._retriever is None:
            from core.tools.mrag.retrieval.retrieval import Retriever

            self._retriever = Retriever()
        return self._retriever

    @property
    def generator(self):
        if self._generator is None:
            from core.tools.mrag.generation.generation import Generator

            self._generator = Generator()
        return self._generator

    # ---------- 建库 ----------
    def build(self, files: Union[str, Path, List[Union[str, Path]]], rebuild: bool = False) -> Dict[str, Any]:
        """解析文件 → 向量索引 + 知识图谱，返回统计。

        Args:
            files: 单个文件 / 目录 / 列表
            rebuild: True 则先清空旧向量索引与图谱再重建
        """
        import shutil

        from core.tools.mrag.document.loader import process_file
        from core.tools.mrag.storage.vector_store import VectorStore

        file_list = _collect_files(files)
        if not file_list:
            raise RuntimeError("没有找到可解析的文件（支持格式见 /formats）")

        if rebuild:
            shutil.rmtree(config.vector_index_dir, ignore_errors=True)
            if config.graph_path.exists():
                config.graph_path.unlink()
            logger.info("已清理旧向量索引 / 图谱，开始重建")

        all_chunks: List[Dict[str, Any]] = []
        for f in file_list:
            chunks = process_file(str(f))
            all_chunks.extend(chunks)
            logger.info("已解析 {}：{} 块", f.name, len(chunks))

        if not all_chunks:
            raise RuntimeError("解析结果为空，未生成任何 chunk")

        store = VectorStore(bm25_weight=config.bm25_weight)
        if rebuild:
            store.add(all_chunks, embed=True)
        else:
            # 增量：先载入旧索引，按 chunk_id 去重后只追加新块，避免重复
            store.load()
            existing_ids = store.existing_chunk_ids()
            new_chunks = [c for c in all_chunks if c.get("chunk_id") not in existing_ids]
            if new_chunks:
                store.add(new_chunks, embed=True)
            else:
                logger.info("本次无新增 chunk（{} 块均已存在）", len(all_chunks))
        store.persist()

        stats: Dict[str, Any] = {"vector": store.stats()}
        if config.graph_build_enabled:
            stats["graph"] = build_graph(all_chunks, merge_existing=not rebuild)
        return stats

    # ---------- 状态 ----------
    def status(self) -> Dict[str, Any]:
        return {
            "vector_index_ready": (config.vector_index_dir / "chunks.json").is_file(),
            "graph_available": config.graph_path.is_file(),
            "graph_path": str(config.graph_path),
            "vector_index_dir": str(config.vector_index_dir),
        }

    # ---------- 问答 ----------
    def ask(
        self,
        question: str,
        history: Optional[List[Tuple[str, str]]] = None,
        mode: str = "auto",
    ) -> Dict[str, Any]:
        """一次性问答：改写 → 检索 → 重排 → 生成。"""
        query = build_query(question, history, mode, graph_available=config.graph_path.is_file())
        result = self.retriever.retrieve(query)
        answer = self.generator.generate(query, result, history)
        return {"mode": result.mode, "answer": answer, "sources": result.sources, "stats": result.stats}

    def ask_stream(
        self,
        question: str,
        history: Optional[List[Tuple[str, str]]] = None,
        mode: str = "auto",
    ) -> Iterator[Dict[str, Any]]:
        """流式问答：先检索，再逐 token 产出（事件：status / meta / token / done）。"""
        yield {"type": "status", "message": "正在检索..."}
        query = build_query(question, history, mode, graph_available=config.graph_path.is_file())
        result = self.retriever.retrieve(query)
        yield {"type": "meta", "mode": result.mode, "sources": result.sources, "stats": result.stats}
        for token in self.generator.stream(query, result, history):
            yield {"type": "token", "text": token}
        yield {"type": "done"}


if __name__ == "__main__":
    # 命令行自测（假 retriever / generator，不联网不建索引）：
    #   cd backend && python -m core.tools.mrag.init.init
    from dataclasses import dataclass

    @dataclass
    class FakeQuery:
        standalone: str = "怎么降低运输成本？"
        question: str = "怎么降低运输成本？"
        keywords: Optional[list] = None
        mode: str = "rag"

    @dataclass
    class FakeResult:
        mode: str = "rag"
        sources: Optional[list] = None
        stats: Optional[dict] = None

    class FakeRetriever:
        def retrieve(self, query):
            return FakeResult(
                mode=query.mode,
                sources=[{"type": "chunk", "score": 0.9}],
                stats={"engine": "vector"},
            )

    class FakeGenerator:
        def generate(self, query, result, history=None):
            return "答案：降低燃油成本"

        def stream(self, query, result, history=None):
            yield "答案："
            yield "降低燃油成本"

    # monkeypatch build_query 以便不联网、注入 fake query
    import core.tools.mrag.init.init as m

    def fake_build_query(question, history=None, mode="auto", graph_available=None):
        return FakeQuery(mode=mode if mode != "auto" else "rag")

    m.build_query = fake_build_query

    p = RAGPipeline(retriever=FakeRetriever(), generator=FakeGenerator())

    # status
    s = p.status()
    assert "vector_index_ready" in s and "graph_available" in s, "status 字段不全"
    print("status:", s)

    # ask
    ans = p.ask("怎么降低运输成本？")
    assert ans["answer"] == "答案：降低燃油成本", "ask 答案不对"
    assert ans["mode"] == "rag", "ask mode 不对"
    print("ask:", ans)

    # ask_stream
    events = list(p.ask_stream("怎么降低运输成本？"))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "status" and kinds[-1] == "done", "ask_stream 首尾事件不对"
    assert "meta" in kinds and "token" in kinds, "ask_stream 缺少 meta/token"
    assert "".join(e["text"] for e in events if e["type"] == "token") == "答案：降低燃油成本"
    print("ask_stream types:", kinds)

    # _collect_files：目录输入不报错即可（可能为空）
    n = len(_collect_files([Path(config.output_dir)]))
    print("collect_files:", n, "个")
    print("ALL_PASS")
