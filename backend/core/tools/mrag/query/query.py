"""
==========================================================================
Query 构建（检索前处理）
  - standalone：多轮改写后的独立问题
  - keywords  ：抽取的实体/关键词
  - mode      ：auto / rag / graphrag / hybrid

设计：
    向量检索要完整句子(standalone)，图谱检索要干净实体(keywords)。
  - 多轮改写 + 实体抽取用 qwen，temperature=0 保证稳定。

用法：
    from core.tools.mrag.query.query import build_query
    q = build_query("那它的成本怎么降？", history=[("R125 车型是什么", "…")])
    q.standalone   # 改写后的完整问题
    q.keywords     # ["R125 车型", "运输成本"]
    q.mode         # auto / rag / graphrag / hybrid
==========================================================================
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from core.tools.mrag.utils.utils import config, logger
from core.tools.mrag.utils.llm import chat

MODE_AUTO = "auto"
MODE_RAG = "rag"            # 文档向量检索
MODE_GRAPHRAG = "graphrag"  # 知识图谱检索
MODE_HYBRID = "hybrid"      # 文档向量 + 知识图谱融合

_REWRITE_PROMPT = (
    "你是检索查询改写助手。请结合【对话历史】，把用户最后一句追问改写为一句完整、独立、"
    "不含指代（它/这个/那个/这/那等）的问题，用于后续检索。只输出改写后的问题本身，"
    "不要任何解释、引号或多余文字。"
)

_KEYWORD_PROMPT = (
    "你是实体/关键词抽取助手。请从下面的问题中抽取最适合做知识图谱实体链接和关键词检索的词"
    "（实体名、专有名词、核心名词短语），每行输出一个，最多 8 个。"
    "只输出关键词，不要编号、标点、解释或多余文字。"
)


@dataclass
class Query:
    """结构化查询：向量检索用 standalone，图谱检索用 keywords。"""

    question: str                                         # 原始问题
    standalone: str                                       # 改写后的独立问题
    keywords: List[str] = field(default_factory=list)     # 抽取的实体/关键词
    mode: str = MODE_AUTO                                 # 路由结果


def _graph_available() -> bool:
    return config.graph_path.is_file()


def resolve_mode(mode: str, graph_available: bool) -> str:
    """路由：auto → 有图谱走 graphrag，否则 rag；rag / graphrag / hybrid 显式指定。"""
    mode = (mode or MODE_AUTO).strip().lower()
    if mode == MODE_AUTO:
        return MODE_GRAPHRAG if graph_available else MODE_RAG
    if mode in (MODE_RAG, MODE_GRAPHRAG, MODE_HYBRID):
        if mode == MODE_GRAPHRAG and not graph_available:
            raise RuntimeError("知识图谱不存在，无法使用 graphrag 模式，请改用 rag 或 hybrid")
        return mode
    raise ValueError(f"未知模式: {mode}（可选 auto / rag / graphrag / hybrid）")


def _rewrite(question: str, history: List[Tuple[str, str]]) -> str:
    """多轮改写：把带指代的追问改写成独立问题（最多带最近 6 轮历史）。"""
    messages = [{"role": "system", "content": _REWRITE_PROMPT}]
    for q, a in history[-6:]:
        messages.append({"role": "user", "content": q})
        messages.append({"role": "assistant", "content": (a or "")[:500]})
    messages.append({"role": "user", "content": question})
    out = chat(messages, temperature=0.0).strip()
    return out or question  # 改写失败时退回原问题，不阻塞


def _extract_keywords(question: str) -> List[str]:
    """抽取实体/关键词（每行一个），清洗编号与标点，最多 8 个。"""
    messages = [
        {"role": "system", "content": _KEYWORD_PROMPT},
        {"role": "user", "content": question},
    ]
    out = chat(messages, temperature=0.0).strip()
    if not out:
        return []
    keywords: List[str] = []
    for line in out.splitlines():
        line = line.strip()
        # 去掉行首的编号 / 列表符号，如 "1." "-" "、" "·" "•"
        line = line.lstrip("0123456789.、-—•·* ").strip()
        if line and line not in keywords:
            keywords.append(line)
    return keywords[:8]


def build_query(
    question: str,
    history: Optional[List[Tuple[str, str]]] = None,
    mode: str = MODE_AUTO,
    graph_available: Optional[bool] = None,
) -> Query:
    """检索前处理入口：多轮改写 + 实体抽取 + 路由，返回 Query。

    Args:
        question: 用户原始问题
        history: 多轮历史 [(用户, 助手), ...]
        mode: auto / rag / graphrag / hybrid
        graph_available: 知识图谱是否可用；缺省自动判断 config.graph_path 是否存在

    说明：只在需要图谱（graphrag / hybrid）时才做实体抽取，纯向量(rag)模式跳过，省一次 LLM 调用。
    """
    question = (question or "").strip()
    if not question:
        raise ValueError("question 不能为空")

    if graph_available is None:
        graph_available = _graph_available()

    history = history or []
    standalone = _rewrite(question, history) if history else question
    resolved = resolve_mode(mode, graph_available)
    need_graph = resolved in (MODE_GRAPHRAG, MODE_HYBRID)
    keywords = _extract_keywords(standalone) if need_graph else []

    logger.info(
        "Query 构建完成：mode={}, 是否改写={}, 关键词数={}",
        resolved, standalone != question, len(keywords),
    )
    return Query(question=question, standalone=standalone, keywords=keywords, mode=resolved)


if __name__ == "__main__":
    # 命令行自测（不调用真实 LLM）：
    #   cd backend && python -m core.tools.mrag.query.query
    print("=== resolve_mode 路由自测 ===")
    print("auto + 有图谱   ->", resolve_mode("auto", True))
    print("auto + 无图谱   ->", resolve_mode("auto", False))
    print("hybrid          ->", resolve_mode("hybrid", True))
    print("rag             ->", resolve_mode("rag", False))
    print("graphrag 无图谱 ->", end=" ")
    try:
        resolve_mode("graphrag", False)
    except RuntimeError as exc:
        print("抛出预期异常:", exc)

    print("=== build_query（monkeypatch chat，不联网）===")
    import core.tools.mrag.query.query as m

    def fake_chat(messages, temperature=0.0, **kw):
        last = messages[-1]["content"]
        if "改写" in messages[0]["content"]:
            return "R125 车型的运输成本怎么降低？"
        return "R125 车型\n运输成本\n燃油成本"

    m.chat = fake_chat
    q = m.build_query(
        "那它的成本怎么降？",
        history=[("R125 车型是什么？", "是一款物流车型")],
        mode="hybrid",
    )
    print("standalone:", q.standalone)
    print("keywords  :", q.keywords)
    print("mode      :", q.mode)
    print("ALL_PASS")
