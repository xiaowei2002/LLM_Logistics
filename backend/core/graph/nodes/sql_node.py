
"""
SQLAgent 适配节点：SQL V2 结构化执行证据版本。

职责：
1. 从 LogisticsAgentState 读取用户查询。
2. 调用 SQLAgent.query_with_evidence()。
3. 校验结构化执行状态。
4. 只有经过工具执行证据核验，才标记 SQL 成功。
5. 将结果写回外层 LangGraph State。

安全约束：
- 不接受仅有 query() -> str 的旧 Agent。
- 不根据非空自然语言回答判定查询成功。
- 不把错误回答作为已验证 SQL 结果向下游传播。
- 不在节点内部直接连接数据库或调用 MCP。

说明：
query_verified 代表 SQLAgent 的工具执行证据通过，
不代表最终回答的全部业务数值经过独立核验。
"""

from __future__ import annotations

from typing import Any, Protocol

from core.graph.state import LogisticsAgentState


class SQLVerifiedAgent(Protocol):
    """SQL Node 所要求的结构化查询接口。"""

    async def query_with_evidence(
        self,
        question: str,
    ) -> dict[str, Any]:
        ...


def _sql_failure(
    error: str,
) -> LogisticsAgentState:
    """
    统一构建 SQL Node 的失败状态。

    失败时清空 sql_result，避免向 Report Node
    传递未经验证的查询回答。
    """
    return {
        "current_agent": "sql",
        "sql_result": "",
        "sql_status": "error",
        "sql_error": error,
        "status": "failed",
        "execution_trace": ["sql"],
    }


def build_sql_node(
    sql_agent: SQLVerifiedAgent,
):
    """
    构建 SQL 查询节点。

    通过依赖注入接收 SQLAgent，
    便于使用离线 Fake Agent 测试。
    """

    async def sql_node(
        state: LogisticsAgentState,
    ) -> LogisticsAgentState:

        raw_query = state.get(
            "user_query",
            "",
        )

        if not isinstance(raw_query, str):
            return _sql_failure(
                "用户任务必须是字符串。"
            )

        query = raw_query.strip()

        if not query:
            return _sql_failure(
                "用户任务为空。"
            )

        # Fail closed:
        # 不允许回退到没有工具证据的 query()。
        verified_query = getattr(
            sql_agent,
            "query_with_evidence",
            None,
        )

        if not callable(verified_query):
            return _sql_failure(
                "SQLAgent 不支持结构化工具执行证据接口。"
            )

        try:
            result = await verified_query(
                query
            )

            if not isinstance(result, dict):
                return _sql_failure(
                    "SQLAgent 返回的证据结果格式无效。"
                )

            agent_status = result.get(
                "status"
            )

            query_verified = result.get(
                "query_verified"
            )

            answer = result.get(
                "answer"
            )

            error = result.get(
                "error"
            )

            if (
                agent_status != "success"
                or query_verified is not True
            ):
                if isinstance(error, str) and error.strip():
                    error_text = error.strip()
                else:
                    error_text = (
                        "SQL 查询未通过工具执行证据核验。"
                    )

                return _sql_failure(
                    error_text
                )

            if not isinstance(answer, str):
                return _sql_failure(
                    "SQLAgent 返回的最终回答不是字符串。"
                )

            answer = answer.strip()

            if not answer:
                return _sql_failure(
                    "SQLAgent 未返回有效查询结果。"
                )

            # 不允许成功结果同时携带错误。
            if error not in (None, ""):
                return _sql_failure(
                    "SQLAgent 返回了相互矛盾的成功与错误状态。"
                )

            # 即使 query_verified=True，也要检查
            # 是否带有合法的查询工具执行标识。
            if result.get("tool_name") != "execute_sql":
                return _sql_failure(
                    "缺少 execute_sql 工具执行证据。"
                )

            tool_call_id = result.get(
                "tool_call_id"
            )

            if (
                not isinstance(tool_call_id, str)
                or not tool_call_id.strip()
            ):
                return _sql_failure(
                    "SQL 查询工具调用 ID 缺失。"
                )

            return {
                "current_agent": "sql",
                "sql_result": answer,
                "sql_status": "success",
                "sql_error": None,
                "status": "running",
                "execution_trace": ["sql"],
            }

        except Exception as exc:
            return _sql_failure(
                f"{type(exc).__name__}: {exc}"
            )

    return sql_node
