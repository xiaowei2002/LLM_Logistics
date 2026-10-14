
"""
ReportAgent V1：确定性控制流程与事实传递评估。

真实组件：
    core.graph.nodes.report_node.build_report_node

模拟组件：
    ReportAgent.generate()

不调用 LLM、不访问数据库、不修改业务数据。

运行：
    python -m tests.system_eval.eval_report
"""

from __future__ import annotations

import csv
import json
import time
from datetime import datetime
from pathlib import Path

from core.graph.nodes.report_node import build_report_node


ROOT = Path(__file__).resolve().parent
CASE_FILE = ROOT / "cases" / "report_cases.json"
RESULT_DIR = ROOT / "results" / "report_v1"


class FakeReportAgent:
    """捕获真实 Report Node 发送的 instruction。"""

    def __init__(self, scenario: str):
        self.scenario = scenario
        self.calls: list[str] = []

    def generate(self, instruction: str) -> str:
        self.calls.append(instruction)

        if self.scenario == "generator_exception":
            raise RuntimeError("模拟报告生成器异常")

        if self.scenario == "empty_report":
            return ""

        return "这是由模拟 ReportAgent 返回的报告。"


def build_state(scenario: str) -> dict:
    """生成受控上游状态，不构造真实业务执行。"""

    if scenario == "empty":
        return {}

    if scenario == "query_only":
        return {
            "user_query": "请生成本月物料需求分析报告"
        }

    if scenario == "sql_success":
        return {
            "user_query": "查询 SKU-001 的需求数量",
            "sql_result": {
                "rows": [
                    {"sku": "SKU-001", "quantity": 42}
                ]
            },
            "sql_status": "success",
        }

    if scenario == "cleaning_conflict":
        return {
            "user_query": "总结数据清洗情况",
            "cleaning_result": (
                "CleaningAgent 文字台账声称"
                "原始8行，清洗后7行。"
            ),
            "cleaning_quality": {
                "source_row_count": 8,
                "cleaned_row_count": 5,
                "removed_row_count": 3,
                "passed": False,
                "errors": [
                    "Agent台账与最终文件行数不一致"
                ],
            },
            "cleaning_quality_status": "failed",
            "cleaning_quality_error": (
                "清洗台账与验证结果不一致"
            ),
        }

    if scenario == "hitl_approved":
        return {
            "user_query": "生成清洗审核报告",
            "cleaning_quality": {
                "source_row_count": 8,
                "cleaned_row_count": 7,
                "removed_row_count": 1,
                "deletion_rate_percent": 12.5,
                "large_deletion_detected": True,
                "passed": True,
                "warnings": [
                    "大比例删行风险已经人工确认"
                ],
            },
            "cleaning_quality_status": "passed",
            "cleaning_large_deletion_approved": True,
        }

    if scenario == "forecast_answer":
        return {
            "user_query": "生成未来两小时需求报告",
            "forecast_status": "success",
            "forecast_result": {
                "answer": (
                    "预测结果：M001 在 t+1 时段"
                    "需求为 12.5。"
                )
            },
        }

    if scenario == "upstream_error":
        return {
            "user_query": "查询并生成数据报告",
            "sql_status": "error",
            "sql_error": "数据库连接失败",
        }

    if scenario == "empty_report":
        return {
            "user_query": "总结查询结果",
            "sql_result": {
                "rows": [
                    {"sku": "SKU-002", "quantity": 6}
                ]
            },
        }

    if scenario == "generator_exception":
        return {
            "user_query": "总结查询结果",
            "sql_result": {
                "rows": [
                    {"sku": "SKU-003", "quantity": 9}
                ]
            },
        }

    if scenario == "structured_forecast_only":
        return {
            "forecast_status": "success",
            "forecast_result": {
                "answer": "",
                "tool_result": {
                    "status": "success",
                    "variant": "state_conditioned",
                    "forecast": {
                        "t+1": {
                            "material_demand": {
                                "M001": 17.5
                            }
                        }
                    }
                },
            },
        }

    raise ValueError(f"未知评估场景：{scenario}")


def evaluate_case(case: dict) -> dict:
    """运行真实 Report Node 并核对结果。"""

    started = time.perf_counter()

    scenario = case["scenario"]
    fake_agent = FakeReportAgent(scenario)

    result = {}
    problems = []

    try:
        state = build_state(scenario)

        node = build_report_node(fake_agent)
        result = node(state)

        actual_status = result.get("report_status")
        expected_status = case["expected_status"]

        if actual_status != expected_status:
            problems.append(
                f"报告状态不匹配："
                f"expected={expected_status}, "
                f"actual={actual_status}"
            )

        actual_calls = len(fake_agent.calls)
        expected_calls = case["expected_generate_calls"]

        if actual_calls != expected_calls:
            problems.append(
                f"生成器调用次数不匹配："
                f"expected={expected_calls}, "
                f"actual={actual_calls}"
            )

        instruction = (
            fake_agent.calls[0]
            if fake_agent.calls
            else ""
        )

        missing_tokens = [
            token
            for token in case.get("must_include", [])
            if token not in instruction
        ]

        if missing_tokens:
            problems.append(
                "输入事实传递不完整："
                + ", ".join(missing_tokens)
            )

        if actual_status == "success":
            if not str(
                result.get("report_result") or ""
            ).strip():
                problems.append(
                    "成功状态没有非空报告。"
                )

            if result.get("status") != "completed":
                problems.append(
                    "成功状态未标记为 completed。"
                )

        if actual_status == "error":
            if not result.get("report_error"):
                problems.append(
                    "错误状态缺少 report_error。"
                )

        passed = len(problems) == 0

        return {
            "case_id": case["case_id"],
            "name": case["name"],
            "scenario": scenario,
            "risk_probe": bool(
                case.get("risk_probe", False)
            ),
            "expected_status": expected_status,
            "actual_status": actual_status or "",
            "expected_generate_calls": expected_calls,
            "actual_generate_calls": actual_calls,
            "instruction_length": len(instruction),
            "required_tokens": json.dumps(
                case.get("must_include", []),
                ensure_ascii=False,
            ),
            "passed": passed,
            "elapsed_seconds": round(
                time.perf_counter() - started, 6
            ),
            "detail": (
                "; ".join(problems)
                if problems
                else "符合预期"
            ),
        }

    except Exception as exc:
        return {
            "case_id": case["case_id"],
            "name": case["name"],
            "scenario": scenario,
            "risk_probe": bool(
                case.get("risk_probe", False)
            ),
            "expected_status": case["expected_status"],
            "actual_status": "exception",
            "expected_generate_calls": (
                case["expected_generate_calls"]
            ),
            "actual_generate_calls": len(
                fake_agent.calls
            ),
            "instruction_length": (
                len(fake_agent.calls[0])
                if fake_agent.calls
                else 0
            ),
            "required_tokens": json.dumps(
                case.get("must_include", []),
                ensure_ascii=False,
            ),
            "passed": False,
            "elapsed_seconds": round(
                time.perf_counter() - started, 6
            ),
            "detail": (
                f"{type(exc).__name__}: {exc}"
            ),
        }


def main() -> None:
    if not CASE_FILE.is_file():
        raise FileNotFoundError(
            f"案例文件不存在：{CASE_FILE}"
        )

    config = json.loads(
        CASE_FILE.read_text(encoding="utf-8")
    )

    cases = config["cases"]

    if len(cases) != 10:
        raise ValueError(
            "Report V1 固定评估应有10个案例。"
        )

    ids = [
        case["case_id"]
        for case in cases
    ]

    if len(ids) != len(set(ids)):
        raise ValueError(
            "评估案例ID存在重复。"
        )

    # 不覆盖原有评估结果。
    if RESULT_DIR.exists():
        raise FileExistsError(
            "Report V1结果目录已经存在，"
            "请先检查，不要覆盖："
            f"{RESULT_DIR}"
        )

    records = []

    print("=== ReportAgent Evaluation V1 ===")
    print("真实 Report Node：是")
    print("真实 LLM：否")
    print("模拟 ReportAgent：是\n")

    for case in cases:
        row = evaluate_case(case)
        records.append(row)

        status = "PASS" if row["passed"] else "FAIL"

        print(
            f"[{status}] "
            f"{row['case_id']} {row['name']}"
        )

        if not row["passed"]:
            print(f"  detail={row['detail']}")

    total = len(records)
    passed = sum(
        int(row["passed"])
        for row in records
    )

    risk_rows = [
        row for row in records
        if row["risk_probe"]
    ]

    regular_rows = [
        row for row in records
        if not row["risk_probe"]
    ]

    def pass_rate(rows):
        if not rows:
            return None

        return round(
            sum(int(r["passed"]) for r in rows)
            / len(rows),
            4,
        )

    summary = {
        "evaluation_id": config["evaluation_id"],
        "evaluated_at": datetime.now().isoformat(),
        "evaluation_type": config["evaluation_type"],
        "total_cases": total,
        "passed_cases": passed,
        "failed_cases": total - passed,
        "overall_pass_rate": pass_rate(records),
        "regular_cases": len(regular_rows),
        "regular_pass_rate": pass_rate(regular_rows),
        "risk_probe_cases": len(risk_rows),
        "risk_probe_pass_rate": pass_rate(risk_rows),
        "real_report_node_used": True,
        "real_llm_used": False,
        "report_factual_accuracy": None,
        "report_hallucination_rate": None,
        "note": (
            "该评估只检查真实 Report Node "
            "的控制逻辑及事实传递，"
            "不衡量 LLM 生成文本的事实准确性。"
            "上游错误信息可形成失败情况报告，"
            "不将报告生成成功等同于上游任务成功。"
        ),
    }

    RESULT_DIR.mkdir(
        parents=True,
        exist_ok=False,
    )

    csv_path = (
        RESULT_DIR / "report_case_results.csv"
    )

    with csv_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=list(records[0].keys()),
        )
        writer.writeheader()
        writer.writerows(records)

    summary_path = (
        RESULT_DIR / "report_summary.json"
    )

    summary_path.write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n=== Report V1 Summary ===")
    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        )
    )

    print("\n结果文件：")
    print(csv_path)
    print(summary_path)


if __name__ == "__main__":
    main()