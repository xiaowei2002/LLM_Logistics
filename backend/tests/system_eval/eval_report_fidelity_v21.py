
"""
ReportAgent V2.1: real LLM factual-fidelity reevaluation.

Uses exactly the existing RV201-RV206 cases.
Does not edit cases, model code, or Report Node.
Preserves all earlier V2 evidence.

Run:
    python -m tests.system_eval.eval_report_fidelity_v21
"""

from __future__ import annotations

import csv
import json
import re
import time
from datetime import datetime
from pathlib import Path

from core.agents.report_agent import ReportAgent
from core.graph.nodes.report_node import build_report_node


ROOT = Path(__file__).resolve().parent
CASE_PATH = ROOT / "cases" / "report_fidelity_cases.json"
RESULT_DIR = ROOT / "results" / "report_fidelity_v21"


def contains_token(text: str, token: str) -> bool:
    """Reuse the V2 numeric and substring matching rules."""
    token = str(token)

    if re.fullmatch(r"-?\d+(?:\.\d+)?", token):
        pattern = (
            r"(?<![\d.])"
            + re.escape(token)
            + r"(?![\d.])"
        )
        return re.search(pattern, text) is not None

    return token.casefold() in text.casefold()


def base_check(report: str, case: dict) -> dict:
    missing = [
        token
        for token in case.get("required_strings", [])
        if not contains_token(report, token)
    ]

    missing_groups = [
        group
        for group in case.get("required_any_groups", [])
        if not any(
            contains_token(report, token)
            for token in group
        )
    ]

    forbidden = [
        token
        for token in case.get("forbidden_strings", [])
        if contains_token(report, token)
    ]

    return {
        "missing_required": missing,
        "missing_any_groups": missing_groups,
        "found_forbidden": forbidden,
        "passed": not (
            missing or missing_groups or forbidden
        ),
    }


def risk_screen(case_id: str, report: str) -> list[dict]:
    """
    Flag recurring patterns found in V2 manual review.

    A match is a review flag, not conclusive evidence
    of hallucination. Full-context review is required.
    """

    checks = {
        "RV201": [
            (
                "quantity_as_inventory",
                r"(?:库存数量|库存量|现有库存|可用库存)"
                r".{0,25}(?:42|17)"
                r"|(?:42|17).{0,25}"
                r"(?:库存数量|库存量|现有库存|可用库存)",
            ),
        ],
        "RV202": [
            (
                "unprovided_percentage",
                r"37(?:\.50*|\.5)?\s*%",
            ),
        ],
        "RV203": [
            (
                "unsupported_quality_check",
                r"(?:未检出|未检测到|不存在|无)"
                r".{0,18}"
                r"(?:缺失值|重复记录|负值|质量问题)",
            ),
        ],
        "RV204": [
            (
                "claimed_structured_forecast_evidence",
                r"(?:结构化预测输出|结构化预测工具结果|"
                r"结构化预测数据|预测数据经过校验|"
                r"预测结果已核验|状态检查通过)",
            ),
        ],
        "RV205": [
            (
                "unsupported_downstream_state",
                r"(?:CleaningAgent|ForecastAgent|"
                r"清洗模块|预测模块|下游模块|"
                r"后续模块|整个任务链路|整个流程)"
                r".{0,30}"
                r"(?:未运行|未启动|未执行|"
                r"均未|全部跳过|已中断|已终止)"
                r"|(?:未执行|未启动|全部跳过)"
                r".{0,30}"
                r"(?:清洗模块|预测模块|下游模块)",
            ),
        ],
    }

    flags = []

    for rule_id, pattern in checks.get(case_id, []):
        match = re.search(
            pattern,
            report,
            flags=re.IGNORECASE,
        )

        if match:
            flags.append({
                "rule": rule_id,
                "matched_text": match.group(0),
                "review_required": True,
            })

    return flags


def write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )


class CountingReportAgent:
    """
    Calls the actual ReportAgent.
    Counts invocations to verify RV206 does not
    reach the LLM report-generation stage.
    """

    def __init__(self, actual_agent: ReportAgent):
        self.actual_agent = actual_agent
        self.calls = 0

    def generate(self, instruction: str) -> str:
        self.calls += 1
        return self.actual_agent.generate(instruction)


def evaluate_case(
    case: dict,
    node,
    counting_agent: CountingReportAgent,
) -> tuple[dict, str]:

    started = time.perf_counter()

    case_id = case["case_id"]
    expected = case["expected_report_status"]
    before_calls = counting_agent.calls

    result = {}
    report_text = ""
    exception = ""

    try:
        result = node(dict(case["state"]))
        report_text = str(
            result.get("report_result") or ""
        )
    except Exception as exc:
        exception = (
            f"{type(exc).__name__}: {exc}"
        )

    actual = (
        "exception"
        if exception
        else str(result.get("report_status") or "")
    )

    calls = counting_agent.calls - before_calls
    status_match = actual == expected

    if expected == "error":
        base_passed = (
            status_match
            and calls == 0
            and not report_text.strip()
            and bool(result.get("report_error"))
        )

        inspection = {
            "missing_required": [],
            "missing_any_groups": [],
            "found_forbidden": [],
            "passed": base_passed,
        }
        review_status = "not_applicable"

    else:
        inspection = (
            base_check(report_text, case)
            if actual == "success"
            else {
                "missing_required": [],
                "missing_any_groups": [],
                "found_forbidden": [],
                "passed": False,
            }
        )

        base_passed = (
            status_match
            and calls == 1
            and bool(report_text.strip())
            and inspection["passed"]
        )
        review_status = "pending"

    flags = (
        risk_screen(case_id, report_text)
        if actual == "success"
        else []
    )

    strict_screen_passed = (
        base_passed and not flags
    )

    row = {
        "case_id": case_id,
        "name": case["name"],
        "expected_status": expected,
        "actual_status": actual,
        "generate_calls": calls,
        "status_match": status_match,
        "base_rules_passed": base_passed,
        "risk_flags_count": len(flags),
        "risk_screen_passed": strict_screen_passed,
        "manual_review_status": review_status,
        "elapsed_seconds": round(
            time.perf_counter() - started, 4
        ),
        "missing_required": json.dumps(
            inspection["missing_required"],
            ensure_ascii=False,
        ),
        "missing_any_groups": json.dumps(
            inspection["missing_any_groups"],
            ensure_ascii=False,
        ),
        "found_forbidden": json.dumps(
            inspection["found_forbidden"],
            ensure_ascii=False,
        ),
        "risk_flags": json.dumps(
            flags, ensure_ascii=False
        ),
        "error": (
            exception
            or str(result.get("report_error") or "")
        ),
    }

    return row, report_text


def main() -> None:
    if not CASE_PATH.is_file():
        raise FileNotFoundError(CASE_PATH)

    config = json.loads(
        CASE_PATH.read_text(encoding="utf-8")
    )

    cases = config["cases"]

    expected_ids = [
        "RV201", "RV202", "RV203",
        "RV204", "RV205", "RV206",
    ]

    if [case["case_id"] for case in cases] != expected_ids:
        raise ValueError(
            "固定案例 ID 或顺序发生变化，"
            "停止 V2.1 对照评估。"
        )

    if RESULT_DIR.exists():
        raise FileExistsError(
            "结果目录已存在，禁止覆盖："
            f"{RESULT_DIR}"
        )

    # Validate the agent before creating an output directory.
    actual_agent = ReportAgent()
    counting_agent = CountingReportAgent(
        actual_agent
    )
    node = build_report_node(counting_agent)

    RESULT_DIR.mkdir(parents=True)

    rows = []

    print("=== Report Fidelity V2.1 ===")
    print("真实 LLM：是")
    print("案例：原 RV201-RV206，保持不变")
    print("报告全文人工审计：仍然必需")

    for case in cases:
        case_id = case["case_id"]
        print(f"\n运行 {case_id}：{case['name']}")

        row, report = evaluate_case(
            case, node, counting_agent
        )
        rows.append(row)

        # Persist each result immediately, so interrupted
        # runs do not lose all previous reports.
        (
            RESULT_DIR / f"{case_id}_report.txt"
        ).write_text(
            report,
            encoding="utf-8",
        )

        write_json(
            RESULT_DIR / f"{case_id}_audit.json",
            row,
        )

        label = (
            "PASS"
            if row["risk_screen_passed"]
            else "REVIEW/FAIL"
        )

        print(
            f"[{label}] {case_id} "
            f"status={row['actual_status']}, "
            f"calls={row['generate_calls']}, "
            f"risk_flags={row['risk_flags_count']}, "
            f"elapsed={row['elapsed_seconds']}s"
        )

        if row["risk_flags_count"]:
            print("  风险信号：", row["risk_flags"])

        if row["error"]:
            print("  错误：", row["error"])

    csv_path = (
        RESULT_DIR / "report_fidelity_results.csv"
    )

    with csv_path.open(
        "w", encoding="utf-8-sig", newline=""
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=list(rows[0].keys()),
        )
        writer.writeheader()
        writer.writerows(rows)

    base_passes = sum(
        int(row["base_rules_passed"])
        for row in rows
    )

    screened_passes = sum(
        int(row["risk_screen_passed"])
        for row in rows
    )

    summary = {
        "evaluation_id": "report_fidelity_v21",
        "evaluated_at": datetime.now().isoformat(),
        "total_cases": len(rows),
        "base_rule_passes": base_passes,
        "base_rule_pass_rate": round(
            base_passes / len(rows), 4
        ),
        "risk_screen_passes": screened_passes,
        "risk_screen_pass_rate": round(
            screened_passes / len(rows), 4
        ),
        "risk_flagged_cases": [
            row["case_id"]
            for row in rows
            if row["risk_flags_count"]
        ],
        "pending_manual_reviews": sum(
            row["manual_review_status"] == "pending"
            for row in rows
        ),
        "real_llm_used": True,
        "real_sql_used": False,
        "real_forecast_model_used": False,
        "real_cleaning_used": False,
        "full_text_factual_accuracy": None,
        "verified_hallucination_rate": None,
        "limitations": [
            "Risk pattern matching can produce false positives",
            "Risk patterns do not cover all unsupported claims",
            "Complete reports require manual source comparison",
            "Single run is not a stability assessment"
        ],
    }

    write_json(
        RESULT_DIR / "report_fidelity_summary.json",
        summary,
    )

    print("\n=== Report V2.1 Summary ===")
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