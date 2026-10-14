
"""
ReportAgent V2：真实 LLM 报告事实忠实性评估。

真实组件：
- ReportAgent
- Report Node
- 项目统一 LLM

模拟输入：
- 固定 SQL / Cleaning / Forecast 上游事实

评估结果：
- 确定性规则检查
- 每案例原始报告
- 待人工复核状态

运行：
python -m tests.system_eval.eval_report_fidelity

注意：
自动检查通过，不代表全文事实准确率达到100%。
"""

from __future__ import annotations

import csv
import json
import re
import time
from datetime import datetime
from pathlib import Path

from core.agents.report_agent import ReportAgent
from core.graph.nodes.report_node import (
    build_report_node,
)


ROOT = Path(__file__).resolve().parent

CASE_PATH = (
    ROOT / "cases" / "report_fidelity_cases.json"
)

RESULT_DIR = (
    ROOT / "results" / "report_fidelity_v2"
)


def contains_token(text: str, token: str) -> bool:
    """
    检查文本是否包含预期事实。

    对纯数字使用边界检查，减少 17 命中 170
    等明显误判。

    注意：这仍然不验证数字与对象的对应关系。
    """
    token = str(token)

    if re.fullmatch(
        r"-?\d+(?:\.\d+)?", token
    ):
        pattern = (
            r"(?<![\d.])"
            + re.escape(token)
            + r"(?![\d.])"
        )
        return re.search(pattern, text) is not None

    return token.casefold() in text.casefold()


def inspect_report(
    report: str,
    case: dict,
) -> dict:

    missing_required = [
        token
        for token in case.get(
            "required_strings", []
        )
        if not contains_token(report, token)
    ]

    missing_any_groups = []

    for group in case.get(
        "required_any_groups", []
    ):
        if not any(
            contains_token(report, token)
            for token in group
        ):
            missing_any_groups.append(group)

    found_forbidden = [
        token
        for token in case.get(
            "forbidden_strings", []
        )
        if contains_token(report, token)
    ]

    return {
        "missing_required": missing_required,
        "missing_any_groups": missing_any_groups,
        "found_forbidden": found_forbidden,
        "passed": not (
            missing_required
            or missing_any_groups
            or found_forbidden
        ),
    }


def evaluate_case(
    case: dict,
    report_node,
) -> tuple[dict, str]:

    started = time.perf_counter()
    report_text = ""

    expected_status = case[
        "expected_report_status"
    ]

    record = {
        "case_id": case["case_id"],
        "name": case["name"],
        "expected_status": expected_status,
        "actual_status": "",
        "status_match": False,
        "fidelity_rules_passed": False,
        "automatic_passed": False,
        "manual_review_status": "pending",
        "elapsed_seconds": 0.0,
        "missing_required": "[]",
        "missing_any_groups": "[]",
        "found_forbidden": "[]",
        "error": "",
    }

    try:
        result = report_node(
            dict(case["state"])
        )

        actual_status = str(
            result.get("report_status") or ""
        )

        record["actual_status"] = actual_status
        record["status_match"] = (
            actual_status == expected_status
        )

        report_text = str(
            result.get("report_result") or ""
        )

        if expected_status == "error":
            # 无上游事实应被拦截。
            rules_passed = (
                actual_status == "error"
                and not report_text.strip()
                and bool(result.get("report_error"))
            )

            record["fidelity_rules_passed"] = (
                rules_passed
            )

            # 未调用 LLM 的门控案例不需要全文复核。
            record["manual_review_status"] = (
                "not_applicable"
            )

        else:
            if actual_status == "success":
                inspection = inspect_report(
                    report_text, case
                )

                record[
                    "fidelity_rules_passed"
                ] = inspection["passed"]

                for field in (
                    "missing_required",
                    "missing_any_groups",
                    "found_forbidden",
                ):
                    record[field] = json.dumps(
                        inspection[field],
                        ensure_ascii=False,
                    )

            record["manual_review_status"] = "pending"

        record["automatic_passed"] = (
            record["status_match"]
            and record["fidelity_rules_passed"]
        )

        if actual_status == "error":
            record["error"] = str(
                result.get("report_error") or ""
            )

    except Exception as exc:
        record["actual_status"] = "exception"
        record["error"] = (
            f"{type(exc).__name__}: {exc}"
        )
        record["manual_review_status"] = (
            "not_evaluable"
        )

    record["elapsed_seconds"] = round(
        time.perf_counter() - started,
        4,
    )

    return record, report_text


def main() -> None:
    if not CASE_PATH.is_file():
        raise FileNotFoundError(
            f"找不到评估案例：{CASE_PATH}"
        )

    config = json.loads(
        CASE_PATH.read_text(encoding="utf-8")
    )

    cases = config["cases"]

    if len(cases) != 6:
        raise ValueError(
            "Report V2 预期固定为6个案例。"
        )

    ids = [
        case["case_id"] for case in cases
    ]

    if len(ids) != len(set(ids)):
        raise ValueError(
            "评估案例ID重复。"
        )

    if RESULT_DIR.exists():
        raise FileExistsError(
            "Report V2 结果目录已存在，"
            "拒绝覆盖历史评估："
            f"{RESULT_DIR}"
        )

    # 先建立真实模型和报告节点。
    # 此过程不执行 SQL、清洗或预测工具。
    agent = ReportAgent()
    report_node = build_report_node(agent)

    print("=== Report Fidelity V2 ===")
    print("真实 LLM：是")
    print("真实 Report Node：是")
    print("上游业务事实：固定受控数据")
    print("人工全文复核：待完成")

    records = []
    report_texts = {}

    for case in cases:
        print(
            f"\n正在运行 {case['case_id']}："
            f"{case['name']}"
        )

        row, report_text = evaluate_case(
            case, report_node
        )

        records.append(row)
        report_texts[case["case_id"]] = (
            report_text
        )

        label = (
            "PASS"
            if row["automatic_passed"]
            else "FAIL"
        )

        print(
            f"[{label}] {case['case_id']}"
        )
        print(
            f"  actual_status="
            f"{row['actual_status']}"
        )
        print(
            f"  elapsed="
            f"{row['elapsed_seconds']}s"
        )

        if row["missing_required"] != "[]":
            print(
                "  缺少字段："
                + row["missing_required"]
            )

        if row["missing_any_groups"] != "[]":
            print(
                "  缺少事实类别："
                + row["missing_any_groups"]
            )

        if row["found_forbidden"] != "[]":
            print(
                "  命中禁止表述："
                + row["found_forbidden"]
            )

        if row["error"]:
            print(
                "  error=" + row["error"]
            )

    # 所有案例执行完毕后再创建结果目录。
    RESULT_DIR.mkdir(
        parents=True,
        exist_ok=False,
    )

    for case in cases:
        case_id = case["case_id"]

        (
            RESULT_DIR / f"{case_id}_report.txt"
        ).write_text(
            report_texts[case_id],
            encoding="utf-8",
        )

    results_path = (
        RESULT_DIR / "report_fidelity_results.csv"
    )

    with results_path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=list(records[0].keys()),
        )
        writer.writeheader()
        writer.writerows(records)

    completed = [
        row for row in records
        if row["actual_status"]
        in ("success", "error")
    ]

    passed = sum(
        int(row["automatic_passed"])
        for row in completed
    )

    generated_reports = [
        row for row in records
        if row["actual_status"] == "success"
    ]

    summary = {
        "evaluation_id": config["evaluation_id"],
        "evaluated_at": datetime.now().isoformat(),
        "total_cases": len(records),
        "completed_cases": len(completed),
        "exception_cases": (
            len(records) - len(completed)
        ),
        "automatically_passed_cases": passed,
        "automatic_pass_rate": (
            round(passed / len(completed), 4)
            if completed else None
        ),
        "generated_reports": len(
            generated_reports
        ),
        "pending_manual_reviews": sum(
            row["manual_review_status"] == "pending"
            for row in records
        ),
        "real_llm_used": True,
        "real_report_agent_used": True,
        "real_sql_used": False,
        "real_cleaning_used": False,
        "real_forecasting_used": False,
        "verified_full_text_factual_accuracy": None,
        "verified_hallucination_rate": None,
        "note": (
            "自动规则只检测明确的必要字段及"
            "禁止表述，不能证明数值与对象关联正确，"
            "也不能识别所有新增事实。"
            "必须复核各案例完整报告文本。"
        ),
    }

    (
        RESULT_DIR / "report_fidelity_summary.json"
    ).write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n=== Report V2 Summary ===")
    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        )
    )

    print("\n结果目录：")
    print(RESULT_DIR)


if __name__ == "__main__":
    main()