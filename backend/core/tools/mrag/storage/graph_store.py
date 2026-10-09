"""
==========================================================================
知识图谱存储 + 实体检索

流程：
    merged_graph.json -> networkx 有向图 -> 实体链接（语义 + BM25 混合）
    -> 命中实体 k 跳邻域子图 -> 三元组上下文。
实体向量缓存到 index_dir（entities.json + entity_vectors.npy），二次启动秒级加载。

merged_graph.json 格式：
    {
      "entities": ["实体A", "实体B", ...],
      "relations": [["实体A", "关系", "实体B"], ...]
    }

用法：
    from core.tools.mrag.storage.graph_store import GraphStore
    g = GraphStore()
    g.ensure_index()                        # 加载图 + 缓存实体向量
    triples, entities = g.retrieve("查询")   # -> (三元组列表, 实体列表)
==========================================================================
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import networkx as nx
import numpy as np

from core.tools.mrag.utils.utils import config, logger
from core.tools.mrag.storage.vector_store import char_bigrams, _BM25Okapi

class GraphStore:
    """知识图谱存储：加载 merged_graph.json，做实体链接 + 子图检索。"""

    def __init__(
        self,
        graph_path: Optional[Union[str, Path]] = None,
        index_dir: Optional[Union[str, Path]] = None,
        entity_top_k: int = 8,
        context_depth: int = 2,
        max_triples: int = 40,
        embedder: Any = None,
    ):
        """
        Args:
            graph_path: merged_graph.json 路径，缺省 config.graph_path
            index_dir: 实体向量缓存目录，缺省 config.graph_index_dir
            entity_top_k: 实体链接命中数
            context_depth: 从命中实体出发扩展的跳数
            max_triples: 最多返回的三元组数
            embedder: 嵌入器实例；缺省懒加载 get_embedder()
        """
        self.graph_path = Path(graph_path) if graph_path else config.graph_path
        self.index_dir = Path(index_dir) if index_dir else config.graph_index_dir
        self.entity_top_k = entity_top_k
        self.context_depth = context_depth
        self.max_triples = max_triples
        self._embedder = embedder
        self._graph: Optional[nx.DiGraph] = None
        self._nodes: List[str] = []
        self._node_vectors: Optional[np.ndarray] = None
        self._bm25: Optional[_BM25Okapi] = None

    def _get_embedder(self) -> Any:
        if self._embedder is None:
            from core.tools.mrag.embedding.embedding import get_embedder

            self._embedder = get_embedder()
        return self._embedder

    # ---- 图谱加载 ----
    def load_graph(self) -> nx.DiGraph:
        """加载 merged_graph.json 为 networkx 有向图（边属性 relation）。"""
        if not self.graph_path.is_file():
            raise FileNotFoundError(
                f"知识图谱不存在：{self.graph_path}（请先运行图谱构建管线，或改用向量检索 VectorStore）"
            )
        with self.graph_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        graph = nx.DiGraph()
        graph.add_nodes_from(data.get("entities", []))
        for subject, relation, target in data.get("relations", []):
            graph.add_node(subject)
            graph.add_node(target)
            graph.add_edge(subject, target, relation=relation)
        return graph

    # ---- 实体索引构建 / 缓存 ----
    def ensure_index(self, force: bool = False) -> Dict[str, Any]:
        """加载图谱 + 构建/加载实体向量缓存，返回统计信息。"""
        if not force and self._graph is not None:
            return self.stats()
        self._graph = self.load_graph()
        self._nodes = sorted(self._graph.nodes)
        if not self._nodes:
            raise RuntimeError(f"知识图谱为空：{self.graph_path}")

        nodes_file = self.index_dir / "entities.json"
        vectors_file = self.index_dir / "entity_vectors.npy"
        meta_file = self.index_dir / "meta.json"
        meta = {"embedding_model": config.embedding_model}

        cached = (
            nodes_file.is_file()
            and vectors_file.is_file()
            and meta_file.is_file()
            and json.loads(meta_file.read_text(encoding="utf-8")) == meta
        )
        if cached and not force:
            saved_nodes = json.loads(nodes_file.read_text(encoding="utf-8"))
            if saved_nodes == self._nodes:
                self._node_vectors = np.load(vectors_file)
                logger.info("GraphStore 已加载实体索引缓存：{} 个实体", len(self._nodes))
            else:
                cached = False
        if not cached:
            logger.info("GraphStore 为 {} 个实体生成向量（首次较慢，结果已缓存）", len(self._nodes))
            self._node_vectors = np.asarray(
                self._get_embedder().embed_texts(self._nodes), dtype=np.float32
            )
            self.index_dir.mkdir(parents=True, exist_ok=True)
            nodes_file.write_text(
                json.dumps(self._nodes, ensure_ascii=False), encoding="utf-8"
            )
            np.save(vectors_file, self._node_vectors)
            meta_file.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

        self._bm25 = _BM25Okapi([char_bigrams(n) for n in self._nodes])
        return self.stats()

    def stats(self) -> Dict[str, Any]:
        return {
            "engine": "graph",
            "graph_path": str(self.graph_path),
            "nodes": int(self._graph.number_of_nodes()) if self._graph is not None else 0,
            "edges": int(self._graph.number_of_edges()) if self._graph is not None else 0,
            "index_ready": self._graph is not None and self._node_vectors is not None,
        }

    # ---- 实体链接 + 子图检索 ----
    def link_entities(self, query: str) -> List[Tuple[str, float]]:
        """混合检索命中实体：[(实体名, 融合分数)]，按分数降序。"""
        query_vec = np.asarray(self._get_embedder().embed_query(query), dtype=np.float32)
        semantic = self._node_vectors @ query_vec
        bm25_raw = np.asarray(self._bm25.get_scores(char_bigrams(query)), dtype=np.float64)
        bm25_max = float(bm25_raw.max()) if bm25_raw.size else 0.0
        bm25_norm = bm25_raw / bm25_max if bm25_max > 0 else np.zeros_like(bm25_raw)
        fused = 0.7 * semantic + 0.3 * bm25_norm
        order = np.argsort(fused)[::-1][: self.entity_top_k]
        return [(self._nodes[i], float(fused[i])) for i in order]

    def retrieve(
        self, question: str
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """实体链接 + 子图检索，返回 (triples, entities)。"""
        matched = self.link_entities(question)
        if not matched:
            return [], []
        entity_scores = {name: score for name, score in matched}
        visited = {name for name, _ in matched}
        frontier = deque([(name, 1) for name, _ in matched])
        triples: Dict[Tuple[str, str, str], float] = {}

        while frontier:
            node, depth = frontier.popleft()
            if depth > self.context_depth:
                continue
            neighbors: List[Tuple[str, bool]] = [(s, True) for s in self._graph.successors(node)]
            neighbors += [(p, False) for p in self._graph.predecessors(node)]
            for neighbor, is_successor in neighbors:
                if is_successor:
                    rel = self._graph[node][neighbor]["relation"]
                    triple = (node, rel, neighbor)
                else:
                    rel = self._graph[neighbor][node]["relation"]
                    triple = (neighbor, rel, node)
                score = entity_scores.get(node, 0.0)
                triples[triple] = max(triples.get(triple, 0.0), score)
                if neighbor not in visited:
                    visited.add(neighbor)
                    entity_scores.setdefault(neighbor, score * 0.5)
                    frontier.append((neighbor, depth + 1))

        ranked = sorted(triples.items(), key=lambda kv: kv[1], reverse=True)[: self.max_triples]
        triple_dicts = [
            {"subject": s, "relation": p, "object": o, "score": round(score, 4)}
            for (s, p, o), score in ranked
        ]
        entity_dicts = [
            {"name": name, "score": round(score, 4)}
            for name, score in sorted(
                entity_scores.items(), key=lambda kv: kv[1], reverse=True
            )[: self.entity_top_k]
        ]
        return triple_dicts, entity_dicts

    @staticmethod
    def _context(triples: List[Dict[str, Any]]) -> str:
        lines = [
            f"{i}. {t['subject']} -[{t['relation']}]-> {t['object']}"
            for i, t in enumerate(triples, 1)
        ]
        return "\n".join(lines)
