"""
==========================================================================
检索：按 Query.mode 路由，把查询喂给向量库 / 知识图谱，产出统一 RetrievalResult

  - rag      ：向量库检索文档块（语义 + BM25 混合）
  - graphrag ：知识图谱实体链接 + 子图检索三元组
  - hybrid   ：两者都检，结果分两段拼进同一上下文

多模态改进：
  - 向量检索用 Query.standalone（多轮改写后的独立问题）
  - 图谱检索用 Query.keywords（抽取的干净实体），空则退回 standalone
==========================================================================
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from core.tools.mrag.utils.utils import config, logger
from core.tools.mrag.enums import SearchMode
from core.tools.mrag.query.query import Query

_NO_GRAPH_CONTEXT = "（未构建 merge 知识图谱，本部分为空）"


@dataclass
class RetrievalResult:
    """统一检索结果：向量片段 + 图谱三元组 + 实体 + 两段上下文 + 来源。"""

    mode: str
    docs: List[Dict[str, Any]] = field(default_factory=list)      # 命中文档块（带 score）
    triples: List[Dict[str, Any]] = field(default_factory=list)   # 命中三元组
    entities: List[Dict[str, Any]] = field(default_factory=list)  # 命中实体
    context: str = ""            # 文档片段上下文（向量检索）
    graph_context: str = ""      # 三元组上下文（图谱检索）
    sources: List[Dict[str, Any]] = field(default_factory=list)   # 统一来源（给 UI / generation 溯源）
    stats: Dict[str, Any] = field(default_factory=dict)


def _chunk_source(chunk: Dict[str, Any]) -> str:
    """chunk 来源：文本块用 metadata.source，图片/表格/公式块退回 img_path。"""
    m = chunk.get("metadata") or {}
    return m.get("source") or m.get("img_path") or "?"


def _page_label(chunk: Dict[str, Any]) -> str:
    """页码标签：单页 '5'；跨页 '8-9'；无页码 '?'。"""
    try:
        start = int(chunk.get("page_idx") or 0)
        end = int(chunk.get("page_end") or start)
    except (TypeError, ValueError):
        return "?"
    if start <= 0:
        return "?"
    return str(start) if end <= start else f"{start}-{end}"


def _doc_context(docs: List[Dict[str, Any]]) -> str:
    """命中文档块拼上下文。"""
    parts = []
    for i, d in enumerate(docs, 1):
        src = _chunk_source(d)
        page = _page_label(d)
        parts.append(f"[片段{i} | 来源: {src} 第{page}页]\n{d.get('content', '')}")
    return "\n\n".join(parts)


def _doc_sources(docs: List[Dict[str, Any]], max_len: int = 160) -> List[Dict[str, Any]]:
    """文档块来源。"""
    out = []
    for d in docs:
        excerpt = (d.get("content") or "").strip()
        if len(excerpt) > max_len:
            excerpt = excerpt[:max_len] + "…"
        out.append(
            {
                "type": "chunk",
                "score": round(d.get("score", 0.0), 4),
                "source": _chunk_source(d),
                "page": _page_label(d),
                "text": excerpt,
            }
        )
    return out


def _entity_sources(entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"type": "entity", "name": e["name"], "score": e["score"]} for e in entities]


def _triple_sources(triples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "type": "triple",
            "text": f"{t['subject']} -[{t['relation']}]-> {t['object']}",
            "score": t["score"],
        }
        for t in triples
    ]


class Retriever:
    """检索编排：懒加载向量库 / 图谱，按模式路由。"""

    def __init__(self, vector_store: Any = None, graph_store: Any = None, reranker: Any = None):
        self._vector_store = vector_store
        self._graph_store = graph_store
        self._reranker = reranker

    @property
    def vector_store(self):
        if self._vector_store is None:
            from core.tools.mrag.storage.vector_store import VectorStore

            self._vector_store = VectorStore(bm25_weight=config.bm25_weight)
            if not self._vector_store.load():
                logger.warning("向量索引未就绪：{}（请先运行 init 构建索引）", config.vector_index_dir)
        return self._vector_store

    @property
    def graph_store(self):
        if self._graph_store is None:
            from core.tools.mrag.storage.graph_store import GraphStore

            self._graph_store = GraphStore(
                entity_top_k=config.entity_top_k,
                context_depth=config.context_depth,
                max_triples=config.max_triples,
            )
        return self._graph_store

    @property
    def reranker(self):
        if self._reranker is None:
            from core.tools.mrag.rerank.rerank import Reranker

            self._reranker = Reranker()
        return self._reranker

    def retrieve(self, query: Query) -> RetrievalResult:
        """按 Query.mode 检索，返回统一结果。"""
        mode = query.mode
        docs, triples, entities = [], [], []
        context = ""
        graph_context = _NO_GRAPH_CONTEXT
        sources: List[Dict[str, Any]] = []
        stats: Dict[str, Any] = {}

        if mode in (SearchMode.RAG.value, SearchMode.HYBRID.value):
            docs = self.vector_store.search(query.standalone, top_k=config.top_k)
            if config.rerank_enabled and docs:
                docs = self.reranker.rerank(query.standalone, docs)
            context = _doc_context(docs)
            sources += _doc_sources(docs)
            stats["vector"] = self.vector_store.stats()

        if mode in (SearchMode.GRAPHRAG.value, SearchMode.HYBRID.value):
            if config.graph_path.is_file():
                graph_query = " ".join(query.keywords) if query.keywords else query.standalone
                self.graph_store.ensure_index()
                triples, entities = self.graph_store.retrieve(graph_query)
                graph_context = self.graph_store._context(triples)
                sources += _entity_sources(entities) + _triple_sources(triples)
                stats["graph"] = self.graph_store.stats()
            else:
                # 图谱未构建：hybrid 降级为仅文档检索（对齐师兄 _hybrid_retrieve 的 graph_available 判断）
                logger.warning("知识图谱不存在，{} 模式跳过图谱检索（仅向量）", mode)

        logger.info(
            "检索完成：mode={}, 文档块={}, 三元组={}, 实体={}",
            mode, len(docs), len(triples), len(entities),
        )
        return RetrievalResult(
            mode=mode,
            docs=docs,
            triples=triples,
            entities=entities,
            context=context,
            graph_context=graph_context,
            sources=sources,
            stats=stats,
        )


if __name__ == "__main__":
    # 命令行自测（假 store，不联网不建索引）：
    #   cd backend && python -m core.tools.mrag.retrieval.retrieval
    class FakeVec:
        def load(self):
            return True

        def stats(self):
            return {"engine": "vector", "chunks": 2, "index_ready": True}

        def search(self, q, top_k=5):
            return [
                {"chunk_id": "c1", "type": "text", "content": "物流运输成本主要由燃油、人工构成",
                 "page_idx": 1, "metadata": {"source": "a.pdf"}, "score": 0.9},
                {"chunk_id": "c2", "type": "image", "content": "这是一张仓储布局图",
                 "page_idx": 2, "metadata": {"img_path": "p1.png"}, "score": 0.7},
            ][:top_k]

    class FakeGraph:
        def ensure_index(self):
            return {}

        def stats(self):
            return {"engine": "graph", "nodes": 3, "index_ready": True}

        def retrieve(self, q):
            return (
                [{"subject": "R125车型", "relation": "运输成本", "object": "燃油", "score": 0.8}],
                [{"name": "R125车型", "score": 0.8}],
            )

        @staticmethod
        def _context(triples):
            return "\n".join(
                f"{i}. {t['subject']} -[{t['relation']}]-> {t['object']}"
                for i, t in enumerate(triples, 1)
            )

    class FakeReranker:
        def rerank(self, query, docs, top_k=None):
            return docs[: (top_k or len(docs))]

    r = Retriever(vector_store=FakeVec(), graph_store=FakeGraph(), reranker=FakeReranker())

    for mode in ("rag", "graphrag", "hybrid"):
        q = Query(
            question="怎么降低运输成本？",
            standalone="怎么降低运输成本？",
            keywords=["运输成本", "燃油"],
            mode=mode,
        )
        res = r.retrieve(q)
        print(f"[{mode}] docs={len(res.docs)} triples={len(res.triples)} "
              f"entities={len(res.entities)} sources={len(res.sources)}")
        print("  context      :", res.context.replace("\n", " | ")[:70])
        print("  graph_context:", res.graph_context.replace("\n", " | ")[:70])

    print("ALL_PASS")
