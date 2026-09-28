"""
==========================================================================
生成：把检索结果（文档片段 / 图谱三元组）拼进提示词，调 qwen 产出最终答案
  - 三种 system 提示词逐字照抄（RAG / GraphRAG / Hybrid）
  - 纯 Python list[dict] 复刻 langchain 的 ChatPromptTemplate 布局，不引 langchain
  - 问题用 Query.standalone（多轮改写后的独立问题），历史展开成 user/assistant 消息

放在 retrieval / rerank 之后，是问答链路的最后一环。
==========================================================================
"""
from __future__ import annotations

from typing import Dict, Iterator, List, Optional, Tuple

from core.tools.mrag.enums import SearchMode
from core.tools.mrag.query.query import Query
from core.tools.mrag.retrieval.retrieval import RetrievalResult
from core.tools.mrag.utils.llm import chat, chat_stream
from core.tools.mrag.utils.utils import config

# 三种 system 提示词
_RAG_SYSTEM = """你是物流领域知识问答助手，请严格依据【参考资料】回答用户问题。
要求：
1. 只使用参考资料中的信息作答，不要引入外部知识，不要编造；
2. 若参考资料不足以回答问题，请明确说明"参考资料中未找到相关信息"；
3. 用中文回答，条理清晰、简洁准确。"""

_GRAPH_SYSTEM = """你是物流领域知识问答助手，将基于给定的【知识图谱三元组】回答用户问题。
每个三元组形如 "实体A -[关系]-> 实体B"，描述物流领域实体之间的关系。
要求：
1. 只依据给出的三元组进行推理作答，不要编造三元组中不存在的事实；
2. 若三元组不足以回答问题，请明确说明"知识图谱中未找到相关信息"；
3. 用中文回答，条理清晰、简洁准确。"""

_HYBRID_SYSTEM = """你是物流领域知识问答助手，请依据【参考资料 · 文档片段】和【知识图谱三元组】回答用户问题。
要求：
1. 只依据给定资料作答，可结合文档片段与三元组中的实体关系进行推理，不要编造；
2. 若资料不足以回答问题，请明确说明"资料中未找到相关信息"；
3. 用中文回答，条理清晰、简洁准确。"""


class Generator:
    """最终答案生成器：把 RetrievalResult 的上下文喂给 qwen，支持一次性 / 流式。"""

    @staticmethod
    def _history_messages(history: Optional[List[Tuple[str, str]]]) -> List[Dict[str, str]]:
        """把 [(用户, 助手), ...] 展开成 [{user}, {assistant}, ...] 消息序列。"""
        out: List[Dict[str, str]] = []
        for human, ai in history or []:
            if human:
                out.append({"role": "user", "content": human})
            if ai:
                out.append({"role": "assistant", "content": ai})
        return out

    def _build_messages(
        self,
        result: RetrievalResult,
        question: str,
        history: Optional[List[Tuple[str, str]]] = None,
    ) -> List[Dict[str, str]]:
        """按 mode 拼出完整 messages：system 提示词 → 资料段 → 历史 → 用户问题。"""
        mode = result.mode
        msgs: List[Dict[str, str]] = []
        if mode == SearchMode.RAG.value:
            msgs.append({"role": "system", "content": _RAG_SYSTEM})
            msgs.append({"role": "system", "content": "【参考资料】\n" + result.context})
        elif mode == SearchMode.GRAPHRAG.value:
            msgs.append({"role": "system", "content": _GRAPH_SYSTEM})
            msgs.append({"role": "system", "content": "【知识图谱三元组】\n" + result.graph_context})
        elif mode == SearchMode.HYBRID.value:
            msgs.append({"role": "system", "content": _HYBRID_SYSTEM})
            msgs.append({"role": "system", "content": "【参考资料 · 文档片段】\n" + result.context})
            msgs.append({"role": "system", "content": "【知识图谱三元组】\n" + result.graph_context})
        else:
            raise ValueError(f"未知检索模式: {mode}（可选 rag / graphrag / hybrid）")
        msgs += self._history_messages(history)
        msgs.append({"role": "user", "content": question})
        return msgs

    def generate(
        self,
        query: Query,
        result: RetrievalResult,
        history: Optional[List[Tuple[str, str]]] = None,
    ) -> str:
        """一次性生成最终答案。"""
        question = query.standalone or query.question
        return chat(
            self._build_messages(result, question, history),
            temperature=config.gen_temperature,
        )

    def stream(
        self,
        query: Query,
        result: RetrievalResult,
        history: Optional[List[Tuple[str, str]]] = None,
    ) -> Iterator[str]:
        """流式生成最终答案，逐 token 产出。"""
        question = query.standalone or query.question
        yield from chat_stream(
            self._build_messages(result, question, history),
            temperature=config.gen_temperature,
        )


if __name__ == "__main__":
    # 命令行自测（假结果，不调真模型不联网）：
    #   cd backend && python -m core.tools.mrag.generation.generation
    from dataclasses import dataclass

    @dataclass
    class FakeResult:
        mode: str
        context: str = ""
        graph_context: str = ""

    g = Generator()
    history = [("上一句：运输成本高吗？", "上一句回答：受燃油影响较高。")]
    cases = {
        "rag": FakeResult(mode="rag", context="[片段1] 运输成本主要由燃油、人工构成"),
        "graphrag": FakeResult(mode="graphrag", graph_context="1. R125车型 -[运输成本]-> 燃油"),
        "hybrid": FakeResult(
            mode="hybrid",
            context="[片段1] 运输成本…",
            graph_context="1. R125车型 -[运输成本]-> 燃油",
        ),
    }

    for mode, res in cases.items():
        msgs = g._build_messages(res, "怎么降低运输成本？", history)
        assert msgs[0]["role"] == "system", "首条应是 system"
        assert msgs[-1] == {"role": "user", "content": "怎么降低运输成本？"}, "末条应是用户问题"
        assert msgs[-2] == {"role": "assistant", "content": "上一句回答：受燃油影响较高。"}, "历史助手消息顺序不对"
        assert msgs[-3] == {"role": "user", "content": "上一句：运输成本高吗？"}, "历史用户消息顺序不对"
        all_system = "".join(m["content"] for m in msgs if m["role"] == "system")
        if mode in ("rag", "hybrid"):
            assert "【参考资料" in all_system, f"{mode} 缺少参考资料段"
        if mode in ("graphrag", "hybrid"):
            assert "【知识图谱三元组】" in all_system, f"{mode} 缺少三元组段"
        print(f"[{mode}] OK  roles={[m['role'] for m in msgs]}")
    print("ALL_PASS")
