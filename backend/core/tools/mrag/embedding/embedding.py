"""
==========================================================================
BGE 向量嵌入模块
用法：
    from core.tools.mrag.embedding.embedding import get_embedder
    emb = get_embedder()
    vecs = emb.embed_texts(["物流成本", "仓储管理"])        # 文档（passage）
    q = emb.embed_query("怎么降低运输成本？")               # 查询（自动加前缀）
    chunks = emb.embed_chunks(chunks)                       # 给 loader 的 chunk 加 embedding 字段
==========================================================================
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from core.tools.mrag.utils.utils import config, logger

# BGE query 前缀（按模型家族区分；passage 一律不加前缀）
_ZH_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
_EN_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def _query_prefix(model: str) -> str:
    """根据模型名返回 query 前缀。"""
    m = (model or "").lower()
    if "zh" in m:
        return _ZH_QUERY_PREFIX
    # bge-m3 等：dense 检索不加前缀（官方建议）
    return ""


class BGEEmbedder:
    """BGE 嵌入器，封装 local（sentence-transformers）与 api（OpenAI 兼容）两种后端。"""

    def __init__(
        self,
        backend: Optional[str] = None,
        model: Optional[str] = None,
        batch_size: Optional[int] = None,
    ) -> None:
        self.backend = (backend or config.embedding_backend).strip().lower()
        self.model_name = model or config.embedding_model
        self.batch_size = batch_size or config.embedding_batch_size
        self._prefix = _query_prefix(self.model_name)
        self._local_model = None
        self._client = None
        self.dim = 0

        if self.backend == "local":
            self._init_local()
        elif self.backend == "api":
            self._init_api()
        else:
            raise ValueError(f"不支持的 EMBEDDING_BACKEND：{self.backend}（支持 local / api）")

    # ---- 初始化 ----
    def _init_local(self) -> None:
        # 国内下载模型慢，默认走 hf-mirror.com 镜像；.env 里 HF_ENDPOINT 置空则用官方源。
        # 必须在加载模型之前设置，huggingface_hub 才会读到。
        if config.hf_endpoint:
            os.environ.setdefault("HF_ENDPOINT", config.hf_endpoint)
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise RuntimeError(
                "本地 BGE 需要 sentence-transformers，请：pip install sentence-transformers"
            )
        logger.info("加载本地 BGE 模型：{}（首次会联网下载，耐心等）", self.model_name)
        self._local_model = SentenceTransformer(self.model_name)
        self.dim = self._local_model.get_sentence_embedding_dimension()
        logger.info("BGE 模型加载完成，向量维度：{}", self.dim)

    def _init_api(self) -> None:
        api_key = config.embedding_api_key
        if not api_key:
            raise RuntimeError(
                "EMBEDDING_BACKEND=api 但未配置 EMBEDDING_API_KEY（硅基流动 SiliconFlow 免费注册）"
            )
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=config.embedding_base_url)
        # api 后端拿不到维度，用 .env 的 EMBEDDING_DIM，缺省按 bge-m3 的 1024
        self.dim = config.embedding_dim or 1024
        logger.info("使用 API 后端嵌入模型：{}（维度 {}）", self.model_name, self.dim)

    # ---- 核心：批量编码 ----
    def embed_texts(self, texts: List[str], is_query: bool = False) -> List[List[float]]:
        """批量编码文本。文档/chunk 传 is_query=False，查询传 True。"""
        texts = [str(t) for t in texts]
        if not texts:
            return []
        if is_query and self._prefix:
            texts = [self._prefix + t for t in texts]

        if self.backend == "local":
            vecs = self._local_model.encode(
                texts,
                batch_size=self.batch_size,
                normalize_embeddings=True,  # L2 归一化，点积即余弦相似度
                show_progress_bar=False,
            )
            return [v.tolist() for v in vecs]

        # api 后端：分批请求
        out: List[List[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            resp = self._client.embeddings.create(model=self.model_name, input=batch)
            for d in sorted(resp.data, key=lambda x: x.index):
                out.append(d.embedding)
        return out

    def embed_query(self, query: str) -> List[float]:
        """编码单条查询（自动加 query 前缀）。"""
        vecs = self.embed_texts([query], is_query=True)
        return vecs[0] if vecs else []

    def embed_chunks(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """给 loader 输出的 chunk 列表加上 embedding 字段（对 content 文本编码）。"""
        if not chunks:
            return chunks
        texts = [c.get("content", "") or "" for c in chunks]
        vecs = self.embed_texts(texts, is_query=False)
        for c, v in zip(chunks, vecs):
            c["embedding"] = v
            c["embedding_dim"] = len(v)
        logger.info("已嵌入 {} 个 chunk", len(chunks))
        return chunks

    # ---- 相似度与极简检索（无向量库时调试用）----
    @staticmethod
    def cosine(a: List[float], b: List[float]) -> float:
        if not a or not b:
            return 0.0
        # 向量已 L2 归一化，点积即余弦相似度
        return sum(x * y for x, y in zip(a, b))

    def search(self, query: str, chunks: List[Dict], top_k: int = 5) -> List[Dict]:
        """在已嵌入的 chunk 列表里做最简暴力检索（没接向量库时先这样测）。"""
        qv = self.embed_query(query)
        scored = []
        for c in chunks:
            if "embedding" in c:
                scored.append((self.cosine(qv, c["embedding"]), c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:top_k]]


# 全局单例（懒加载：首次 get_embedder() 才下载模型 / 建连）
_EMBEDDER: Optional[BGEEmbedder] = None


def get_embedder(**kwargs) -> BGEEmbedder:
    global _EMBEDDER
    if _EMBEDDER is None:
        _EMBEDDER = BGEEmbedder(**kwargs)
    return _EMBEDDER


if __name__ == "__main__":
    # 命令行自测：python embedding.py
    emb = get_embedder()
    print("后端：", emb.backend, " 模型：", emb.model_name, " 维度：", emb.dim)
    docs = ["物流运输成本主要由燃油、人工、路桥费构成", "仓储管理涉及入库、出库、盘点"]
    vecs = emb.embed_texts(docs)
    q = emb.embed_query("运输成本包含哪些？")
    for i, doc in enumerate(docs):
        print(f"  相似度 {emb.cosine(q, vecs[i]):.4f}  <- {doc}")
