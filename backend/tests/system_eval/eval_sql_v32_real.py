"""
SQLAgent V3.2 Real Evaluation

在 SQL V3 基础上提升查询难度：

1. 日期范围过滤
2. 多条件 WHERE
3. TOP-N
4. NULL
5. 数值区间
6. 条件聚合
7. 空结果

真实组件：
- GLM-5.2
- SQLAgent
- SQL read-only guard
- STDIO MCP
- olist_eval_reader
- Olist MySQL

不会覆盖 SQL V3 / V3.1.1 历史结果。

运行：

python -m tests.system_eval.eval_sql_v32_real
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from core.agents.sql_agent import SQLAgent
from core.mcp.mysql_client import MySQLMCPClient
from core.tools.sql_evidence import inspect_sql_evidence
from core.tools.sql_mcp_guard import build_readonly_sql_tools
from core.utils.yaml_loader import load_yaml

from tests.system_eval.audit_sql_v311_fidelity import (
    audit_group_by,
    audit_single_value,
)

from tests.system_eval.eval_sql_v3_real import (
    extract_execute_result,
    extract_execute_sql_call,
    parse_mcp_text,
)


ROOT = Path(__file__).resolve().parents[2]

CASES_PATH = (
    ROOT
    / "tests"
    / "system_eval"
    / "cases"
    / "sql_v32_real_cases.json"
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
    expected = {
        "MYSQL_USER": "olist_eval_reader",
        "MYSQL_DATABASE": "olist_logistics_eval",
        "MYSQL_MCP_TRANSPORT": "stdio",
        "LLM_MODEL": "glm-5.2",
    }

    problems: list[str] = []

    for key, expected_value in expected.items():
        actual = os.getenv(key)

        if actual != expected_value:
            problems.append(
                f"{key}: expected={expected_value!r}, "
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
            "Unsafe or incomplete environment:\n- "
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
            "V3.2 case file must contain a list."
        )

    return cases


async def build_gold_tool():
    raw_tools = await MySQLMCPClient(
        transport="stdio"
    ).get_tools()

    guarded_tools = build_readonly_sql_tools(
        raw_tools
    )

    return next(
        tool
        for tool in guarded_tools
        if tool.name == "execute_sql"
    )


async def run_gold_query(
    tool,
    case_id: str,
    sql: str,
) -> tuple[Any, list[list[str]]]:

    result = await tool.ainvoke({
        "type": "tool_call",
        "id": f"v32-gold-{case_id}",
        "name": "execute_sql",
        "args": {
            "query": sql,
        },
    })

    if result.status != "success":
        raise RuntimeError(
            f"{case_id}: gold SQL failed: "
            f"{result.content}"
        )

    rows = parse_mcp_text(
        result.content
    )

    return (
        result.content,
        rows,
    )


def audit_answer(
    *,
    category: str,
    gold_rows: list[list[str]],
    actual_rows: list[list[str]],
    answer: str,
    result_match: bool,
) -> tuple[bool, list[str]]:

    audit_case = {
        "category": category,
        "gold_rows": gold_rows,
        "actual_rows": actual_rows,
        "answer": answer,
        "result_match": result_match,
    }

    if not result_match:
        return (
            False,
            [
                "Database result does not "
                "match gold result."
            ],
        )

    if category == "group_by":
        return audit_group_by(
            audit_case
        )

    return audit_single_value(
        audit_case
    )


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
                (
                    call.get("id")
                    if call
                    else None
                ),
            )
        )

        actual_rows: list[list[str]] = []

        if tool_result is not None:
            actual_rows = parse_mcp_text(
                tool_result.content
            )

        result_match = (
            actual_rows
            == gold_rows
        )

        answer = str(
            evidence.get(
                "answer",
                "",
            )
            or ""
        )

        fidelity_pass, problems = (
            audit_answer(
                category=str(
                    case.get(
                        "category",
                        "",
                    )
                ),
                gold_rows=gold_rows,
                actual_rows=actual_rows,
                answer=answer,
                result_match=result_match,
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
            and fidelity_pass
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
            "fidelity_pass": (
                fidelity_pass
            ),
            "fidelity_problems": (
                problems
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
            "fidelity_pass": False,
            "fidelity_problems": [],
            "overall_correct": False,
            "error": (
                f"{type(exc).__name__}: "
                f"{exc}"
            ),
        }


async def main() -> None:
    require_safe_environment()

    cases = load_cases()

    timestamp = datetime.now().strftime(
        "%Y%m%d-%H%M%S"
    )

    result_dir = (
        RESULT_ROOT
        / f"sql_v32_real_{timestamp}"
    )

    result_dir.mkdir(
        parents=True,
        exist_ok=False,
    )

    print(
        "=== SQLAgent V3.2 "
        "REAL EVALUATION ==="
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

        try:
            gold_content, gold_rows = (
                await run_gold_query(
                    gold_tool,
                    case_id,
                    str(
                        case["gold_sql"]
                    ),
                )
            )

        except Exception as exc:
            print(
                "GOLD SQL FAILED:"
            )

            print(
                type(exc).__name__,
                str(exc),
            )

            raise

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
            record[
                "generated_sql"
            ],
        )

        print(
            "Evidence status:",
            record["status"],
        )

        print(
            "Query verified:",
            record[
                "query_verified"
            ],
        )

        print(
            "Result match:",
            record[
                "result_match"
            ],
        )

        print(
            "Answer fidelity:",
            record[
                "fidelity_pass"
            ],
        )

        if record[
            "fidelity_problems"
        ]:
            for problem in record[
                "fidelity_problems"
            ]:
                print(
                    "  Fidelity issue:",
                    problem,
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

    query_verified_count = sum(
        1
        for item in records
        if item[
            "query_verified"
        ]
    )

    result_correct_count = sum(
        1
        for item in records
        if item[
            "result_match"
        ]
    )

    fidelity_count = sum(
        1
        for item in records
        if item[
            "fidelity_pass"
        ]
    )

    overall_count = sum(
        1
        for item in records
        if item[
            "overall_correct"
        ]
    )

    summary = {
        "evaluation": (
            "SQLAgent V3.2 "
            "Real Olist"
        ),
        "timestamp": timestamp,
        "total_cases": total,
        "query_verified_cases": (
            query_verified_count
        ),
        "result_correct_cases": (
            result_correct_count
        ),
        "fidelity_pass_cases": (
            fidelity_count
        ),
        "overall_correct_cases": (
            overall_count
        ),
        "query_verified_rate": (
            query_verified_count
            / total
            if total
            else 0.0
        ),
        "result_accuracy": (
            result_correct_count
            / total
            if total
            else 0.0
        ),
        "answer_fidelity": (
            fidelity_count
            / total
            if total
            else 0.0
        ),
        "overall_accuracy": (
            overall_count
            / total
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
        "Query verified:",
        f"{query_verified_count}/{total}",
    )

    print(
        "DB result correct:",
        f"{result_correct_count}/{total}",
    )

    print(
        "Answer fidelity:",
        f"{fidelity_count}/{total}",
    )

    print(
        "Overall correct:",
        f"{overall_count}/{total}",
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