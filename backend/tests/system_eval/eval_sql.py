
"""
SQLAgent V1：SQL Node 控制逻辑及风险探测评估。

真实组件：
    core.graph.nodes.sql_node.build_sql_node

模拟组件：
    SQLAgent.query

不调用真实 LLM、MCP 或 MySQL。
不修改业务代码。

运行：
    python -m tests.system_eval.eval_sql
"""

from __future__ import annotations

import asyncio
import csv
import json
import time
from datetime import datetime
from pathlib import Path

from core.graph.nodes.sql_node import build_sql_node


ROOT = Path(__file__).resolve().parent
CASE_FILE = ROOT / "cases" / "sql_cases.json"
RESULT_DIR = ROOT / "results" / "sql_v1"


class FakeSQLAgent:
    """
    返回预设结果，同时记录模拟的工具执行证据。

    注意：
    当前 SQL Node 只接收 query() 返回的 str。
    tool_evidence_available 是测试夹具的记录，
    不是 SQL Node 当前能够访问的结构化字段。
    """

    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.calls: list[str] = []
        self.tool_evidence_available = False

    async def query(self, question: str) -> str:
        self.calls.append(question)

        if self.scenario == "sql_success":
            self.tool_evidence_available = True
            return "SKU-001 的查询数量为 42。"

        if self.scenario == "empty_result":
            self.tool_evidence_available = False
            return ""

        if self.scenario == "raised_exception":
            self.tool_evidence_available = False
            raise RuntimeError("数据库连接失败")

        if self.scenario == "connection_error_text":
            self.tool_evidence_available = False
            return "数据库连接失败，无法获得查询结果。"

        if self.scenario == "missing_table_text":
            self.tool_evidence_available = False
            return "SQL执行失败：表 inventory 不存在。"

        if self.scenario == "no_tool_evidence":
            self.tool_evidence_available = False
            return "SKU-001 当前数量为 42。"

        if self.scenario == "verified_empty_query":
            self.tool_evidence_available = True
            return "查询成功，结果为0条记录。"

        if self.scenario == "empty_query":
            raise AssertionError(
                "空用户任务不得调用 SQLAgent。"
            )

        raise ValueError(
            f"未知测试场景：{self.scenario}"
        )


def build_state(scenario: str) -> dict:
    """构造受控输入状态。"""

    if scenario == "empty_query":
        return {
            "user_query": "",
            "execution_trace": [],
        }

    if scenario == "sql_success":
        question = "查询 SKU-001 的数量"
    elif scenario == "empty_result":
        question = "查询最近订单"
    elif scenario == "raised_exception":
        question = "查询当前库存"
    elif scenario == "connection_error_text":
        question = "读取库存数据"
    elif scenario == "missing_table_text":
        question = "查询 inventory 表的数据"
    elif scenario == "no_tool_evidence":
        question = "查询 SKU-001 当前数量"
    elif scenario == "verified_empty_query":
        question = "查询不存在的订单编号"
    else:
        raise ValueError(
            f"未知场景：{scenario}"
        )

    return {
        "user_query": question,
        "execution_trace": [],
    }


async def evaluate_case(case: dict) -> dict:
    """运行一项真实 SQL Node 评估。"""

    started = time.perf_counter()
    scenario = case["scenario"]

    agent = FakeSQLAgent(scenario)
    node = build_sql_node(agent)

    expected_status = case["expected_status"]
    expected_calls = case["expected_agent_calls"]

    problems = []
    actual_status = ""
    actual_result = ""
    actual_error = ""
    final_status = ""
    execution_trace = None

    try:
        state = build_state(scenario)
        output = await node(state)

        actual_status = str(
            output.get("sql_status") or ""
        )

        actual_result = str(
            output.get("sql_result") or ""
        )

        actual_error = str(
            output.get("sql_error") or ""
        )

        final_status = str(
            output.get("status") or ""
        )

        execution_trace = output.get(
            "execution_trace"
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
                    "查询结果没有按预期传递。"
                )

        expected_error = case.get(
            "expected_error"
        )

        if expected_error is not None:
            if actual_error != expected_error:
                problems.append(
                    "异常信息没有按预期传递。"
                )

        if execution_trace != ["sql"]:
            problems.append(
                "execution_trace 不正确。"
            )

        if actual_status == "success":
            if final_status != "running":
                problems.append(
                    "成功查询未进入 running 状态。"
                )

            if not actual_result.strip():
                problems.append(
                    "成功状态下缺少查询结果说明。"
                )

            if actual_error:
                problems.append(
                    "成功状态包含 sql_error。"
                )

            if not agent.tool_evidence_available:
                problems.append(
                    "SQL Node 标记查询成功，"
                    "但测试夹具未提供工具成功证据。"
                )

        elif actual_status == "error":
            if final_status != "failed":
                problems.append(
                    "错误状态未标记为 failed。"
                )

            if not actual_error:
                problems.append(
                    "错误状态缺少 sql_error。"
                )

        passed = len(problems) == 0

    except Exception as exc:
        passed = False
        actual_status = "exception"
        problems.append(
            f"{type(exc).__name__}: {exc}"
        )

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
            agent.tool_evidence_available
        ),
        "actual_result": actual_result,
        "actual_error": actual_error,
        "node_status": final_status,
        "passed": passed,
        "elapsed_seconds": round(
            time.perf_counter() - started, 6
        ),
        "detail": (
            "; ".join(problems)
            if problems else "符合预期"
        ),
    }


def calculate_rate(rows: list[dict]) -> float | None:
    if not rows:
        return None

    return round(
        sum(int(row["passed"]) for row in rows)
        / len(rows),
        4,
    )


async def run_evaluation(cases: list[dict]) -> list[dict]:
    results = []

    for case in cases:
        result = await evaluate_case(case)
        results.append(result)

        label = (
            "PASS" if result["passed"]
            else "FAIL"
        )

        print(
            f"[{label}] "
            f"{result['case_id']} "
            f"{result['name']}"
        )

        if not result["passed"]:
            print(
                "  detail="
                + result["detail"]
            )

    return results


def main() -> None:
    if not CASE_FILE.is_file():
        raise FileNotFoundError(
            f"案例文件不存在：{CASE_FILE}"
        )

    config = json.loads(
        CASE_FILE.read_text(encoding="utf-8")
    )

    cases = config["cases"]

    expected_ids = [
        "SQ001", "SQ002", "SQ003", "SQ004",
        "SQ005", "SQ006", "SQ007", "SQ008",
    ]

    actual_ids = [
        case["case_id"] for case in cases
    ]

    if actual_ids != expected_ids:
        raise ValueError(
            "SQL V1 固定案例集合或顺序错误。"
        )

    if RESULT_DIR.exists():
        raise FileExistsError(
            "SQL V1 结果目录已存在，"
            "拒绝覆盖历史评估结果："
            f"{RESULT_DIR}"
        )

    print("=== SQLAgent Evaluation V1 ===")
    print("真实 SQL Node：是")
    print("真实 SQLAgent：否")
    print("真实 LLM：否")
    print("真实 MCP / MySQL：否")
    print()

    results = asyncio.run(
        run_evaluation(cases)
    )

    total = len(results)
    passed = sum(
        int(row["passed"])
        for row in results
    )

    regular = [
        row for row in results
        if not row["risk_probe"]
    ]

    risk = [
        row for row in results
        if row["risk_probe"]
    ]

    summary = {
        "evaluation_id": config["evaluation_id"],
        "evaluated_at": datetime.now().isoformat(),
        "evaluation_type": config["evaluation_type"],
        "total_cases": total,
        "passed_cases": passed,
        "failed_cases": total - passed,
        "overall_pass_rate": calculate_rate(results),
        "regular_cases": len(regular),
        "regular_pass_rate": calculate_rate(regular),
        "risk_probe_cases": len(risk),
        "risk_probe_pass_rate": calculate_rate(risk),
        "real_sql_node_used": True,
        "real_sql_agent_used": False,
        "real_llm_used": False,
        "real_database_used": False,
        "limitations": [
            "SQL Node only receives a string from SQLAgent",
            "Tool evidence is controlled by the test fixture",
            "No actual MCP tools or SQL statements are executed",
            "Passing cases do not establish database correctness",
            "Failure of risk probes does not establish real LLM hallucination"
        ],
    }

    # 全部执行完成后再保存结果，避免覆盖历史。
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
            fieldnames=list(results[0].keys()),
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

    print("\n=== SQL V1 Summary ===")
    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        )
    )

    print("\n结果目录：", RESULT_DIR)


if __name__ == "__main__":
    main()