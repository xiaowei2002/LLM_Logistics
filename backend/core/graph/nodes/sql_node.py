"""
SQLAgent 适配节点。

负责把外层 LogisticsAgentState 中的用户任务，
转换为 SQLAgent.query() 调用，并把结果写回统一 State。
"""

from __future__ import annotations

from typing import Protocol

from core.graph.state import LogisticsAgentState


class SQLQueryAgent(Protocol):
    async def query(self, question: str) -> str:
        ...


def build_sql_node(sql_agent: SQLQueryAgent):
    """
    构建 SQL 查询节点。

    使用依赖注入，便于后续单元测试使用 Fake Agent，
    不直接在节点内部实例化真实 SQLAgent。
    """

    async def sql_node(
        state: LogisticsAgentState,
    ) -> LogisticsAgentState:

        query = state.get(
            "user_query",
            "",
        ).strip()

        

        if not query:
            return {
                "current_agent": "sql",
                "sql_status": "error",
                "sql_error": "用户任务为空。",
                "status": "failed",
                "execution_trace": ["sql"],
            }

        try:
            result = await sql_agent.query(
                query
            )

            if not result.strip():
                return {
                    "current_agent": "sql",
                    "sql_result": "",
                    "sql_status": "error",
                    "sql_error": "SQLAgent 未返回查询结果。",
                    "status": "failed",
                    "execution_trace": ["sql"],
                }

            return {
                "current_agent": "sql",
                "sql_result": result,
                "sql_status": "success",
                "sql_error": None,
                "status": "running",
                "execution_trace": ["sql"],
            }

        except Exception as exc:
            return {
                "current_agent": "sql",
                "sql_status": "error",
                "sql_error": str(exc),
                "status": "failed",
                "execution_trace": ["sql"],
            }

    return sql_node