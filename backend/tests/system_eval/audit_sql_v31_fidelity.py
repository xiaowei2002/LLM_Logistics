"""
SQL V3.1 Answer Fidelity Audit

目的：
1. 对已经完成的 SQL V3 Real 结果重新审计自然语言答案。
2. 不调用 LLM。
3. 不访问 MCP。
4. 不访问 MySQL。
5. 不修改 SQL V3 原始结果。
6. 解决以下旧判分问题：
   - 2251909.54 与 2,251,909.54 应视为同值；
   - 120.653739 -> 120.65 的合理四舍五入应允许；
   - GROUP BY 答案必须核对类别与对应数值；
   - 模型额外生成的百分比、总计也必须验证，
     防止“原始值正确但派生计算错误”仍被判 PASS。

运行：
python -m tests.system_eval.audit_sql_v31_fidelity

也可以指定某一次 V3 结果：
python -m tests.system_eval.audit_sql_v31_fidelity `
    --source tests/system_eval/results/sql_v3_real_20261013-103905
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]

RESULT_ROOT = (
    ROOT
    / "tests"
    / "system_eval"
    / "results"
)


NUMBER_PATTERN = re.compile(
    r"(?<![\w.])"
    r"[-+]?"
    r"(?:\d{1,3}(?:,\d{3})+|\d+)"
    r"(?:\.\d+)?"
    r"(?![\w.])"
)

PERCENT_PATTERN = re.compile(
    r"([-+]?"
    r"(?:\d{1,3}(?:,\d{3})+|\d+)"
    r"(?:\.\d+)?)"
    r"\s*%"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit SQL V3 natural-language "
            "answer fidelity."
        )
    )

    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help=(
            "SQL V3 result directory. "
            "If omitted, use latest "
            "sql_v3_real_* directory."
        ),
    )

    return parser.parse_args()


def resolve_source_dir(
    source: str | None,
) -> Path:
    if source:
        path = Path(source)

        if not path.is_absolute():
            path = ROOT / path

        path = path.resolve()

        if not path.exists():
            raise FileNotFoundError(
                f"Source directory not found: {path}"
            )

        return path

    candidates = sorted(
        [
            path
            for path in RESULT_ROOT.glob(
                "sql_v3_real_*"
            )
            if path.is_dir()
        ],
        key=lambda path: path.name,
    )

    if not candidates:
        raise FileNotFoundError(
            "No sql_v3_real_* result "
            "directory was found."
        )

    return candidates[-1]


def load_json(path: Path) -> Any:
    with path.open(
        "r",
        encoding="utf-8",
    ) as f:
        return json.load(f)


def parse_number(
    value: str,
) -> float | None:
    cleaned = (
        value.strip()
        .replace(",", "")
    )

    try:
        return float(cleaned)
    except ValueError:
        return None


def decimal_places(
    token: str,
) -> int:
    cleaned = token.replace(",", "")

    if "." not in cleaned:
        return 0

    return len(
        cleaned.rsplit(".", 1)[1]
    )


def is_integer_text(
    value: str,
) -> bool:
    number = parse_number(value)

    if number is None:
        return False

    return math.isclose(
        number,
        round(number),
        rel_tol=0.0,
        abs_tol=1e-12,
    )


def extract_number_tokens(
    text: str,
) -> list[str]:
    return [
        match.group(0)
        for match in NUMBER_PATTERN.finditer(
            text
        )
    ]


def numeric_token_matches_gold(
    token: str,
    gold: str,
    *,
    integer_exact: bool,
) -> bool:
    """
    数值语义比较。

    integer_exact=True：
        COUNT 等整数必须精确一致。

    integer_exact=False：
        允许答案按其显示的小数位数
        对真实值进行标准四舍五入。

    例如：
        gold=120.653739
        answer=120.65
        -> PASS

        gold=2251909.54
        answer=2,251,909.54
        -> PASS
    """

    actual = parse_number(token)
    expected = parse_number(gold)

    if (
        actual is None
        or expected is None
    ):
        return False

    if integer_exact:
        return math.isclose(
            actual,
            expected,
            rel_tol=0.0,
            abs_tol=1e-9,
        )

    places = decimal_places(token)

    rounded_expected = round(
        expected,
        places,
    )

    tolerance = (
        0.5 * (10 ** (-places))
        if places > 0
        else 0.5
    )

    return math.isclose(
        actual,
        rounded_expected,
        rel_tol=0.0,
        abs_tol=max(
            tolerance * 1e-6,
            1e-12,
        ),
    )


def audit_single_value(
    case: dict[str, Any],
) -> tuple[bool, list[str]]:
    """
    审计单一数值型答案。

    basic_count / filter_count：
        整数精确匹配。

    aggregation：
        允许合理显示精度舍入。
    """

    gold_rows = case.get(
        "gold_rows",
        [],
    )

    answer = str(
        case.get("answer") or ""
    )

    if (
        len(gold_rows) != 1
        or len(gold_rows[0]) != 1
    ):
        return (
            False,
            [
                "Expected exactly one "
                "gold scalar value."
            ],
        )

    gold = str(
        gold_rows[0][0]
    )

    tokens = extract_number_tokens(
        answer
    )

    category = str(
        case.get("category") or ""
    )

    integer_exact = (
        category
        in {
            "basic_count",
            "filter_count",
        }
    )

    matched = any(
        numeric_token_matches_gold(
            token,
            gold,
            integer_exact=integer_exact,
        )
        for token in tokens
    )

    if matched:
        return True, []

    return (
        False,
        [
            (
                f"Gold scalar {gold!r} "
                "was not represented "
                "correctly in answer."
            )
        ],
    )


def possible_subset_percentages(
    counts: list[float],
) -> list[float]:
    """
    生成各类别任意非空真子集的占比。

    数据类别数量很少，
    V3 中最多 8 类，因此穷举安全。

    这样可以验证：
    - 单类别占比；
    - “其他类别合计”占比。
    """

    total = sum(counts)

    if total == 0:
        return []

    percentages: list[float] = []

    n = len(counts)

    for size in range(
        1,
        n + 1,
    ):
        for subset in itertools.combinations(
            counts,
            size,
        ):
            percentages.append(
                sum(subset)
                / total
                * 100.0
            )

    return percentages


def percentage_is_supported(
    token: str,
    candidates: list[float],
) -> bool:
    value = parse_number(token)

    if value is None:
        return False

    places = decimal_places(token)

    for candidate in candidates:
        if math.isclose(
            value,
            round(candidate, places),
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            return True

    return False


def audit_group_by(
    case: dict[str, Any],
) -> tuple[bool, list[str]]:
    """
    审计 GROUP BY 类答案。

    要求：
    1. 每个类别出现在答案中；
    2. 每个类别对应的计数正确出现；
    3. 如模型额外给出总计，则必须正确；
    4. 如模型额外给出百分比，则必须能够由
       Gold 结果推导得到。
    """

    gold_rows = case.get(
        "gold_rows",
        [],
    )

    answer = str(
        case.get("answer") or ""
    )

    problems: list[str] = []

    if not gold_rows:
        return (
            False,
            ["Gold rows are empty."],
        )

    counts: list[float] = []

    for row in gold_rows:
        if len(row) < 2:
            problems.append(
                "Gold GROUP BY row "
                "does not contain 2 columns."
            )
            continue

        label = str(row[0])
        count_text = str(row[1])

        count = parse_number(
            count_text
        )

        if count is None:
            problems.append(
                f"Invalid gold count "
                f"for {label!r}: "
                f"{count_text!r}"
            )
            continue

        counts.append(count)

        if label.lower() not in answer.lower():
            problems.append(
                f"Missing category "
                f"{label!r}."
            )
            continue

        # 在类别附近寻找正确数量。
        label_pos = (
            answer.lower().find(
                label.lower()
            )
        )

        local_text = answer[
            label_pos:
            label_pos + 160
        ]

        tokens = extract_number_tokens(
            local_text
        )

        if not any(
            numeric_token_matches_gold(
                token,
                count_text,
                integer_exact=True,
            )
            for token in tokens
        ):
            problems.append(
                f"Category {label!r} "
                f"does not show correct "
                f"value {count_text}."
            )

    if counts:
        total = sum(counts)

        # 检查“总计”附近的数字。
        for keyword in (
            "总计",
            "合计",
            "总数",
        ):
            start = 0

            while True:
                pos = answer.find(
                    keyword,
                    start,
                )

                if pos < 0:
                    break

                local = answer[
                    pos:
                    pos + 100
                ]

                tokens = (
                    extract_number_tokens(
                        local
                    )
                )

                if tokens:
                    first = tokens[0]

                    if not math.isclose(
                        parse_number(first)
                        or float("nan"),
                        total,
                        rel_tol=0.0,
                        abs_tol=1e-9,
                    ):
                        problems.append(
                            f"Derived total "
                            f"{first} is wrong; "
                            f"expected {total:g}."
                        )

                start = (
                    pos
                    + len(keyword)
                )

        # 检查模型额外声称的百分比。
        percent_tokens = [
            match.group(1)
            for match
            in PERCENT_PATTERN.finditer(
                answer
            )
        ]

        candidates = (
            possible_subset_percentages(
                counts
            )
        )

        for token in percent_tokens:
            if not percentage_is_supported(
                token,
                candidates,
            ):
                problems.append(
                    f"Derived percentage "
                    f"{token}% is not "
                    "supported by gold rows."
                )

    return (
        len(problems) == 0,
        problems,
    )


def audit_case(
    case: dict[str, Any],
) -> dict[str, Any]:
    category = str(
        case.get("category") or ""
    )

    if not case.get(
        "result_match",
        False,
    ):
        passed = False
        problems = [
            "Database result itself "
            "did not match gold result."
        ]

    elif category == "group_by":
        passed, problems = (
            audit_group_by(case)
        )

    else:
        passed, problems = (
            audit_single_value(case)
        )

    return {
        "id": case.get("id"),
        "category": category,
        "question": case.get(
            "question"
        ),
        "gold_rows": case.get(
            "gold_rows"
        ),
        "actual_rows": case.get(
            "actual_rows"
        ),
        "generated_sql": case.get(
            "generated_sql"
        ),
        "answer": case.get(
            "answer"
        ),
        "v3_answer_match": case.get(
            "answer_match"
        ),
        "v3_overall_correct": case.get(
            "overall_correct"
        ),
        "v31_fidelity_pass": passed,
        "v31_problems": problems,
    }


def main() -> None:
    args = parse_args()

    source_dir = resolve_source_dir(
        args.source
    )

    source_file = (
        source_dir
        / "case_results.json"
    )

    if not source_file.exists():
        raise FileNotFoundError(
            f"Missing source result: "
            f"{source_file}"
        )

    cases = load_json(
        source_file
    )

    if not isinstance(cases, list):
        raise TypeError(
            "case_results.json must "
            "contain a list."
        )

    timestamp = datetime.now().strftime(
        "%Y%m%d-%H%M%S"
    )

    output_dir = (
        RESULT_ROOT
        / (
            "sql_v31_fidelity_audit_"
            + timestamp
        )
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=False,
    )

    audited = [
        audit_case(case)
        for case in cases
    ]

    total = len(audited)

    passed = sum(
        1
        for item in audited
        if item[
            "v31_fidelity_pass"
        ]
    )

    failed = total - passed

    changed_false_to_true = [
        item["id"]
        for item in audited
        if (
            item[
                "v3_answer_match"
            ]
            is False
            and item[
                "v31_fidelity_pass"
            ]
            is True
        )
    ]

    changed_true_to_false = [
        item["id"]
        for item in audited
        if (
            item[
                "v3_answer_match"
            ]
            is True
            and item[
                "v31_fidelity_pass"
            ]
            is False
        )
    ]

    summary = {
        "evaluation": (
            "SQL V3.1 Answer "
            "Fidelity Audit"
        ),
        "source_directory": str(
            source_dir
        ),
        "source_case_results": str(
            source_file
        ),
        "total_cases": total,
        "v31_passed": passed,
        "v31_failed": failed,
        "v31_fidelity_rate": (
            passed / total
            if total
            else 0.0
        ),
        "v3_false_to_v31_true": (
            changed_false_to_true
        ),
        "v3_true_to_v31_false": (
            changed_true_to_false
        ),
        "llm_called": False,
        "mysql_called": False,
        "mcp_called": False,
        "source_results_modified": False,
    }

    audit_path = (
        output_dir
        / "audit_results.json"
    )

    summary_path = (
        output_dir
        / "summary.json"
    )

    with audit_path.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            audited,
            f,
            ensure_ascii=False,
            indent=2,
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

    print(
        "=== SQL V3.1 "
        "ANSWER FIDELITY AUDIT ==="
    )

    print(
        "Source:",
        source_dir,
    )

    print(
        "Cases:",
        total,
    )

    print()

    for item in audited:
        status = (
            "PASS"
            if item[
                "v31_fidelity_pass"
            ]
            else "FAIL"
        )

        print(
            f"{item['id']}: {status}"
        )

        if item["v31_problems"]:
            for problem in item[
                "v31_problems"
            ]:
                print(
                    "  -",
                    problem,
                )

    print()
    print("=== SUMMARY ===")

    print(
        f"V3.1 fidelity: "
        f"{passed}/{total}"
    )

    print(
        "V3 FAIL -> V3.1 PASS:",
        (
            changed_false_to_true
            or "None"
        ),
    )

    print(
        "V3 PASS -> V3.1 FAIL:",
        (
            changed_true_to_false
            or "None"
        ),
    )

    print(
        "Audit results:",
        audit_path,
    )

    print(
        "Summary:",
        summary_path,
    )

    print()
    print(
        "No LLM, MCP or MySQL "
        "calls were made."
    )


if __name__ == "__main__":
    main()