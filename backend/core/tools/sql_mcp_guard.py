
"""
SQL V2.3：MySQL MCP 只读工具安全包装。

职责：
1. 只允许经过审核的 MCP 工具名称。
2. 为 execute_sql 加入 SQL 只读语法检查。
3. 使用 ToolCall 形式调用原始 MCP 工具。
4. 保留原始 content 和 artifact。
5. 拒绝原始工具错误状态和已识别的错误负载。
6. 不允许危险 SQL 到达原始 MCP。
7. 识别真实 MCP 返回的 Error executing query: 错误。

注意：
此层不代替 MySQL 数据库账号的只读权限。
"""

from __future__ import annotations

import uuid
from typing import Any, Sequence

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool

from core.tools.sql_readonly_guard import (
    validate_readonly_sql,
)


ALLOWED_TOOL_NAMES = frozenset({
    "execute_sql",
    "get_schema_info",
    "get_table_sample",
})


ERROR_TEXT_PREFIXES = (
    "error:",
    "error executing query:",
    "exception:",
    "sql error:",
    "mysql error:",
    "数据库连接失败",
    "查询失败",
    "执行失败",
)


class SQLMCPToolConfigurationError(ValueError):
    """MCP 工具配置不符合安全要求。"""


class SQLMCPExecutionError(RuntimeError):
    """原始 MCP 工具执行失败或响应不可信。"""


def _has_error_flag(value: Any) -> bool:
    """
    识别常见结构化错误标志和 MCP 错误文本。

    仅检查 text 内容块开头的错误前缀，
    不因正常业务结果中包含 error 单词而误判。

    已确认真实 MCP 可能返回：
    ToolMessage.status = "success"
    content = [{
        "type": "text",
        "text": "Error executing query: ..."
    }]
    """

    if isinstance(value, dict):
        if value.get("isError") is True:
            return True

        if value.get("is_error") is True:
            return True

        if value.get("success") is False:
            return True

        status = value.get("status")

        if (
            isinstance(status, str)
            and status.lower() in {
                "error",
                "failed",
                "failure",
            }
        ):
            return True

        if value.get("error") not in (
            None,
            "",
            False,
        ):
            return True

        if value.get("type") == "text":
            text = value.get("text")

            if isinstance(text, str):
                normalized = text.lstrip().lower()

                if normalized.startswith(
                    ERROR_TEXT_PREFIXES
                ):
                    return True

        for key in (
            "content",
            "result",
            "results",
        ):
            nested = value.get(key)

            if isinstance(nested, (dict, list)):
                if _has_error_flag(nested):
                    return True

        return False

    if isinstance(value, list):
        return any(
            _has_error_flag(item)
            for item in value
        )

    return False


def _validate_mcp_response(
    result: ToolMessage,
    expected_call_id: str,
) -> None:
    """
    检查原始 MCP ToolMessage 的执行状态。

    只有完成基本协议校验后才允许返回内容。
    """

    if result.tool_call_id != expected_call_id:
        raise SQLMCPExecutionError(
            "原始 MCP 工具调用 ID 不匹配。"
        )

    if result.name != "execute_sql":
        raise SQLMCPExecutionError(
            "原始 MCP 工具返回名称不匹配。"
        )

    if result.status != "success":
        raise SQLMCPExecutionError(
            "原始 MCP 执行失败："
            + str(result.content)
        )

    if _has_error_flag(result.artifact):
        raise SQLMCPExecutionError(
            "原始 MCP artifact 报告执行失败："
            + str(result.artifact)
        )

    if _has_error_flag(result.content):
        raise SQLMCPExecutionError(
            "原始 MCP 内容报告执行失败："
            + str(result.content)
        )

    if result.content is None:
        raise SQLMCPExecutionError(
            "原始 MCP 没有返回有效内容。"
        )

    if (
        isinstance(result.content, str)
        and not result.content.strip()
    ):
        raise SQLMCPExecutionError(
            "原始 MCP 返回空内容，"
            "无法确认执行结果。"
        )

    if (
        isinstance(result.content, list)
        and not result.content
    ):
        raise SQLMCPExecutionError(
            "原始 MCP 返回空内容块，"
            "无法确认执行结果。"
        )


def build_readonly_sql_tools(
    tools: Sequence[BaseTool],
) -> list[BaseTool]:
    """
    将原始 execute_sql 替换为只读包装工具。

    未审核的工具和重复工具名将被拒绝。
    """

    original_tools = list(tools)

    names = [
        str(tool.name)
        for tool in original_tools
    ]

    if len(names) != len(set(names)):
        raise SQLMCPToolConfigurationError(
            "MCP 工具名称存在重复，拒绝加载。"
        )

    unexpected = (
        set(names) - ALLOWED_TOOL_NAMES
    )

    if unexpected:
        raise SQLMCPToolConfigurationError(
            "检测到未审核的 MCP 工具："
            + ", ".join(sorted(unexpected))
        )

    if "execute_sql" not in names:
        raise SQLMCPToolConfigurationError(
            "缺少 execute_sql 工具，"
            "无法建立只读执行包装。"
        )

    wrapped_tools: list[BaseTool] = []

    for original in original_tools:

        if original.name != "execute_sql":
            wrapped_tools.append(original)
            continue

        async def execute_readonly(
            query: str,
            _original: BaseTool = original,
        ) -> tuple[Any, Any]:
            """
            校验 SQL，并接收原始 MCP ToolMessage。

            返回 (content, artifact)，供 LangChain
            的 content_and_artifact 机制使用。
            """

            statement = validate_readonly_sql(
                query
            )

            call_id = "sql-inner-" + uuid.uuid4().hex

            result = await _original.ainvoke({
                "type": "tool_call",
                "id": call_id,
                "name": "execute_sql",
                "args": {
                    "query": statement,
                },
            })

            if not isinstance(result, ToolMessage):
                raise SQLMCPExecutionError(
                    "原始 MCP 未返回 ToolMessage，"
                    "无法确认执行状态。"
                )

            _validate_mcp_response(
                result,
                expected_call_id=call_id,
            )

            return (
                result.content,
                result.artifact,
            )

        async def guarded_execute(
            query: str,
        ) -> tuple[Any, Any]:
            """执行受保护的只读 MySQL SELECT 查询。"""
            return await execute_readonly(
                query
            )

        wrapped = StructuredTool.from_function(
            coroutine=guarded_execute,
            name="execute_sql",
            description=(
                "Execute one validated read-only MySQL "
                "SELECT query. Write statements, DDL, "
                "administrative SQL, multiple statements "
                "and unsupported syntax are rejected."
            ),
            infer_schema=True,
            response_format="content_and_artifact",
        )

        wrapped_tools.append(wrapped)

    return wrapped_tools
