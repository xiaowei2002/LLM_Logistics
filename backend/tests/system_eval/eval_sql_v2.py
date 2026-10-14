
"""
SQL V2：SQL Node 与工具证据校验的八案例评估。

复用：
    tests/system_eval/cases/sql_cases.json

真实组件：
    build_sql_node
    inspect_sql_evidence

模拟组件：
    SQLAgent.query_with_evidence 的执行边界
    LangChain AIMessage / ToolMessage

不调用：
    真实 LLM、MCP Server、MySQL

结果：
    tests/system_eval/results/sql_v2_evidence/

不会覆盖已有结果目录。

运行：
    python -m tests.system_eval.eval_sql_v2
"""

from __future__ import annotations

import asyncio
import csv
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from core.graph.nodes.sql_node import build_sql_node
from core.tools.sql_evidence import inspect_sql_evidence


ROOT = Path(__file__).resolve().parent
CASE_FILE = ROOT / "cases" / "sql_cases.json"
RESULT_DIR = ROOT / "results" / "sql_v2_evidence"

EXPECTED_IDS = [
    f"SQ{i:03d}" for i in range(1, 9)
]


def tool_call(
    call_id: str = "sql-call-001",
) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "execute_sql",
                "args": {
                    "query": "SELECT quantity FROM inventory",
                },
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def tool_result(
    content: str,
    *,
    status: str = "success",
    call_id: str = "sql-call-001",
) -> ToolMessage:
    return ToolMessage(
        content=content,
        name="execute_sql",
        tool_call_id=call_id,
        status=status,
    )


def final_answer(content: str) -> AIMessage:
    return AIMessage(content=content)


def build_messages(
    scenario: str,
) -> list[Any]:
    """
    构造各场景的受控 LangChain 消息。

    不伪造真实数据库已执行这一事实：
    这里所有工具消息均为测试夹具。
    """

    if scenario == "sql_success":
        return [
            tool_call(),
            tool_result(
                '[{"sku":"SKU-001","quantity":42}]'
            ),
            final_answer(
                "SKU-001 的查询数量为 42。"
            ),
        ]

    if scenario == "empty_result":
        return [
            tool_call(),
            tool_result(
                '[{"quantity":42}]'
            ),
            final_answer(""),
        ]

    if scenario == "connection_error_text":
        return [
            tool_call(),
            tool_result(
                "数据库连接失败，无法获得查询结果。",
                status="error",
            ),
            final_answer(
                "数据库连接失败，无法获得查询结果。"
            ),
        ]

    if scenario == "missing_table_text":
        return [
            tool_call(),
            tool_result(
                json.dumps(
                    {
                        "status": "error",
                        "error": (
                            "SQL执行失败："
                            "表 inventory 不存在。"
                        ),
                    },
                    ensure_ascii=False,
                )
            ),
            final_answer(
                "SQL执行失败：表 inventory 不存在。"
            ),
        ]

    if scenario == "no_tool_evidence":
        return [
            final_answer(
                "SKU-001 当前数量为 42。"
            )
        ]

    if scenario == "verified_empty_query":
        return [
            tool_call(),
            tool_result("[]"),
            final_answer(
                "查询成功，结果为0条记录。"
            ),
        ]

    raise ValueError(
        f"无法构造消息的场景：{scenario}"
    )


class FakeEvidenceSQLAgent:
    """
    V2 结构化接口测试替身。

    与 V1 的区别：
    不使用人工赋值的 tool_evidence_available
    直接决定成功状态，而是调用真实的
    inspect_sql_evidence() 处理模拟工具轨迹。
    """

    def __init__(self, scenario: str):
        self.scenario = scenario
        self.calls: list[str] = []
        self.last_evidence: dict[str, Any] | None = None

    async def query_with_evidence(
        self,
        question: str,
    ) -> dict[str, Any]:
        self.calls.append(question)

        if self.scenario == "raised_exception":
            raise RuntimeError("数据库连接失败")

        if self.scenario == "empty_query":
            raise AssertionError(
                "空用户请求不应调用 SQLAgent。"
            )

        messages = build_messages(self.scenario)

        evidence = inspect_sql_evidence(
            messages
        )

        self.last_evidence = evidence
        return evidence


def build_state(scenario: str) -> dict[str, Any]:
    """复用 V1 场景的用户查询输入。"""

    questions = {
        "empty_query": "",
        "sql_success": "查询 SKU-001 的数量",
        "empty_result": "查询最近订单",
        "raised_exception": "查询当前库存",
        "connection_error_text": "读取库存数据",
        "missing_table_text": "查询 inventory 表的数据",
        "no_tool_evidence": "查询 SKU-001 当前数量",
        "verified_empty_query": "查询不存在的订单编号",
    }

    if scenario not in questions:
        raise ValueError(
            f"未知场景：{scenario}"
        )

    return {
        "user_query": questions[scenario],
        "execution_trace": [],
        "status": "running",
    }


async def evaluate_case(
    case: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()

    scenario = case["scenario"]
    agent = FakeEvidenceSQLAgent(scenario)
    node = build_sql_node(agent)

    expected_status = case["expected_status"]
    expected_calls = case["expected_agent_calls"]

    problems: list[str] = []
    output: dict[str, Any] = {}

    try:
        output = await node(
            build_state(scenario)
        )

        actual_status = output.get(
            "sql_status"
        )
        actual_result = output.get(
            "sql_result"
        )
        actual_error = output.get(
            "sql_error"
        )
        node_status = output.get(
            "status"
        )

        if actual_status != expected_status:
            problems.append(
                "SQL 状态不匹配："
                f"expected={expected_status}, "
                f"actual={actual_status}"
            )

        if len(agent.calls) != expected_calls:
            problems.append(
                "SQLAgent 调用次数不匹配："
                f"expected={expected_calls}, "
                f"actual={len(agent.calls)}"
            )

        expected_result = case.get(
            "expected_result"
        )

        if expected_result is not None:
            if actual_result != expected_result:
                problems.append(
                    "查询结果未按预期传递。"
                )

        expected_error = case.get(
            "expected_error"
        )

        if expected_error is not None:
            # V2 节点保留异常类型前缀，
            # 因此检查原始错误原因必须存在。
            if expected_error not in str(
                actual_error or ""
            ):
                problems.append(
                    "异常原因未按预期传递。"
                )

        if output.get(
            "execution_trace"
        ) != ["sql"]:
            problems.append(
                "execution_trace 不正确。"
            )

        if actual_status == "success":
            if node_status != "running":
                problems.append(
                    "成功时状态不是 running。"
                )

            if not isinstance(
                actual_result, str
            ) or not actual_result.strip():
                problems.append(
                    "成功时缺少查询结果。"
                )

            if actual_error not in (None, ""):
                problems.append(
                    "成功状态存在 sql_error。"
                )

            evidence = agent.last_evidence

            if not evidence:
                problems.append(
                    "缺少查询证据记录。"
                )
            elif (
                evidence.get("status") != "success"
                or evidence.get(
                    "query_verified"
                ) is not True
                or evidence.get(
                    "tool_name"
                ) != "execute_sql"
                or not evidence.get(
                    "tool_call_id"
                )
            ):
                problems.append(
                    "SQL 成功但缺少有效执行证据。"
                )

        elif actual_status == "error":
            if node_status != "failed":
                problems.append(
                    "错误状态未进入 failed。"
                )

            if not str(
                actual_error or ""
            ).strip():
                problems.append(
                    "错误状态缺少 sql_error。"
                )

            if actual_result not in ("", None):
                problems.append(
                    "失败结果没有清空 sql_result。"
                )

        passed = not problems

    except Exception as exc:
        passed = False
        problems.append(
            f"{type(exc).__name__}: {exc}"
        )
        actual_status = "exception"
        actual_result = ""
        actual_error = str(exc)
        node_status = "exception"

    return {
        "case_id": case["case_id"],
        "name": case["name"],
        "scenario": scenario,
        "risk_probe": bool(
            case.get("risk_probe", False)
        ),
        "expected_status": expected_status,
        "actual_status": actual_status,
        "expected_agent_calls": expected_calls,
        "actual_agent_calls": len(agent.calls),
        "tool_evidence_in_fixture": (
            agent.last_evidence is not None
            and agent.last_evidence.get(
                "query_verified"
            ) is True
        ),
        "actual_result": actual_result or "",
        "actual_error": actual_error or "",
        "node_status": node_status,
        "passed": passed,
        "elapsed_seconds": round(
            time.perf_counter() - started,
            6,
        ),
        "detail": (
            "; ".join(problems)
            if problems
            else "符合预期"
        ),
    }


def pass_rate(
    rows: list[dict[str, Any]],
) -> float | None:
    if not rows:
        return None

    return round(
        sum(
            bool(row["passed"])
            for row in rows
        ) / len(rows),
        4,
    )


async def run_evaluation(
    cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    results = []

    for case in cases:
        result = await evaluate_case(case)
        results.append(result)

        label = (
            "PASS"
            if result["passed"]
            else "FAIL"
        )

        print(
            f"[{label}] "
            f"{result['case_id']} "
            f"{result['name']}"
        )

        if not result["passed"]:
            print(
                "  detail=" + result["detail"]
            )

    return results


def main() -> None:
    if not CASE_FILE.is_file():
        raise FileNotFoundError(
            f"缺少案例文件：{CASE_FILE}"
        )

    if RESULT_DIR.exists():
        raise FileExistsError(
            "SQL V2 结果目录已存在，"
            "拒绝覆盖："
            f"{RESULT_DIR}"
        )

    config = json.loads(
        CASE_FILE.read_text(
            encoding="utf-8"
        )
    )

    cases = config["cases"]

    actual_ids = [
        case["case_id"]
        for case in cases
    ]

    if actual_ids != EXPECTED_IDS:
        raise ValueError(
            "SQL 八案例编号或顺序发生变化。"
        )

    print("=== SQL Evaluation V2 ===")
    print("真实 SQL Node：是")
    print("真实工具证据校验器：是")
    print("工具消息：模拟")
    print("真实 SQLAgent / LLM：否")
    print("真实 MCP / MySQL：否")
    print()

    results = asyncio.run(
        run_evaluation(cases)
    )

    regular = [
        row for row in results
        if not row["risk_probe"]
    ]

    risk = [
        row for row in results
        if row["risk_probe"]
    ]

    total = len(results)
    passed = sum(
        bool(row["passed"])
        for row in results
    )

    summary = {
        "evaluation_id": "sql_control_v2",
        "source_case_file": str(
            CASE_FILE.relative_to(ROOT)
        ),
        "source_evaluation_id": config[
            "evaluation_id"
        ],
        "evaluated_at": (
            datetime.now().astimezone().isoformat()
        ),
        "evaluation_type": (
            "real_sql_node_with_simulated_"
            "langchain_tool_messages"
        ),
        "total_cases": total,
        "passed_cases": passed,
        "failed_cases": total - passed,
        "overall_pass_rate": pass_rate(results),
        "regular_cases": len(regular),
        "regular_pass_rate": pass_rate(regular),
        "risk_probe_cases": len(risk),
        "risk_probe_pass_rate": pass_rate(risk),
        "real_sql_node_used": True,
        "real_evidence_inspector_used": True,
        "real_sql_agent_used": False,
        "real_llm_used": False,
        "real_database_used": False,
        "limitations": [
            "All LangChain tool messages are simulated.",
            "The real SQLAgent LLM loop is not invoked.",
            "The actual MySQL MCP response schema is not verified.",
            "Passing cases do not establish SQL correctness.",
            "Passing cases do not establish factual answer fidelity.",
            "The database account permissions are not verified.",
            "SQL parser and tool error coverage remain incomplete.",
        ],
    }

    # 所有案例结束后才创建独立结果目录。
    RESULT_DIR.mkdir(
        parents=True,
        exist_ok=False,
    )

    results_file = (
        RESULT_DIR / "sql_case_results.csv"
    )

    with results_file.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=list(
                results[0].keys()
            ),
        )
        writer.writeheader()
        writer.writerows(results)

    summary_file = (
        RESULT_DIR / "sql_summary.json"
    )

    summary_file.write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n=== SQL V2 Summary ===")
    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        )
    )

    print(
        "\n结果目录：",
        RESULT_DIR,
    )


if __name__ == "__main__":
    main()
