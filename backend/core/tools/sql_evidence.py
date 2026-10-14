
"""
SQL V2.1：完整工具执行证据链校验。

目标：
1. 每个工具调用必须有唯一、匹配的工具结果。
2. ToolMessage 必须出现在对应调用之后。
3. 工具调用和返回名称必须一致。
4. 任一工具执行失败，整条执行链不得标记成功。
5. 拒绝孤立、重复、缺失的工具结果。
6. 拒绝工具未完成时出现的 AI 最终回答。
7. 识别常见 MCP 结构化及嵌套文本错误。
8. 业务数据查询必须具有成功的 execute_sql 证据。

不执行 SQL，不访问数据库，不调用 LLM。

安全边界：
query_verified 仅表示工具执行轨迹通过本模块校验，
不代表 SQL 查询语义正确、数据库数据真实，
也不代表最终自然语言回答与工具数据一致。
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage


QUERY_TOOL_NAME = "execute_sql"

APPROVED_TOOL_NAMES = frozenset({
    "execute_sql",
    "get_schema_info",
    "get_table_sample",
})

_ERROR_TEXT_PATTERN = re.compile(
    r"^\s*(?:"
    r"error\b|exception\b|"
    r"sql\s*error\b|mysql\s*error\b|"
    r"database\s+connection\s+(?:failed|error)\b|"
    r"connection\s+(?:failed|refused|lost)\b|"
    r"table\s+.+\s+does(?:n't| not)\s+exist\b|"
    r"错误|执行失败|查询失败|"
    r"SQL\s*执行失败|"
    r"数据库连接失败|连接失败|表不存在"
    r")\s*(?:[:：\s]|$)",
    re.IGNORECASE,
)

_ERROR_CODE_PATTERN = re.compile(
    r"^\s*(?:ERROR\s+\d+|"
    r"\(\d{4},\s*['\"])",
    re.IGNORECASE,
)


def _text(value: Any) -> str:
    """将消息内容转换为可审计的字符串。"""
    if value is None:
        return ""

    if isinstance(value, str):
        return value

    if isinstance(value, (dict, list)):
        return json.dumps(
            value,
            ensure_ascii=False,
            default=str,
        )

    return str(value)


def _explicit_error_text(
    value: str,
) -> str | None:
    """
    判断文本是否具有明确的错误前缀。

    不因正常数据中包含“错误”等词语就拒绝。
    """
    candidate = value.strip()

    if not candidate:
        return None

    if (
        _ERROR_TEXT_PATTERN.match(candidate)
        or _ERROR_CODE_PATTERN.match(candidate)
    ):
        return candidate

    return None


def _error_from_payload(
    value: Any,
    *,
    depth: int = 0,
) -> str | None:
    """
    递归检查常见 MCP / LangChain 错误负载。

    支持：
    - {"isError": true}
    - {"is_error": true}
    - {"status": "error"}
    - {"success": false}
    - {"error": "..."}
    - [{"type": "text", "text": "Error: ..."}]
    - JSON 字符串内部的嵌套错误结构

    depth 限制防止异常的深层嵌套负载
    导致无限递归或过度消耗资源。
    """
    if depth > 12:
        return "工具返回内容嵌套层数超出允许范围。"

    if isinstance(value, str):
        explicit_error = _explicit_error_text(value)

        if explicit_error:
            return explicit_error

        candidate = value.strip()

        if not candidate:
            return None

        # 普通自然语言字符串不强制 JSON 解析。
        if not candidate.startswith(("{", "[")):
            return None

        try:
            decoded = json.loads(candidate)
        except (TypeError, ValueError):
            return None

        return _error_from_payload(
            decoded,
            depth=depth + 1,
        )

    if isinstance(value, list):
        for item in value:
            error = _error_from_payload(
                item,
                depth=depth + 1,
            )

            if error:
                return error

        return None

    if not isinstance(value, dict):
        return None

    if value.get("isError") is True:
        return (
            _text(value.get("error"))
            or _text(value)
        )

    if value.get("is_error") is True:
        return (
            _text(value.get("error"))
            or _text(value)
        )

    status = value.get("status")

    if (
        isinstance(status, str)
        and status.lower() in {
            "error",
            "failed",
            "failure",
        }
    ):
        return (
            _text(value.get("error"))
            or _text(value)
        )

    if value.get("success") is False:
        return (
            _text(value.get("error"))
            or _text(value)
        )

    explicit_error = value.get("error")

    if explicit_error not in (
        None,
        "",
        False,
    ):
        return _text(explicit_error)

    # MCP Content Blocks：
    # {"type": "text", "text": "Error: ..."}
    if value.get("type") == "text":
        content = value.get("text")

        if isinstance(content, (str, list, dict)):
            return _error_from_payload(
                content,
                depth=depth + 1,
            )

    # 仅深入常见协议包裹字段，避免把任意
    # 正常业务字段误判为执行错误。
    for key in (
        "content",
        "result",
        "results",
        "data",
    ):
        nested = value.get(key)

        if isinstance(
            nested,
            (str, dict, list),
        ):
            error = _error_from_payload(
                nested,
                depth=depth + 1,
            )

            if error:
                return error

    return None


def _tool_error(
    message: ToolMessage,
) -> str | None:
    """检查单条工具结果的错误状态。"""
    if message.status == "error":
        return (
            _text(message.content).strip()
            or "工具报告执行失败。"
        )

    if message.status != "success":
        return "工具状态不是明确的 success。"

    for payload in (
        message.artifact,
        message.content,
    ):
        error = _error_from_payload(payload)

        if error:
            return error

    return None


def _failure(
    reason: str,
    *,
    answer: str = "",
) -> dict[str, Any]:
    """统一失败结果结构。"""
    return {
        "status": "error",
        "answer": answer,
        "query_verified": False,
        "tool_name": None,
        "tool_call_id": None,
        "error": reason,
    }


def inspect_sql_evidence(
    messages: list[Any],
) -> dict[str, Any]:
    """
    对 SQLAgent 的完整消息序列进行顺序核验。

    采用 Fail-Closed 策略：
    任何未匹配、失败或顺序异常的工具事件
    都会导致整条执行链校验失败。
    """

    if not isinstance(messages, list) or not messages:
        return _failure(
            "Agent 未返回消息。"
        )

    final_message = messages[-1]

    if not isinstance(
        final_message,
        AIMessage,
    ):
        return _failure(
            "最后一条消息不是 AIMessage。"
        )

    if final_message.tool_calls:
        return _failure(
            "最后一条 AIMessage 仍包含未完成的工具调用。"
        )

    if final_message.invalid_tool_calls:
        return _failure(
            "最后一条 AIMessage 包含无效工具调用。"
        )

    answer = _text(
        final_message.content
    ).strip()

    if not answer:
        return _failure(
            "Agent 未返回最终回答。"
        )

    # 所有已声明的工具调用。
    calls: dict[str, str] = {}

    # 已成功配对的工具结果。
    results: dict[str, ToolMessage] = {}

    # 已声明但尚未返回的工具调用。
    pending: set[str] = set()

    successful_sql_calls: list[str] = []

    for index, message in enumerate(messages):

        if isinstance(message, AIMessage):

            if message.invalid_tool_calls:
                return _failure(
                    "存在无法解析的工具调用。",
                    answer=answer,
                )

            is_last = (
                index == len(messages) - 1
            )

            if message.tool_calls:

                # 不允许在上一批工具尚未结束时，
                # 又开始新的工具调用批次。
                if pending:
                    return _failure(
                        "上一批工具调用尚未全部返回。",
                        answer=answer,
                    )

                if is_last:
                    return _failure(
                        "最终消息仍包含工具调用。",
                        answer=answer,
                    )

                for call in message.tool_calls:

                    if not isinstance(call, dict):
                        return _failure(
                            "工具调用结构无效。",
                            answer=answer,
                        )

                    call_id = call.get("id")
                    call_name = call.get("name")

                    if (
                        not isinstance(
                            call_id, str
                        )
                        or not call_id.strip()
                        or not isinstance(
                            call_name, str
                        )
                        or not call_name.strip()
                    ):
                        return _failure(
                            "工具调用缺少有效名称或 ID。",
                            answer=answer,
                        )

                    if call_id in calls:
                        return _failure(
                            "工具调用 ID 重复。",
                            answer=answer,
                        )

                    if call_name not in APPROVED_TOOL_NAMES:
                        return _failure(
                            "执行轨迹出现未经审核的工具："
                            + call_name,
                            answer=answer,
                        )

                    calls[call_id] = call_name
                    pending.add(call_id)

            else:
                # 允许最后一条 AIMessage 是最终回答。
                # 中途不允许出现有实质内容的回答，
                # 避免未经完成的工具执行被掩盖。
                if not is_last:
                    interim_content = _text(
                        message.content
                    ).strip()

                    if interim_content:
                        return _failure(
                            "工具执行链中出现提前的 AI 回答。",
                            answer=answer,
                        )

                if pending:
                    return _failure(
                        "工具尚未完成就生成 AI 回答。",
                        answer=answer,
                    )

        elif isinstance(message, ToolMessage):

            call_id = message.tool_call_id

            if call_id not in calls:
                return _failure(
                    "存在没有对应调用的工具返回消息。",
                    answer=answer,
                )

            if call_id in results:
                return _failure(
                    "工具结果 ID 重复。",
                    answer=answer,
                )

            if call_id not in pending:
                return _failure(
                    "工具返回顺序异常。",
                    answer=answer,
                )

            expected_name = calls[call_id]

            if message.name != expected_name:
                return _failure(
                    "工具调用名称与返回名称不一致。",
                    answer=answer,
                )

            error = _tool_error(message)

            if error:
                return _failure(
                    f"{expected_name} 执行失败：{error}",
                    answer=answer,
                )

            if not _text(
                message.content
            ).strip():
                return _failure(
                    f"{expected_name} 返回内容为空，"
                    "无法确认工具执行结果。",
                    answer=answer,
                )

            results[call_id] = message
            pending.remove(call_id)

            if expected_name == QUERY_TOOL_NAME:
                successful_sql_calls.append(
                    call_id
                )

    if pending:
        return _failure(
            "存在未收到返回结果的工具调用。",
            answer=answer,
        )

    if len(results) != len(calls):
        return _failure(
            "工具调用与返回消息数量不一致。",
            answer=answer,
        )

    if not successful_sql_calls:
        return _failure(
            "未发现 execute_sql 工具调用证据。",
            answer=answer,
        )

    return {
        "status": "success",
        "answer": answer,
        "query_verified": True,
        "tool_name": QUERY_TOOL_NAME,
        "tool_call_id": successful_sql_calls[-1],
        "error": None,
    }
