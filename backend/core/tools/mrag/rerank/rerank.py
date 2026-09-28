"""
==========================================================================
检索后重排：cross-encoder 对 (query, chunk) 逐对打分，重排文档块

用法：
    from core.tools.mrag.rerank.rerank import Reranker
    r = Reranker()
    docs = r.rerank(query, docs, top_k=4)   # 重排后 docs（score 已是重排分）
==========================================================================
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.tools.mrag.utils.utils import config, logger, torch_dtype_kwargs


class Reranker:
    """Cross-encoder 重排器：懒加载模型，rerank(query, docs) 返回重排后的 docs。"""

    def __init__(self, model_name: Optional[str] = None):
        self.model_name = model_name or config.rerank_model
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            logger.info("加载重排模型（首次较慢）：{}", self.model_name)
            self._model = CrossEncoder(
                self.model_name,
                max_length=1024,
                device=config.embedding_device,
                model_kwargs=torch_dtype_kwargs(config.embedding_dtype),
            )
        return self._model

    def rerank(
        self, query: str, docs: List[Dict[str, Any]], top_k: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """对文档块做 cross-encoder 重排，返回按新分降序的 docs（截断到 top_k）。

        每块保留原融合分 fusion_score，用重排分覆盖 score，下游 context/sources 直接用新顺序。
        """
        if not docs:
            return []
        top_k = top_k or config.rerank_top_k
        contents = [d.get("content", "") for d in docs]
        scores = self.model.predict([[query, c] for c in contents])
        ranked: List[Dict[str, Any]] = []
        for d, s in zip(docs, scores):
            nd = dict(d)
            nd["fusion_score"] = nd.get("score")   # 保留原融合分
            nd["score"] = round(float(s), 4)       # 重排分覆盖 score
            ranked.append(nd)
        ranked.sort(key=lambda d: d["score"], reverse=True)
        return ranked[:top_k]


if __name__ == "__main__":
    # 命令行自测（假模型，不下载重排模型）：
    #   cd backend && python -m core.tools.mrag.rerank.rerank
    class FakeModel:
        def predict(self, pairs):
            # 模拟：内容越短分数越高（仅验证排序/截断/保留 fusion_score 逻辑）
            return [1.0 / (len(q) + len(c) + 1) * 100 for q, c in pairs]

    r = Reranker(model_name="fake-reranker")
    r._model = FakeModel()  # 注入假模型，跳过下载

    docs = [
        {"chunk_id": "c1", "content": "物流运输成本主要由燃油构成", "score": 0.9},
        {"chunk_id": "c2", "content": "仓储", "score": 0.5},
        {"chunk_id": "c3", "content": "入库出库盘点管理流程", "score": 0.8},
    ]
    out = r.rerank("运输成本", docs, top_k=2)
    for d in out:
        print(f"{d['chunk_id']}  score={d['score']}  fusion={d.get('fusion_score')}")
    assert len(out) == 2, "截断数量不对"
    assert out[0]["chunk_id"] == "c2", "重排顺序不对（应最短内容分数最高）"
    assert all("fusion_score" in d for d in out), "应保留原融合分"
    print("ALL_PASS")
