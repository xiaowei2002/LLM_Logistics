"""
SQLAgent V3 Real Evaluation

目标：
1. 使用真实 GLM-5.2。
2. 使用真实 Olist MySQL。
3. 使用真实 STDIO MCP。
4. 使用真实 SQL 只读 Guard。
5. 使用只读数据库账号。
6. 不覆盖历史 SQL V1/V2/V2.1/V2.2 结果。
7. 标准答案由真实数据库 gold SQL 动态生成。

运行前要求：

MYSQL_USER=olist_eval_reader
MYSQL_DATABASE=olist_logistics_eval
MYSQL_MCP_TRANSPORT=stdio

LLM_MODEL=glm-5.2
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
LLM_API_KEY=<有效 Key>

运行：

python tests/system_eval/eval_sql_v3_real.py
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

# 必须位于 core 模块导入之前。
# 防止项目 .env 中旧的 root / 本地 LLM 配置
# 覆盖当前 PowerShell 的安全配置。
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from langchain_core.messages import (
    AIMessage,
    ToolMessage,
)

from core.agents.sql_agent import SQLAgent
from core.mcp.mysql_client import MySQLMCPClient
from core.tools.sql_evidence import (
    inspect_sql_evidence,
)
from core.tools.sql_mcp_guard import (
    build_readonly_sql_tools,
)
from core.utils.yaml_loader import load_yaml


ROOT = Path(__file__).resolve().parents[2]

CASES_PATH = (
    ROOT
    / "tests"
    / "system_eval"
    / "cases"
    / "sql_v3_real_cases.json"
)

PROMPT_PATH = (
    ROOT
    / "core"
    / "prompts"
    / "sql_agent_olist_v3.yaml"
)

RESULT_ROOT = (
    ROOT
    / "tests"
    / "system_eval"
    / "results"
)


def require_safe_environment() -> None:
    """
    在真实 LLM + MySQL 评估前进行环境校验。

    这里只检查配置名称，不打印密码或 API Key。
    """

    expected = {
        "MYSQL_USER": "olist_eval_reader",
        "MYSQL_DATABASE": "olist_logistics_eval",
        "MYSQL_MCP_TRANSPORT": "stdio",
        "LLM_MODEL": "glm-5.2",
    }

    problems: list[str] = []

    for key, value in expected.items():
        actual = os.getenv(key)

        if actual != value:
            problems.append(
                f"{key}: expected={value!r}, "
                f"actual={actual!r}"
            )

    if not os.getenv("MYSQL_PASSWORD"):
        problems.append(
            "MYSQL_PASSWORD is missing"
        )

    if not os.getenv("LLM_API_KEY"):
        problems.append(
            "LLM_API_KEY is missing"
        )

    if problems:
        raise RuntimeError(
            "Unsafe or incomplete evaluation "
            "environment:\n- "
            + "\n- ".join(problems)
        )


def load_cases() -> list[dict[str, Any]]:
    with CASES_PATH.open(
        "r",
        encoding="utf-8",
    ) as f:
        cases = json.load(f)

    if not isinstance(cases, list):
        raise TypeError(
            "SQL V3 case file must contain a list."
        )

    return cases


def normalize_cell(value: str) -> str:
    """
    规范数据库结果单元格。

    数值尽量进行标准化，
    避免 1.0 与 1.000000 的纯格式差异。
    """

    value = value.strip()

    if not value:
        return ""

    try:
        number = float(value)

        if number.is_integer():
            return str(int(number))

        return format(
            number,
            ".10g",
        )
    except ValueError:
        return value


def parse_mcp_text(
    content: Any,
) -> list[list[str]]:
    """
    把 MCP 返回的 CSV 风格文本解析为二维表。

    第一行通常是列名。
    返回时去掉列名，仅保留数据行，
    从而允许模型使用不同 SQL alias。
    """

    if isinstance(content, list):
        texts: list[str] = []

        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "text"
            ):
                text = block.get("text")

                if isinstance(text, str):
                    texts.append(text)

        raw = "\n".join(texts)

    elif isinstance(content, str):
        raw = content

    else:
        raw = str(content)

    raw = raw.strip()

    if not raw:
        return []

    rows = list(
        csv.reader(
            raw.splitlines()
        )
    )

    if len(rows) <= 1:
        return []

    data_rows = rows[1:]

    return [
        [
            normalize_cell(cell)
            for cell in row
        ]
        for row in data_rows
    ]


def extract_execute_sql_call(
    messages: list[Any],
) -> dict[str, Any] | None:
    """
    找最后一个 execute_sql 工具调用。

    返回：
    {
        "id": ...,
        "query": ...
    }
    """

    found: dict[str, Any] | None = None

    for message in messages:
        if not isinstance(
            message,
            AIMessage,
        ):
            continue

        for call in (
            message.tool_calls or []
        ):
            if call.get("name") != "execute_sql":
                continue

            args = call.get("args") or {}

            found = {
                "id": call.get("id"),
                "query": args.get("query"),
            }

    return found


def extract_execute_result(
    messages: list[Any],
    tool_call_id: str | None,
) -> ToolMessage | None:
    """
    查找与 execute_sql 调用匹配的 ToolMessage。
    """

    if not tool_call_id:
        return None

    result: ToolMessage | None = None

    for message in messages:
        if not isinstance(
            message,
            ToolMessage,
        ):
            continue

        if message.name != "execute_sql":
            continue

        if (
            message.tool_call_id
            != tool_call_id
        ):
            continue

        result = message

    return result


def answer_contains_result(
    answer: str,
    gold_rows: list[list[str]],
) -> bool:
    """
    粗粒度检查最终自然语言回答是否包含
    gold result 中的重要值。

    数据库结果匹配是主要指标；
    此指标用于检测：
    SQL 查询正确，但最终回答把数字说错。

    数值允许千分位逗号差异。
    """

    if not gold_rows:
        return True

    answer_lower = answer.lower()

    flattened: list[str] = []

    for row in gold_rows:
        for value in row:
            if value:
                flattened.append(value)

    # 分组结果可能很多。
    # 第一轮真实评估全部检查。
    for value in flattened:
        normalized_value = value.lower()

        if normalized_value in answer_lower:
            continue

        if re.fullmatch(
            r"-?\d+(?:\.\d+)?",
            value,
        ):
            comma_version = value

            try:
                numeric = float(value)

                if numeric.is_integer():
                    comma_version = (
                        f"{int(numeric):,}"
                    )
            except ValueError:
                pass

            if comma_version in answer:
                continue

        return False

    return True


async def build_gold_tool():
    """
    构建与 SQLAgent 同样受保护的真实 SQL 工具。

    Gold SQL 同样经过 read-only guard，
    不绕过安全层。
    """

    raw_tools = await MySQLMCPClient(
        transport="stdio"
    ).get_tools()

    guarded = build_readonly_sql_tools(
        raw_tools
    )

    return next(
        tool
        for tool in guarded
        if tool.name == "execute_sql"
    )


async def run_gold_query(
    tool,
    case_id: str,
    sql: str,
) -> tuple[Any, list[list[str]]]:
    result = await tool.ainvoke({
        "type": "tool_call",
        "id": f"gold-{case_id}",
        "name": "execute_sql",
        "args": {
            "query": sql,
        },
    })

    if not isinstance(
        result,
        ToolMessage,
    ):
        raise RuntimeError(
            f"{case_id}: gold SQL "
            "did not return ToolMessage"
        )

    if result.status != "success":
        raise RuntimeError(
            f"{case_id}: gold SQL failed: "
            f"{result.content}"
        )

    rows = parse_mcp_text(
        result.content
    )

    return result.content, rows


async def run_agent_case(
    agent: SQLAgent,
    case: dict[str, Any],
    gold_rows: list[list[str]],
) -> dict[str, Any]:
    question = str(
        case["question"]
    )

    try:
        state = await agent._invoke_agent(
            question
        )

        messages = state.get(
            "messages",
            [],
        )

        if not isinstance(
            messages,
            list,
        ):
            messages = list(messages)

        evidence = inspect_sql_evidence(
            messages
        )

        call = extract_execute_sql_call(
            messages
        )

        tool_result = (
            extract_execute_result(
                messages,
                call.get("id")
                if call
                else None,
            )
        )

        actual_rows: list[list[str]] = []

        if tool_result is not None:
            actual_rows = parse_mcp_text(
                tool_result.content
            )

        result_match = (
            actual_rows == gold_rows
        )

        answer = str(
            evidence.get(
                "answer",
                "",
            )
            or ""
        )

        answer_match = (
            answer_contains_result(
                answer,
                gold_rows,
            )
        )

        query_verified = (
            evidence.get(
                "query_verified"
            )
            is True
        )

        overall_correct = (
            evidence.get("status")
            == "success"
            and query_verified
            and result_match
            and answer_match
        )

        return {
            "status": evidence.get(
                "status"
            ),
            "query_verified": (
                query_verified
            ),
            "generated_sql": (
                call.get("query")
                if call
                else None
            ),
            "tool_call_id": (
                call.get("id")
                if call
                else None
            ),
            "tool_result_status": (
                tool_result.status
                if tool_result
                else None
            ),
            "actual_rows": (
                actual_rows
            ),
            "result_match": (
                result_match
            ),
            "answer": answer,
            "answer_match": (
                answer_match
            ),
            "overall_correct": (
                overall_correct
            ),
            "error": evidence.get(
                "error"
            ),
        }

    except Exception as exc:
        return {
            "status": "error",
            "query_verified": False,
            "generated_sql": None,
            "tool_call_id": None,
            "tool_result_status": None,
            "actual_rows": [],
            "result_match": False,
            "answer": "",
            "answer_match": False,
            "overall_correct": False,
            "error": (
                f"{type(exc).__name__}: "
                f"{exc}"
            ),
        }


async def main():
    require_safe_environment()

    cases = load_cases()

    timestamp = datetime.now().strftime(
        "%Y%m%d-%H%M%S"
    )

    result_dir = (
        RESULT_ROOT
        / f"sql_v3_real_{timestamp}"
    )

    result_dir.mkdir(
        parents=True,
        exist_ok=False,
    )

    print(
        "=== SQLAgent V3 REAL EVALUATION ==="
    )

    print(
        "Cases:",
        len(cases),
    )

    print(
        "MySQL user:",
        os.getenv("MYSQL_USER"),
    )

    print(
        "Database:",
        os.getenv("MYSQL_DATABASE"),
    )

    print(
        "MCP transport:",
        os.getenv("MYSQL_MCP_TRANSPORT"),
    )

    print(
        "LLM model:",
        os.getenv("LLM_MODEL"),
    )

    gold_tool = await build_gold_tool()

    agent = SQLAgent()

    # V3 评估专用提示词。
    # 不修改历史默认 sql_agent.yaml。
    agent.system_prompt = load_yaml(
        PROMPT_PATH
    )["system_prompt"]

    records: list[dict[str, Any]] = []

    for index, case in enumerate(
        cases,
        start=1,
    ):
        case_id = str(
            case["id"]
        )

        print()
        print(
            f"[{index}/{len(cases)}] "
            f"{case_id}"
        )

        print(
            "Question:",
            case["question"],
        )

        gold_content, gold_rows = (
            await run_gold_query(
                gold_tool,
                case_id,
                str(case["gold_sql"]),
            )
        )

        print(
            "Gold rows:",
            gold_rows,
        )

        agent_result = (
            await run_agent_case(
                agent,
                case,
                gold_rows,
            )
        )

        record = {
            "id": case_id,
            "category": (
                case.get("category")
            ),
            "question": (
                case["question"]
            ),
            "gold_sql": (
                case["gold_sql"]
            ),
            "gold_content": (
                gold_content
            ),
            "gold_rows": (
                gold_rows
            ),
            **agent_result,
        }

        records.append(
            record
        )

        print(
            "Generated SQL:",
            record["generated_sql"],
        )

        print(
            "Evidence status:",
            record["status"],
        )

        print(
            "Query verified:",
            record["query_verified"],
        )

        print(
            "Result match:",
            record["result_match"],
        )

        print(
            "Answer match:",
            record["answer_match"],
        )

        print(
            "Overall:",
            (
                "PASS"
                if record[
                    "overall_correct"
                ]
                else "FAIL"
            ),
        )

        if record["error"]:
            print(
                "Error:",
                record["error"],
            )

    total = len(records)

    evidence_success = sum(
        1
        for item in records
        if item["query_verified"]
    )

    result_correct = sum(
        1
        for item in records
        if item["result_match"]
    )

    answer_correct = sum(
        1
        for item in records
        if item["answer_match"]
    )

    overall_correct = sum(
        1
        for item in records
        if item["overall_correct"]
    )

    summary = {
        "evaluation": (
            "SQLAgent V3 Real Olist"
        ),
        "timestamp": timestamp,
        "total_cases": total,
        "query_verified_cases": (
            evidence_success
        ),
        "result_correct_cases": (
            result_correct
        ),
        "answer_correct_cases": (
            answer_correct
        ),
        "overall_correct_cases": (
            overall_correct
        ),
        "query_verified_rate": (
            evidence_success / total
            if total
            else 0.0
        ),
        "result_accuracy": (
            result_correct / total
            if total
            else 0.0
        ),
        "answer_fidelity": (
            answer_correct / total
            if total
            else 0.0
        ),
        "overall_accuracy": (
            overall_correct / total
            if total
            else 0.0
        ),
        "mysql_user": (
            os.getenv("MYSQL_USER")
        ),
        "database": (
            os.getenv(
                "MYSQL_DATABASE"
            )
        ),
        "mcp_transport": (
            os.getenv(
                "MYSQL_MCP_TRANSPORT"
            )
        ),
        "llm_model": (
            os.getenv("LLM_MODEL")
        ),
    }

    results_path = (
        result_dir
        / "case_results.json"
    )

    summary_path = (
        result_dir
        / "summary.json"
    )

    with results_path.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            records,
            f,
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    with summary_path.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            summary,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print()
    print(
        "=== SUMMARY ==="
    )

    print(
        f"Query verified: "
        f"{evidence_success}/{total}"
    )

    print(
        f"DB result correct: "
        f"{result_correct}/{total}"
    )

    print(
        f"Answer fidelity: "
        f"{answer_correct}/{total}"
    )

    print(
        f"Overall correct: "
        f"{overall_correct}/{total}"
    )

    print(
        "Results:",
        results_path,
    )

    print(
        "Summary:",
        summary_path,
    )


if __name__ == "__main__":
    asyncio.run(main())