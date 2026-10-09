"""
==========================================================================
向量存储 + 混合检索
  - chunks.json  每块的 {chunk_id, type, content, page_idx, section_path, metadata}
  - vectors.npy  向量矩阵 (n, dim) float32，与 chunks.json 逐行对应
  - meta.json    嵌入模型等参数，用于判断缓存是否失效

检索：语义向量（余弦） + BM25（中文字符二元组）混合。

对接已有代码（loader + embedding）：
    from core.tools.mrag.document.loader import process_file
    from core.tools.mrag.storage.vector_store import VectorStore

    store = VectorStore()                       # 索引目录默认 output/.rag_index
    chunks = process_file("demo.pdf")           # List[Dict]，见 loader.Chunk
    store.add(chunks)                           # 缺 embedding 会自动补（首次下载 BGE）
    store.persist()                             # 落盘，二次启动 store.load() 秒级加载
    hits = store.search("怎么降低运输成本？", top_k=5)  # -> 带 score 的 chunk 列表
==========================================================================
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np

from core.tools.mrag.utils.utils import config, logger

# ==========================================================================
# BM25（中文友好：字符二元组；内联实现，避免额外引入 rank_bm25 依赖）
# ==========================================================================
def char_bigrams(text: str) -> List[str]:
    """中文友好的词法切分：字符二元组（无分词依赖，离线可用，与师兄一致）。"""
    text = (text or "").lower().strip()
    if len(text) < 2:
        return [text] if text else []
    return [text[i : i + 2] for i in range(len(text) - 1)]


class _BM25Okapi:
    """标准 BM25（k1=1.5, b=0.75），算法与 rank_bm25.BM25Okapi 对齐。"""

    def __init__(self, corpus: List[List[str]]):
        self.corpus_size = len(corpus)
        self.avgdl = (
            sum(len(d) for d in corpus) / self.corpus_size if self.corpus_size else 0.0
        )
        if self.avgdl == 0.0:
            self.avgdl = 1.0
        self.doc_len = [len(d) for d in corpus]
        self.doc_freqs: List[Dict[str, int]] = []
        self.nd: Dict[str, int] = {}
        for doc in corpus:
            freqs: Dict[str, int] = {}
            for w in doc:
                freqs[w] = freqs.get(w, 0) + 1
            self.doc_freqs.append(freqs)
            for w in freqs:
                self.nd[w] = self.nd.get(w, 0) + 1

    def get_scores(self, query: List[str]) -> List[float]:
        scores = [0.0] * self.corpus_size
        for w in query:
            df = self.nd.get(w, 0)
            if df == 0:
                continue
            idf = math.log((self.corpus_size - df + 0.5) / (df + 0.5) + 1.0)
            for i in range(self.corpus_size):
                f = self.doc_freqs[i].get(w, 0)
                if f == 0:
                    continue
                denom = f + 1.5 * (1.0 - 0.75 + 0.75 * self.doc_len[i] / self.avgdl)
                scores[i] += idf * f * 2.5 / denom
        return scores


class VectorStore:
    """文件式向量库：存 chunk 元数据 + 向量，检索走语义+BM25 混合。"""

    def __init__(
        self,
        index_dir: Optional[Union[str, Path]] = None,
        bm25_weight: float = 0.3,
        embedder: Any = None,
    ):
        """
        Args:
            index_dir: 索引目录，缺省用 config.vector_index_dir（output/.rag_index）
            bm25_weight: 混合检索中 BM25 的权重（0~1，越大越偏向关键词匹配）
            embedder: 嵌入器实例；缺省懒加载 get_embedder()（BGEEmbedder）
        """
        self.index_dir = Path(index_dir) if index_dir else config.vector_index_dir
        self.bm25_weight = bm25_weight
        self._embedder = embedder
        self._chunks: List[Dict[str, Any]] = []
        self._vectors: Optional[np.ndarray] = None
        self._bm25: Optional[_BM25Okapi] = None

    # ---- 文件路径 ----
    @property
    def chunks_file(self) -> Path:
        return self.index_dir / "chunks.json"

    @property
    def vectors_file(self) -> Path:
        return self.index_dir / "vectors.npy"

    @property
    def meta_file(self) -> Path:
        return self.index_dir / "meta.json"

    def _meta(self) -> Dict[str, Any]:
        return {"embedding_model": config.embedding_model, "bm25_weight": self.bm25_weight}

    def _get_embedder(self) -> Any:
        if self._embedder is None:
            from core.tools.mrag.embedding.embedding import get_embedder

            self._embedder = get_embedder()
        return self._embedder

    # ---- 写入 ----
    def add(self, chunks: List[Dict[str, Any]], embed: bool = True) -> int:
        """写入 chunk 列表（复制一份，不改动传入对象）。

        chunk 需带 ``embedding`` 字段（embed_chunks 的产物）；若缺且 embed=True，
        自动用 get_embedder() 补上（首次会下载 BGE 模型）。

        Returns:
            本次写入的 chunk 数量。
        """
        if not chunks:
            return 0
        chunks = [dict(c) for c in chunks]

        missing = [i for i, c in enumerate(chunks) if not c.get("embedding")]
        if missing and embed:
            self._get_embedder().embed_chunks([chunks[i] for i in missing])

        vecs = [c.get("embedding") for c in chunks]
        if any(v is None for v in vecs):
            raise ValueError("chunk 缺少 embedding 字段（请先 embed_chunks，或 add(embed=True)）")

        matrix = np.asarray(vecs, dtype=np.float32)
        for c in chunks:  # 向量单独存 .npy，不重复写进 json
            c.pop("embedding", None)
            c.pop("embedding_dim", None)

        self._chunks.extend(chunks)
        self._vectors = matrix if self._vectors is None else np.vstack([self._vectors, matrix])
        self._bm25 = None  # 语料变化，BM25 需重建
        logger.info("VectorStore.add：写入 {} 块，当前共 {} 块", len(chunks), len(self._chunks))
        return len(chunks)

    def persist(self) -> None:
        """把当前内存中的 chunks + 向量落盘到 index_dir。"""
        if not self._chunks:
            logger.warning("VectorStore 为空，跳过 persist")
            return
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.chunks_file.write_text(
            json.dumps(self._chunks, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        np.save(self.vectors_file, self._vectors)
        self.meta_file.write_text(json.dumps(self._meta(), ensure_ascii=False), encoding="utf-8")
        logger.info("VectorStore 已落盘 {} 块 -> {}", len(self._chunks), self.index_dir)

    # ---- 读取 ----
    def load(self) -> bool:
        """从 index_dir 读取缓存；文件不存在或不一致返回 False。"""
        if not (self.chunks_file.is_file() and self.vectors_file.is_file()):
            return False
        self._chunks = json.loads(self.chunks_file.read_text(encoding="utf-8"))
        self._vectors = np.load(self.vectors_file)
        if len(self._chunks) != int(self._vectors.shape[0]):
            logger.error(
                "缓存不一致：chunks {} 块 vs vectors {} 行，请删除 {} 重建",
                len(self._chunks), int(self._vectors.shape[0]), self.index_dir,
            )
            return False
        if self.meta_file.is_file():
            saved = json.loads(self.meta_file.read_text(encoding="utf-8"))
            if saved.get("embedding_model") != config.embedding_model:
                logger.warning(
                    "缓存嵌入模型 {} 与当前 {} 不一致，检索结果可能不准，建议重建索引",
                    saved.get("embedding_model"), config.embedding_model,
                )
        self._bm25 = None
        logger.info("VectorStore 已加载缓存 {} 块", len(self._chunks))
        return True

    # ---- 检索 ----
    def _ensure_bm25(self) -> None:
        if self._bm25 is None:
            self._bm25 = _BM25Okapi([char_bigrams(c.get("content", "")) for c in self._chunks])

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """混合检索 top_k 片段，返回带 ``score`` 的 chunk 列表（按分数降序）。"""
        if self._vectors is None or not self._chunks:
            return []
        query_vec = np.asarray(self._get_embedder().embed_query(query), dtype=np.float32)
        semantic = self._vectors @ query_vec
        self._ensure_bm25()
        bm25_raw = np.asarray(self._bm25.get_scores(char_bigrams(query)), dtype=np.float64)
        bm25_max = float(bm25_raw.max()) if bm25_raw.size else 0.0
        bm25_norm = bm25_raw / bm25_max if bm25_max > 0 else np.zeros_like(bm25_raw)

        fused = (1 - self.bm25_weight) * semantic + self.bm25_weight * bm25_norm
        order = np.argsort(fused)[::-1][:top_k]
        out: List[Dict[str, Any]] = []
        for i in order:
            c = dict(self._chunks[i])
            c["score"] = float(fused[i])
            out.append(c)
        return out

    def stats(self) -> Dict[str, Any]:
        return {
            "engine": "vector",
            "index_dir": str(self.index_dir),
            "chunks": len(self._chunks),
            "vector_dim": int(self._vectors.shape[1]) if self._vectors is not None else 0,
            "index_ready": self._vectors is not None and len(self._chunks) > 0,
        }


if __name__ == "__main__":
    # 命令行自测（用合成向量，无需下载模型）：
    #   cd backend && python -m core.tools.mrag.storage.vector_store
    rng = np.random.default_rng(0)
    demo = [
        {"chunk_id": "c1", "type": "text", "content": "物流运输成本主要由燃油、人工、路桥费构成",
         "page_idx": 1, "metadata": {"source": "a.pdf"}},
        {"chunk_id": "c2", "type": "text", "content": "仓储管理涉及入库、出库、盘点",
         "page_idx": 2, "metadata": {"source": "a.pdf"}},
    ]
    for c in demo:
        v = rng.standard_normal(8).astype(np.float32)
        c["embedding"] = (v / np.linalg.norm(v)).tolist()

    store = VectorStore(index_dir=Path(config.output_dir) / "_demo_index")
    store.add(demo, embed=False)
    store.persist()
    print(store.stats())

    store2 = VectorStore(index_dir=Path(config.output_dir) / "_demo_index")
    print("load 成功:", store2.load(), "块数:", len(store2._chunks))
