
"""
CleaningAgent 数据质量评估 V1。

比较原始 CSV 与清洗后 CSV，评估 CL001-CL008。
PASS   = 已有充分证据通过
FAIL   = 已有证据不符合要求
REVIEW = 需要清洗日志、Quality Gate 或业务确认

运行：
python -m tests.system_eval.eval_cleaning

也可以指定清洗文件：
python -m tests.system_eval.eval_cleaning --cleaned "路径"
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(__file__).resolve().parent

SOURCE_PATH = (
    BACKEND_ROOT / "tests" / "real_dirty_orders.csv"
)
DEFAULT_CLEANED_PATH = (
    BACKEND_ROOT / "tests" / "real_dirty_orders_cleaned.csv"
)
CASES_PATH = EVAL_DIR / "cases" / "cleaning_cases.json"
RESULT_DIR = EVAL_DIR / "results"


def read_csv(path):
    with path.open(
        "r", encoding="utf-8-sig", newline=""
    ) as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        columns = reader.fieldnames or []

    return rows, columns


def is_blank(value):
    return value is None or not str(value).strip()


def get_quantity(row):
    try:
        value = str(row.get("qty", "")).strip()
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


def count_exact_duplicates(rows, columns):
    values = [
        tuple(row.get(col) for col in columns)
        for row in rows
    ]
    counts = Counter(values)

    return sum(count - 1 for count in counts.values())


def row_ids(rows):
    return {
        str(row.get("order_no", "")).strip()
        for row in rows
        if not is_blank(row.get("order_no"))
    }


def check_cases(source, cleaned, src_cols, out_cols):
    results = {}

    def record(code, status, evidence):
        results[code] = {
            "status": status,
            "evidence": evidence,
        }

    # CL001: 完全重复记录
    duplicates = count_exact_duplicates(
        cleaned, out_cols
    )
    record(
        "CL001",
        "PASS" if duplicates == 0 else "FAIL",
        f"清洗后完全重复记录数：{duplicates}。"
        "该检查不等于业务主键去重审计。",
    )

    # CL002: 数量缺失
    original_missing = [
        r.get("order_no") for r in source
        if get_quantity(r) is None
    ]
    cleaned_missing = [
        r.get("order_no") for r in cleaned
        if get_quantity(r) is None
    ]

    record(
        "CL002",
        "REVIEW",
        f"原始数量缺失或不可解析记录：{original_missing}；"
        f"清洗后对应情况：{cleaned_missing}。"
        "需核对填充依据、记录保留情况及清洗日志。",
    )

    # CL003: 异常数量
    negatives = [
        r.get("order_no") for r in cleaned
        if get_quantity(r) is not None
        and get_quantity(r) < 0
    ]

    extreme_records = [
        r.get("order_no") for r in cleaned
        if get_quantity(r) is not None
        and get_quantity(r) >= 99999
    ]

    record(
        "CL003",
        "FAIL" if negatives else "REVIEW",
        f"剩余负数记录：{negatives}；"
        f"达到示例极端值99999的记录：{extreme_records}。"
        "负数不符合当前正向需求数量测试口径；"
        "极端值是否有效及修正规则仍需业务依据。",
    )

    # CL004: 编码标准化
    bad_codes = []

    for index, row in enumerate(cleaned, 1):
        for column in ("sku", "warehouse"):
            value = row.get(column)

            if is_blank(value):
                continue

            text = str(value)
            if text != text.strip().upper():
                bad_codes.append(
                    f"row={index}, {column}={text!r}"
                )

    record(
        "CL004",
        "FAIL" if bad_codes else "PASS",
        f"未标准化编码：{bad_codes}。"
        "此项假定 SKU 与仓库编码采用大写规范。",
    )

    # CL005: 仓库缺失
    source_missing_warehouse = [
        r.get("order_no") for r in source
        if is_blank(r.get("warehouse"))
    ]

    cleaned_missing_warehouse = [
        r.get("order_no") for r in cleaned
        if is_blank(r.get("warehouse"))
    ]

    record(
        "CL005",
        "REVIEW",
        f"原始仓库缺失：{source_missing_warehouse}；"
        f"清洗后仓库缺失：{cleaned_missing_warehouse}。"
        "需核查新增仓库值是否具有可靠业务来源。",
    )

    # CL006: 原始时间保护
    time_columns = [
        col for col in out_cols
        if col in ("created_at", "created_at_raw")
    ]

    original_times = {
        (r.get("order_no"), r.get("sku")):
        r.get("created_at")
        for r in source
    }

    if not time_columns:
        record(
            "CL006", "FAIL",
            "清洗文件未保留 created_at 或 "
            "created_at_raw，无法核对原始时间。",
        )
    else:
        changed = []
        if "created_at_raw" in out_cols:
            for row in cleaned:
                key = (
                    row.get("order_no"),
                    row.get("sku"),
                )
                if key in original_times:
                    if (
                        row.get("created_at_raw")
                        != original_times[key]
                    ):
                        changed.append(key)

        record(
            "CL006",
            "FAIL" if changed else "REVIEW",
            f"时间字段：{time_columns}；"
            f"原始时间副本不一致记录：{changed}。"
            "仍需逐行核对日期含义，尤其是 "
            "09-03-2026 的月日顺序。",
        )

    # CL007: Quality Gate 安全证据
    src_count = len(source)
    out_count = len(cleaned)

    deletion_rate = (
        max(0, src_count - out_count) / src_count
        if src_count else None
    )

    record(
        "CL007",
        "REVIEW",
        f"原始 {src_count} 行，清洗后 {out_count} 行；"
        f"净行数减少比例：{deletion_rate}。"
        "本次为离线文件评估，不能仅凭 CSV 证明 "
        "Quality Gate 是否正确触发 HITL，"
        "也不能证明删除前已经审批。",
    )

    # CL008: 落盘、原始记录与追溯
    original_ids = row_ids(source)
    cleaned_ids = row_ids(cleaned)

    missing_ids = sorted(original_ids - cleaned_ids)
    extra_ids = sorted(cleaned_ids - original_ids)

    record(
        "CL008",
        "FAIL" if missing_ids or extra_ids else "REVIEW",
        f"原始订单标识缺失：{missing_ids}；"
        f"新增订单标识：{extra_ids}。"
        "文件存在已确认，但需要核对清洗执行日志、"
        "最终报告及原始数据未被改写的证据。",
    )

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cleaned",
        type=Path,
        default=DEFAULT_CLEANED_PATH,
    )
    args = parser.parse_args()

    cleaned_path = args.cleaned.resolve()

    if not SOURCE_PATH.exists():
        raise FileNotFoundError(
            f"原始数据不存在：{SOURCE_PATH}"
        )

    if not cleaned_path.exists():
        raise FileNotFoundError(
            f"清洗文件不存在：{cleaned_path}\n"
            "请先运行真实 Cleaning 集成测试，"
            "或使用 --cleaned 指定已有清洗文件。"
        )

    case_data = json.loads(
        CASES_PATH.read_text(encoding="utf-8-sig")
    )
    cases = case_data["cases"]

    source, src_cols = read_csv(SOURCE_PATH)
    cleaned, out_cols = read_csv(cleaned_path)

    checks = check_cases(
        source, cleaned, src_cols, out_cols
    )

    details = []

    for case in cases:
        case_id = case["case_id"]
        check = checks[case_id]

        details.append({
            "case_id": case_id,
            "name": case["name"],
            "category": case["category"],
            "status": check["status"],
            "evidence": check["evidence"],
        })

    counts = Counter(
        item["status"] for item in details
    )

    decided = counts["PASS"] + counts["FAIL"]

    summary = {
        "evaluation_type": "offline_cleaning_quality",
        "source_file": str(SOURCE_PATH),
        "cleaned_file": str(cleaned_path),
        "source_rows": len(source),
        "cleaned_rows": len(cleaned),
        "total_cases": len(details),
        "pass_count": counts["PASS"],
        "fail_count": counts["FAIL"],
        "review_count": counts["REVIEW"],
        "decided_cases": decided,
        "decided_case_pass_rate": (
            round(counts["PASS"] / decided, 4)
            if decided else None
        ),
        "overall_pass_rate": (
            round(counts["PASS"] / len(details), 4)
            if details else None
        ),
        "note": (
            "overall_pass_rate 仅表示全部用例中已确认"
            "通过的占比，不代表真实清洗准确率；"
            "REVIEW 不计为通过。"
        ),
    }

    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    detail_path = (
        RESULT_DIR / "cleaning_case_results.csv"
    )
    summary_path = (
        RESULT_DIR / "cleaning_summary.json"
    )

    with detail_path.open(
        "w", encoding="utf-8-sig", newline=""
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "case_id", "name", "category",
                "status", "evidence",
            ],
        )
        writer.writeheader()
        writer.writerows(details)

    summary_path.write_text(
        json.dumps(
            summary, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )

    print("\n=== Cleaning Evaluation V1 ===")

    for item in details:
        print(
            f"[{item['status']}] "
            f"{item['case_id']} {item['name']}"
        )
        print(" ", item["evidence"])

    print("\n=== Summary ===")
    for key, value in summary.items():
        print(f"{key}: {value}")

    print("\n结果文件：")
    print(detail_path)
    print(summary_path)


if __name__ == "__main__":
    main()
