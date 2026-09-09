from __future__ import annotations

import json
from typing import Any, TypedDict

from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from core.agents.human_review import inspect_forecast_query
from core.llm import LLMClient, get_llm
from core.prompts.demand_forecast import DEMAND_FORECAST_SYSTEM_PROMPT
from core.tools.demand_forecast.demand_forecast_tool import (
    DEMAND_FORECAST_TOOLS,
)


# ==========================================================
# 1. LangGraph State
# ==========================================================

class DemandForecastState(TypedDict, total=False):
    """
    需求预测 Agent 的图状态。
    """

    query: str
    answer: str

    # Human-in-the-loop 状态
    needs_human_input: bool
    human_reason: str | None
    human_question: str | None

    # Tool 执行状态
    tool_status: str | None
    tool_error: str | None

    # review_result 恢复后是否需要重新执行预测
    retry_requested: bool


# ==========================================================
# 2. Demand Forecast Agent
# ==========================================================

class DemandForecastAgent:
    """
    带 Human-in-the-loop 的需求预测 Agent。

    流程：
    1. 检查用户输入；
    2. 输入缺少关键信息时 interrupt；
    3. 用户补充后继续；
    4. LangChain Agent 调用预测 Tool；
    5. 检查 Tool 执行结果；
    6. Tool 异常时再次 interrupt；
    7. 用户修正后重新执行预测；
    8. 正常结果结束。
    """

    def __init__(
        self,
        client: LLMClient | None = None,
    ) -> None:

        self.client = client or get_llm()

        # --------------------------------------------------
        # LangChain Agent
        # --------------------------------------------------

        self.base_agent = create_agent(
            model=self.client.model,
            tools=DEMAND_FORECAST_TOOLS,
            system_prompt=DEMAND_FORECAST_SYSTEM_PROMPT,
        )

        # --------------------------------------------------
        # LangGraph
        # --------------------------------------------------

        workflow = StateGraph(
            DemandForecastState
        )

        workflow.add_node(
            "validate_input",
            self._validate_input,
        )

        workflow.add_node(
            "run_agent",
            self._run_agent,
        )

        workflow.add_node(
            "review_result",
            self._review_result,
        )

        workflow.add_edge(
            START,
            "validate_input",
        )

        workflow.add_edge(
            "validate_input",
            "run_agent",
        )

        workflow.add_edge(
            "run_agent",
            "review_result",
        )

        workflow.add_conditional_edges(
            "review_result",
            self._route_after_review,
            {
                "done": END,
                "retry": "run_agent",
            },
        )

        # interrupt/resume 必须依赖 checkpointer
        self.graph = workflow.compile(
            checkpointer=InMemorySaver()
        )

    # ======================================================
    # 3. 输入检查
    # ======================================================

    def _validate_input(
        self,
        state: DemandForecastState,
    ) -> DemandForecastState:
        """
        在执行预测前检查用户输入。

        如果缺少预测日期或具体时刻，则通过 interrupt()
        暂停当前 thread，等待用户补充。
        """

        query = state.get(
            "query",
            "",
        )

        inspection = inspect_forecast_query(
            query
        )

        if inspection["ready"]:
            return {
                "needs_human_input": False,
                "human_reason": None,
                "human_question": None,
            }

        # --------------------------------------------------
        # Human-in-the-loop：输入信息不足
        # --------------------------------------------------

        human_answer = interrupt(
            {
                "type": "clarification",
                "reason": inspection["reason"],
                "question": inspection["question"],
                "original_query": query,
            }
        )

        clarification = self._extract_human_answer(
            human_answer
        )

        combined_query = (
            f"{query}\n"
            f"用户补充信息：{clarification}"
        ).strip()

        return {
            "query": combined_query,
            "needs_human_input": False,
            "human_reason": None,
            "human_question": None,
        }

    # ======================================================
    # 4. 执行 LangChain Agent
    # ======================================================

    def _run_agent(
        self,
        state: DemandForecastState,
    ) -> DemandForecastState:
        """
        调用 LangChain Agent。

        除了得到最终自然语言回答外，
        还检查 ToolMessage 中的真实 Tool 返回结果，
        避免 Tool 已失败但 API 外层仍然报告 success。
        """

        query = state["query"]

        result = self.base_agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": query,
                    }
                ]
            }
        )

        messages = result.get(
            "messages",
            [],
        )

        if not messages:
            raise ValueError(
                "Agent 未返回任何消息。"
            )

        tool_status = "success"
        tool_error: str | None = None

        # --------------------------------------------------
        # 检查所有 ToolMessage
        # --------------------------------------------------

        for message in messages:

            message_type = getattr(
                message,
                "type",
                "",
            )

            if message_type != "tool":
                continue

            content = getattr(
                message,
                "content",
                None,
            )

            if content is None:
                continue

            tool_result = self._parse_tool_result(
                content
            )

            if tool_result is None:
                continue

            error_message = self._inspect_tool_result(
                tool_result
            )

            if error_message is not None:
                tool_status = "error"
                tool_error = error_message
                break

        # --------------------------------------------------
        # 最终 AI 回答
        # --------------------------------------------------

        last_message = messages[-1]

        content = getattr(
            last_message,
            "content",
            "",
        )

        return {
            "answer": str(content),
            "tool_status": tool_status,
            "tool_error": tool_error,
            "retry_requested": False,
        }

    # ======================================================
    # 5. Tool 结果审核
    # ======================================================

    def _review_result(
        self,
        state: DemandForecastState,
    ) -> DemandForecastState:
        """
        对 Tool 执行结果进行确定性审核。

        正常：
            直接结束。

        异常：
            interrupt -> 用户确认/修改 -> 重新执行预测。
        """

        tool_status = state.get(
            "tool_status",
        )

        tool_error = state.get(
            "tool_error",
        )

        # Tool 正常，直接结束
        if tool_status == "success":
            return {
                "needs_human_input": False,
                "human_reason": None,
                "human_question": None,
                "retry_requested": False,
            }

        # --------------------------------------------------
        # Human-in-the-loop：Tool 执行异常
        # --------------------------------------------------

        human_answer = interrupt(
            {
                "type": "result_review",
                "reason": "tool_execution_error",
                "question": (
                    "当前预测无法正常完成。"
                    f"错误信息：{tool_error or '未知错误'}。"
                    "请确认或修正预测条件后继续。"
                ),
                "original_query": state.get(
                    "query",
                    "",
                ),
                "tool_error": tool_error,
            }
        )

        clarification = self._extract_human_answer(
            human_answer
        )

        original_query = state.get(
            "query",
            "",
        )

        revised_query = (
            f"{original_query}\n"
            f"用户修正信息：{clarification}"
        ).strip()

        return {
            "query": revised_query,

            # 清除旧回答，避免误认为上一次失败结果有效
            "answer": "",

            # 清除旧 Tool 状态，准备重新运行
            "tool_status": None,
            "tool_error": None,

            "needs_human_input": False,
            "human_reason": None,
            "human_question": None,

            # 告诉 conditional edge 回到 run_agent
            "retry_requested": True,
        }

    # ======================================================
    # 6. Review 后路由
    # ======================================================

    def _route_after_review(
        self,
        state: DemandForecastState,
    ) -> str:
        """
        决定 review_result 后：
        - done：结束
        - retry：重新执行 Agent
        """

        if state.get(
            "retry_requested",
            False,
        ):
            return "retry"

        return "done"

    # ======================================================
    # 7. Human Answer 统一解析
    # ======================================================

    @staticmethod
    def _extract_human_answer(
        human_answer: Any,
    ) -> str:
        """
        interrupt resume 输入既支持：

        {"answer": "..."} 

        也支持：

        "..."
        """

        if isinstance(
            human_answer,
            dict,
        ):
            clarification = str(
                human_answer.get(
                    "answer",
                    "",
                )
            ).strip()

        else:
            clarification = str(
                human_answer
            ).strip()

        if not clarification:
            raise ValueError(
                "Human-in-the-loop 补充信息不能为空。"
            )

        return clarification

    # ======================================================
    # 8. Tool Message 解析
    # ======================================================

    @staticmethod
    def _parse_tool_result(
        content: Any,
    ) -> dict[str, Any] | None:
        """
        将 ToolMessage.content 尽量转换为 dict。
        """

        if isinstance(
            content,
            dict,
        ):
            return content

        if isinstance(
            content,
            str,
        ):
            try:
                parsed = json.loads(
                    content
                )

                if isinstance(
                    parsed,
                    dict,
                ):
                    return parsed

            except json.JSONDecodeError:
                return None

        return None

    # ======================================================
    # 9. Tool 返回结果检查
    # ======================================================

    @staticmethod
    def _inspect_tool_result(
        tool_result: dict[str, Any],
    ) -> str | None:
        """
        检查 Tool 是否真正成功。

        返回：
        - None：正常
        - str：错误原因
        """

        # --------------------------------------------------
        # 9.1 Tool 外层直接失败
        # --------------------------------------------------

        if tool_result.get(
            "status"
        ) == "error":

            return str(
                tool_result.get("message")
                or tool_result.get("detail")
                or tool_result
            )

        # --------------------------------------------------
        # 9.2 demand_forecast 的嵌套 result 失败
        # --------------------------------------------------

        nested_result = tool_result.get(
            "result"
        )

        if (
            isinstance(
                nested_result,
                dict,
            )
            and nested_result.get(
                "status"
            ) == "error"
        ):

            return str(
                nested_result.get("message")
                or nested_result.get("detail")
                or nested_result
            )

        # --------------------------------------------------
        # 9.3 单模型预测成功但关键字段缺失
        # --------------------------------------------------

        tool_name = tool_result.get(
            "tool"
        )

        if (
            tool_name == "demand_forecast"
            and tool_result.get("status") == "success"
        ):

            if not isinstance(
                nested_result,
                dict,
            ):
                return (
                    "需求预测 Tool 返回 success，"
                    "但缺少有效 result。"
                )

            if "forecast" not in nested_result:
                return (
                    "需求预测结果缺少 forecast 字段。"
                )

            if (
                "two_hour_peak_demand"
                not in nested_result
            ):
                return (
                    "需求预测结果缺少 "
                    "two_hour_peak_demand 字段。"
                )

        # --------------------------------------------------
        # 9.4 模型比较成功但关键字段缺失
        # --------------------------------------------------

        if (
            tool_name
            == "compare_demand_forecasts"
            and tool_result.get("status")
            == "success"
        ):

            if (
                "baseline_result"
                not in tool_result
            ):
                return (
                    "模型比较结果缺少 "
                    "baseline_result。"
                )

            if (
                "state_conditioned_result"
                not in tool_result
            ):
                return (
                    "模型比较结果缺少 "
                    "state_conditioned_result。"
                )

        return None

    # ======================================================
    # 10. 首次调用
    # ======================================================

    def invoke(
        self,
        query: str,
        thread_id: str,
    ) -> dict[str, Any]:
        """
        创建/继续一个指定 thread_id 的预测任务。
        """

        config = {
            "configurable": {
                "thread_id": thread_id,
            }
        }

        return self.graph.invoke(
            {
                "query": query,
            },
            config=config,
        )

    # ======================================================
    # 11. Human-in-the-loop Resume
    # ======================================================

    def resume(
        self,
        thread_id: str,
        human_answer: str,
    ) -> dict[str, Any]:
        """
        使用相同 thread_id 恢复被 interrupt 暂停的任务。
        """

        config = {
            "configurable": {
                "thread_id": thread_id,
            }
        }

        return self.graph.invoke(
            Command(
                resume={
                    "answer":
                        human_answer
                }
            ),
            config=config,
        )