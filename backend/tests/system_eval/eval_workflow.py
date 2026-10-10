
"""
Workflow 系统评估 V1。

评估对象：
- LangGraph 真实路由逻辑
- Cleaning Quality Gate
- HITL 触发与恢复
- 无效任务拦截
- Workflow 执行耗时

测试 Agent 使用 Fake Node，不访问真实 LLM、MCP 或数据库。

运行：
    python -m tests.system_eval.eval_workflow
"""

from __future__ import annotations

import asyncio
import csv
import json
import tempfile
import time
from pathlib import Path
from typing import Any

from core.graph.workflow import build_logistics_workflow


BASE_DIR = Path(__file__).resolve().parent
CASES_PATH = BASE_DIR / "cases" / "workflow_cases.json"
RESULT_DIR = BASE_DIR / "results"


# --------------------------------------------------
# 1. 测试数据
# --------------------------------------------------

HEADER = "order_no,sku,qty,warehouse,created_at_raw"


def create_csv(path: Path, count: int) -> None:
    lines = [HEADER]

    for i in range(count):
        lines.append(
            f"SO{i + 1:04d},SKU-{i + 1:03d},"
            f"{i + 1},WH-A,2026-09-01"
        )

    path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


# --------------------------------------------------
# 2. 构建 Fake Agent
# --------------------------------------------------

def create_fake_nodes(scenario: str, counter: dict):

    async def fake_sql(state):
        return {
            "current_agent": "sql",
            "sql_result": "查询完成",
            "sql_status": "success",
            "status": "running",
            "execution_trace": ["sql"],
        }

    async def fake_cleaning(state):
        counter["cleaning_calls"] += 1

        source = Path(state["data_file_path"])
        output = source.with_name(
            source.stem + "_cleaned" + source.suffix
        )

        if scenario in {
            "large_deletion",
            "large_deletion_resume_approved",
        }:
            create_csv(output, 5)
        else:
            create_csv(output, 2)

        return {
            "current_agent": "cleaning",
            "cleaning_result": "清洗完成",
            "cleaned_file_path": str(output),
            "cleaning_status": "success",
            "status": "running",
            "execution_trace": ["cleaning"],
        }

    def fake_forecast(state):
        if scenario == "needs_human_input":
            return {
                "current_agent": "forecast",
                "forecast_status": "needs_human_input",
                "needs_human_input": True,
                "human_reason": "missing_time",
                "human_question": "请补充预测时间。",
                "status": "needs_human_input",
                "execution_trace": ["forecast"],
            }

        return {
            "current_agent": "forecast",
            "forecast_result": {"answer": "M001=10"},
            "forecast_status": "success",
            "status": "running",
            "execution_trace": ["forecast"],
        }

    def fake_report(state):
        counter["report_calls"] += 1

        return {
            "current_agent": "report",
            "report_result": "最终报告",
            "report_status": "success",
            "status": "completed",
            "execution_trace": ["report"],
        }

    return {
        "sql_node": fake_sql,
        "cleaning_node": fake_cleaning,
        "forecast_node": fake_forecast,
        "report_node": fake_report,
    }


# --------------------------------------------------
# 3. 单用例执行
# --------------------------------------------------

async def execute_case(case: dict[str, Any]) -> dict:
    case_id = case["case_id"]
    scenario = case["scenario"]

    counter = {
        "cleaning_calls": 0,
        "report_calls": 0,
    }

    started = time.perf_counter()

    try:
        with tempfile.TemporaryDirectory() as temp:
            temp_dir = Path(temp)

            source = temp_dir / "orders.csv"
            cleaned = temp_dir / "orders_cleaned.csv"

            if scenario in {
                "large_deletion",
                "large_deletion_resume_approved",
            }:
                create_csv(source, 10)
            else:
                create_csv(source, 2)

            state = {
                "user_query": case["name"],
                "task_mode": case["task_mode"],
                "data_file_path": str(source),
                "status": "running",
                "execution_trace": [],
                "needs_human_input": False,
            }

            # WF004 专门评估已经获得人工批准后的恢复阶段。
            # 它不重复执行前一阶段的 Cleaning。
            if scenario == "large_deletion_resume_approved":
                create_csv(cleaned, 5)

                state.update({
                    "cleaned_file_path": str(cleaned),
                    "cleaning_status": "success",
                    "cleaning_large_deletion_approved": True,
                })

            workflow = build_logistics_workflow(
                **create_fake_nodes(scenario, counter)
            )

            result = await workflow.ainvoke(state)

        elapsed = time.perf_counter() - started

        actual_status = result.get("status")
        actual_trace = result.get("execution_trace", [])
        actual_hitl = (
            actual_status == "needs_human_input"
            or result.get("needs_human_input", False)
        )

        # 基础状态检查
        if case.get("expect_failure"):
            status_ok = actual_status == "failed"
        else:
            status_ok = (
                actual_status == case.get("expected_status")
            )

        # 路由检查
        expected_trace = case.get("expected_trace")
        route_ok = (
            actual_trace == expected_trace
            if expected_trace is not None
            else actual_trace == ["invalid"]
        )

        # HITL 检查
        hitl_ok = (
            actual_hitl == case.get("expect_hitl", False)
        )

        # HITL 原因检查
        if "expected_human_reason" in case:
            reason_ok = (
                result.get("human_reason")
                == case["expected_human_reason"]
            )
        else:
            reason_ok = True

        # Report 是否被正确执行或拦截
        should_report = (
            actual_status == "completed"
            and "report" in actual_trace
        )
        report_ok = (
            counter["report_calls"] == int(should_report)
        )

        # HITL 恢复时不得重新清洗
        reuse_ok = True

        if case.get("rerun_cleaning_agent") is False:
            reuse_ok = counter["cleaning_calls"] == 0

        if case.get("reuse_cleaned_file"):
            quality = result.get("cleaning_quality") or {}
            reuse_ok = (
                reuse_ok
                and result.get("cleaning_quality_status")
                == "success"
                and quality.get("passed") is True
                and quality.get("large_deletion_approved")
                is True
            )

        checks = {
            "status_ok": status_ok,
            "route_ok": route_ok,
            "hitl_ok": hitl_ok,
            "reason_ok": reason_ok,
            "report_ok": report_ok,
            "reuse_ok": reuse_ok,
        }

        passed = all(checks.values())

        return {
            "case_id": case_id,
            "name": case["name"],
            "passed": passed,
            "expected_status": case.get(
                "expected_status", "failed"
            ),
            "actual_status": actual_status,
            "expected_trace": expected_trace or ["invalid"],
            "actual_trace": actual_trace,
            "expected_hitl": case.get("expect_hitl", False),
            "actual_hitl": actual_hitl,
            "human_reason": result.get("human_reason"),
            "cleaning_calls": counter["cleaning_calls"],
            "report_calls": counter["report_calls"],
            "elapsed_seconds": round(elapsed, 4),
            "checks": checks,
            "error": None,
        }

    except Exception as exc:
        return {
            "case_id": case_id,
            "name": case["name"],
            "passed": False,
            "expected_status": case.get("expected_status"),
            "actual_status": "exception",
            "expected_trace": case.get("expected_trace"),
            "actual_trace": [],
            "expected_hitl": case.get("expect_hitl", False),
            "actual_hitl": False,
            "human_reason": None,
            "cleaning_calls": counter["cleaning_calls"],
            "report_calls": counter["report_calls"],
            "elapsed_seconds": round(
                time.perf_counter() - started, 4
            ),
            "checks": {},
            "error": f"{type(exc).__name__}: {exc}",
        }


# --------------------------------------------------
# 4. 指标统计
# --------------------------------------------------

def calculate_metrics(results: list[dict]) -> dict:
    def rate(items, predicate):
        if not items:
            return None

        return round(
            sum(bool(predicate(item)) for item in items)
            / len(items),
            4,
        )

    route_cases = [
        r for r in results
        if r["actual_status"] != "exception"
    ]

    hitl_cases = [
        r for r in results
        if r["case_id"] in {"WF003", "WF006"}
    ]

    resume_cases = [
        r for r in results
        if r["case_id"] == "WF004"
    ]

    completed_cases = [
        r for r in results
        if r["case_id"] in {
            "WF001", "WF002", "WF005"
        }
    ]

    return {
        "evaluation_type": "mock_workflow_control",
        "total_cases": len(results),
        "passed_cases": sum(r["passed"] for r in results),
        "case_pass_rate": rate(
            results, lambda r: r["passed"]
        ),
        "routing_accuracy": rate(
            results,
            lambda r: r["checks"].get("route_ok", False),
        ),
        "hitl_trigger_accuracy": rate(
            hitl_cases,
            lambda r: (
                r["checks"].get("hitl_ok", False)
                and r["checks"].get("reason_ok", False)
            ),
        ),
        "hitl_resume_success_rate": rate(
            resume_cases,
            lambda r: r["passed"],
        ),
        "normal_e2e_success_rate": rate(
            completed_cases,
            lambda r: r["passed"],
        ),
        "average_elapsed_seconds": round(
            sum(r["elapsed_seconds"] for r in results)
            / len(results),
            4,
        ) if results else None,
        "note": (
            "仅评估 Mock Agent 条件下的 Workflow 控制逻辑，"
            "不代表真实业务端到端成功率。"
        ),
    }


# --------------------------------------------------
# 5. 保存与运行
# --------------------------------------------------

async def main():
    cases = json.loads(
        CASES_PATH.read_text(encoding="utf-8-sig")
    )

    if not isinstance(cases, list) or not cases:
        raise ValueError("workflow_cases.json 必须为非空列表")

    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    results = []

    for case in cases:
        result = await execute_case(case)
        results.append(result)

        flag = "PASS" if result["passed"] else "FAIL"

        print(
            f"[{flag}] {result['case_id']} "
            f"{result['name']} "
            f"({result['elapsed_seconds']:.4f}s)"
        )

        if result["error"]:
            print("  ERROR:", result["error"])
        elif not result["passed"]:
            print("  CHECKS:", result["checks"])
            print("  TRACE:", result["actual_trace"])

    metrics = calculate_metrics(results)

    csv_path = RESULT_DIR / "workflow_case_results.csv"
    json_path = RESULT_DIR / "workflow_summary.json"

    with csv_path.open(
        "w", newline="", encoding="utf-8-sig"
    ) as file:
        writer = csv.writer(file)
        writer.writerow([
            "case_id",
            "name",
            "passed",
            "expected_status",
            "actual_status",
            "expected_trace",
            "actual_trace",
            "expected_hitl",
            "actual_hitl",
            "cleaning_calls",
            "report_calls",
            "elapsed_seconds",
            "checks",
            "error",
        ])

        for r in results:
            writer.writerow([
                r["case_id"],
                r["name"],
                r["passed"],
                r["expected_status"],
                r["actual_status"],
                json.dumps(r["expected_trace"]),
                json.dumps(r["actual_trace"]),
                r["expected_hitl"],
                r["actual_hitl"],
                r["cleaning_calls"],
                r["report_calls"],
                r["elapsed_seconds"],
                json.dumps(r["checks"]),
                r["error"],
            ])

    json_path.write_text(
        json.dumps(
            metrics, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )

    print("\n========== Workflow Evaluation ==========")

    for key, value in metrics.items():
        print(f"{key}: {value}")

    print("\n结果文件：")
    print(csv_path)
    print(json_path)


if __name__ == "__main__":
    asyncio.run(main())
