"""具有固定业务流程的生产调度与计算卸载智能体。"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, TypedDict

from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from core.llm import LLMClient, get_llm
from core.prompts.scheduling_offloading import SCHEDULING_OFFLOADING_SYSTEM_PROMPT, SCHEDULING_RESULT_ANALYSIS_PROMPT
from core.tools.scheduling_offloading import run_scheduling_offloading
from core.tools.scheduling_offloading.scheduling_tool import SCHEDULING_OFFLOADING_TOOLS


_JOB_PATTERN = re.compile(r"(?<![\d.])(-?\d+(?:\.\d+)?)\s*个?\s*工件")
_SEED_PATTERN = re.compile(
    r"(?:随机种子|seed)\s*(?:为|是|=|:|：)?\s*([^\s，。；;]+)",
    re.IGNORECASE,
)
_RUN_PATTERNS = (
    re.compile(r"(?:生成|运行|计算|优化|重跑|重新).{0,12}(?:调度|排产|方案|场景|MAPPO)", re.IGNORECASE),
    re.compile(r"(?:调度|排产|MAPPO|默认场景).{0,12}(?:生成|运行|计算|优化|重跑|重新)", re.IGNORECASE),
)
_DOMAIN_TERMS = (
    "调度", "排产", "工件", "工序", "机器", "MAPPO", "SPT",
    "卸载", "边缘", "云端", "本地计算", "质检", "完工", "拖期", "利用率",
    "阻塞", "超时", "随机种子", "决策过程", "计算节点",
)


def inspect_scheduling_query(query: str) -> dict[str, Any]:
    """检查请求范围以及当前MAPPO模型能够接受的参数。"""
    text = query.strip()
    if not text:
        return {
            "ready": False,
            "reason": "empty_query",
            "question": "请输入生产调度或计算卸载任务。",
            "requires_run": False,
        }

    if not any(term.lower() in text.lower() for term in _DOMAIN_TERMS):
        return {
            "ready": False,
            "reason": "unsupported_scope",
            "question": (
                "当前智能体只处理生产调度与计算卸载。"
                "请选择下方的一项调度任务，或重新描述你的调度需求。"
            ),
            "requires_run": False,
        }

    job_matches = list(_JOB_PATTERN.finditer(text))
    job_match = job_matches[-1] if job_matches else None
    if job_match and (not re.fullmatch(r"\d+", job_match.group(1)) or int(job_match.group(1)) < 1):
        return {
            "ready": False,
            "reason": "invalid_job_count",
            "question": (
                "工件数量必须为正整数，请补充要安排的工件数量。"
            ),
            "requires_run": True,
        }

    resource_patterns = (
        r"(?<![\d.])(-?\d+(?:\.\d+)?)\s*台\s*机器",
        r"(?<![\d.])(-?\d+(?:\.\d+)?)\s*个?\s*边缘(?:计算)?节点",
    )
    for pattern in resource_patterns:
        matches = list(re.finditer(pattern, text))
        match = matches[-1] if matches else None
        if match and (not re.fullmatch(r"\d+", match.group(1)) or int(match.group(1)) < 1):
            return {"ready": False, "reason": "invalid_resource_count",
                    "question": "机器数量和边缘节点数量都必须为正整数，请重新填写。", "requires_run": True}

    seed_matches = list(_SEED_PATTERN.finditer(text))
    seed_match = seed_matches[-1] if seed_matches else None
    if seed_match:
        seed_text = seed_match.group(1)
        if not re.fullmatch(r"\d+", seed_text) or int(seed_text) > 2_147_483_647:
            return {
                "ready": False,
                "reason": "invalid_seed",
                "question": "随机种子必须是0到2147483647之间的整数，请重新填写。",
                "requires_run": True,
            }

    requires_run = any(pattern.search(text) for pattern in _RUN_PATTERNS)
    requires_run = requires_run or text in {"排产", "帮我排产", "生产调度", "计算卸载"}
    parameters = {}
    if job_match:
        parameters["number_of_jobs"] = int(job_match.group(1))
    if seed_match:
        parameters["seed"] = int(seed_match.group(1))
    for key, pattern in zip(("machine_count", "edge_count"), resource_patterns):
        matches = list(re.finditer(pattern, text))
        if matches:
            parameters[key] = int(matches[-1].group(1))
    labels = {"number_of_jobs": "工件数量", "machine_count": "机器数量", "edge_count": "边缘节点数量", "seed": "随机种子"}
    missing = [label for key, label in labels.items() if key not in parameters]
    if requires_run and missing:
        return {
            "ready": False,
            "reason": "missing_scheduling_parameters",
            "question": "请补充或确认本次场景参数：" + "、".join(missing) + "。可参考卡片中的示例填写，确认后再运行。",
            "requires_run": True,
        }
    return {"ready": True, "reason": None, "question": None,
            "requires_run": requires_run, "parameters": parameters}


class SchedulingOffloadingState(TypedDict, total=False):
    query: str
    answer: str
    run_result: dict[str, Any] | None
    requires_run: bool
    tool_status: str | None
    tool_error: str | None
    retry_requested: bool


class SchedulingOffloadingAgent:
    """按“输入检查→工具运行→结果审核”流程执行调度任务。"""

    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client
        self._base_agent = None

        workflow = StateGraph(SchedulingOffloadingState)
        workflow.add_node("validate_input", self._validate_input)
        workflow.add_node("run_agent", self._run_agent)
        workflow.add_node("review_result", self._review_result)
        workflow.add_edge(START, "validate_input")
        workflow.add_edge("validate_input", "run_agent")
        workflow.add_edge("run_agent", "review_result")
        workflow.add_conditional_edges(
            "review_result",
            self._route_after_review,
            {"done": END, "retry": "run_agent"},
        )
        self.graph = workflow.compile(checkpointer=InMemorySaver())

    def _ensure_base_agent(self):
        if self._base_agent is None:
            self.client = self.client or get_llm()
            self._base_agent = create_agent(
                model=self.client.model,
                tools=SCHEDULING_OFFLOADING_TOOLS,
                system_prompt=SCHEDULING_OFFLOADING_SYSTEM_PROMPT,
            )
        return self._base_agent

    def optimize(self, request: dict[str, Any]) -> dict[str, Any]:
        """运行MAPPO并整理页面展示所需的数据。"""
        started = time.perf_counter()
        machines = request.get("machine_count", 6)
        edges = request.get("edge_count", 2)
        result = run_scheduling_offloading(request)
        return {
            "status": "success",
            "run_id": str(uuid.uuid4()),
            "data_source": "simulation",
            "scenario": {
                "seed": request["seed"],
                "number_of_jobs": request["number_of_jobs"],
                "initial_jobs": min(6, request["number_of_jobs"]),
                "machine_count": machines,
                "edge_count": edges,
                "machine_topology": "operation_candidates",
                "eligible_machines_per_operation": min(machines, 3),
                "machine_flexibility": f"partial{min(machines, 3)}",
                "compute_nodes": ["LOCAL", *[f"EDGE{index + 1}" for index in range(edges)], "CLOUD"],
            },
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            **result,
        }

    async def analyze_result(self, run_result: dict[str, Any]) -> str:
        """仅解释已有结果；直接使用未绑定工具的LLM，不进入算法执行流程。"""
        payload = {key: run_result[key] for key in (
            "run_id", "scenario", "metrics", "warnings", "elapsed_seconds"
        ) if key in run_result}
        summary = dict(run_result.get("decision_summary") or {})
        summary["by_action"] = list(summary.get("by_action") or [])[:12]
        payload["decision_summary"] = summary
        client = self.client or get_llm()
        response = await client.chat([
            {"role": "system", "content": SCHEDULING_RESULT_ANALYSIS_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ])
        content = response.content
        if isinstance(content, list):
            content = "\n".join(block.get("text", "") for block in content if isinstance(block, dict))
        answer = str(content or "").strip()
        if not answer:
            raise ValueError("分析模型没有返回文字内容")
        return answer

    def _validate_input(self, state: SchedulingOffloadingState) -> SchedulingOffloadingState:
        # 与需求预测一致：信息不足时暂停，补充后恢复；无效补充继续弹卡片。
        query = state.get("query", "")
        inspection = inspect_scheduling_query(query)
        while not inspection["ready"]:
            human_answer = interrupt({
                "type": "clarification",
                "reason": inspection["reason"],
                "question": inspection["question"],
                "original_query": query,
            })
            clarification = self._extract_human_answer(human_answer)
            query = f"{query}\n用户补充或确认：{clarification}".strip()
            inspection = inspect_scheduling_query(query)

        if inspection["requires_run"]:
            params = inspection["parameters"]
            # 最新补充覆盖原参数，以明确的完整参数交给原智能体执行。
            query += (f"\n本次最终确认：{params['number_of_jobs']}个工件、"
                      f"{params['machine_count']}台机器、{params['edge_count']}个边缘节点，"
                      f"随机种子{params['seed']}。请按这些参数生成生产调度与计算卸载方案。")
        return {"query": query, "requires_run": bool(inspection["requires_run"])}

    def _run_agent(self, state: SchedulingOffloadingState) -> SchedulingOffloadingState:
        response = self._ensure_base_agent().invoke({
            "messages": [{"role": "user", "content": state["query"]}]
        })
        messages = response.get("messages", [])
        if not messages:
            raise ValueError("智能体未返回任何消息。")

        run_result = None
        tool_error = None
        for message in messages:
            if getattr(message, "type", "") != "tool":
                continue
            value = self._as_dict(getattr(message, "content", None))
            if not value or value.get("tool") != "run_scheduling_offloading":
                continue
            error = self._inspect_tool_result(value)
            if error:
                tool_error = error
                break
            run_result = value.get("result")

        if state.get("requires_run") and run_result is None and tool_error is None:
            tool_error = "该请求需要生成调度方案，但智能体没有调用MAPPO工具。"

        answer = str(getattr(messages[-1], "content", "")).strip()
        if not answer:
            answer = "调度任务执行完成。" if run_result else "未生成有效回答。"
        return {
            "answer": answer,
            "run_result": run_result,
            "tool_status": "error" if tool_error else "success",
            "tool_error": tool_error,
            "retry_requested": False,
        }

    def _review_result(self, state: SchedulingOffloadingState) -> SchedulingOffloadingState:
        if state.get("tool_status") == "success":
            return {"retry_requested": False}

        tool_error = state.get("tool_error") or "未知错误"
        human_answer = interrupt({
            "type": "result_review",
            "reason": "tool_execution_error",
            "question": (
                f"当前调度方案无法生成。错误信息：{tool_error}。"
                "请修改随机种子后重试，或输入“使用默认场景重新运行”。"
            ),
            "original_query": state.get("query", ""),
            "tool_error": tool_error,
        })
        clarification = self._extract_human_answer(human_answer)
        return {
            "query": f"{state.get('query', '')}\n用户修正信息：{clarification}".strip(),
            "answer": "",
            "run_result": None,
            "requires_run": True,
            "tool_status": None,
            "tool_error": None,
            "retry_requested": True,
        }

    @staticmethod
    def _route_after_review(state: SchedulingOffloadingState) -> str:
        return "retry" if state.get("retry_requested") else "done"

    @staticmethod
    def _extract_human_answer(human_answer: Any) -> str:
        if isinstance(human_answer, dict):
            answer = str(human_answer.get("answer", "")).strip()
        else:
            answer = str(human_answer).strip()
        if not answer:
            raise ValueError("补充信息不能为空。")
        return answer

    @staticmethod
    def _as_dict(content: Any) -> dict[str, Any] | None:
        if isinstance(content, dict):
            return content
        if isinstance(content, str):
            try:
                value = json.loads(content)
                return value if isinstance(value, dict) else None
            except json.JSONDecodeError:
                return None
        return None

    @staticmethod
    def _inspect_tool_result(value: dict[str, Any]) -> str | None:
        if value.get("status") == "error":
            return str(value.get("message") or "MAPPO工具运行失败")
        result = value.get("result")
        if not isinstance(result, dict):
            return "MAPPO工具没有返回有效结果。"
        required = ("metrics", "decisions", "decision_summary", "scenario")
        missing = [field for field in required if field not in result]
        if missing:
            return f"MAPPO结果缺少字段：{', '.join(missing)}。"
        return None

    def invoke(self, query: str, thread_id: str) -> dict[str, Any]:
        return self.graph.invoke(
            {"query": query},
            config={"configurable": {"thread_id": thread_id}},
        )

    def resume(self, thread_id: str, human_answer: str) -> dict[str, Any]:
        return self.graph.invoke(
            Command(resume={"answer": human_answer}),
            config={"configurable": {"thread_id": thread_id}},
        )
