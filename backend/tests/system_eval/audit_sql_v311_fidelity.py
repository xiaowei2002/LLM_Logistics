"""
SQL V3.1.1 Answer Fidelity Audit

目标：
1. 在 V3.1 基础上修正 GROUP BY “合计”判定。
2. 允许答案中的合计值对应 Gold 结果的某个非空子集和。
3. 百分比仍必须能够由 Gold 数据严格推导。
4. 不调用 LLM。
5. 不访问 MCP。
6. 不访问 MySQL。
7. 不修改 V3 / V3.1 原始结果。

运行：
python -m tests.system_eval.audit_sql_v311_fidelity `
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
            "answer fidelity with subset totals."
        )
    )

    parser.add_argument(
        "--source",
        type=str,
        required=True,
        help="SQL V3 result directory.",
    )

    return parser.parse_args()


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

    return math.isclose(
        actual,
        rounded_expected,
        rel_tol=0.0,
        abs_tol=1e-9,
    )


def all_nonempty_subset_sums(
    values: list[float],
) -> list[float]:
    """
    生成所有非空子集和。

    用于验证：
    - 全部类别总计；
    - “其余 N 类合计”；
    - 任意明确子集合计。
    """

    sums: list[float] = []

    n = len(values)

    for size in range(
        1,
        n + 1,
    ):
        for subset in itertools.combinations(
            values,
            size,
        ):
            sums.append(
                sum(subset)
            )

    return sums


def possible_subset_percentages(
    counts: list[float],
) -> list[float]:
    total = sum(counts)

    if total == 0:
        return []

    return [
        subtotal
        / total
        * 100.0
        for subtotal
        in all_nonempty_subset_sums(
            counts
        )
    ]


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


def total_is_supported(
    token: str,
    candidates: list[float],
) -> bool:
    value = parse_number(token)

    if value is None:
        return False

    return any(
        math.isclose(
            value,
            candidate,
            rel_tol=0.0,
            abs_tol=1e-9,
        )
        for candidate in candidates
    )


def audit_single_value(
    case: dict[str, Any],
) -> tuple[bool, list[str]]:
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


def audit_group_by(
    case: dict[str, Any],
) -> tuple[bool, list[str]]:
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

    if not counts:
        return (
            False,
            problems
            or [
                "No valid numeric "
                "GROUP BY counts."
            ],
        )

    subset_totals = (
        all_nonempty_subset_sums(
            counts
        )
    )

    percent_candidates = (
        possible_subset_percentages(
            counts
        )
    )

    # 审计答案中“总计/合计/总数”附近的数字。
    # V3.1.1 允许匹配任意 Gold 子集和，
    # 不再强制要求等于全体总和。
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
                pos + 120
            ]

            tokens = extract_number_tokens(
                local
            )

            if tokens:
                first = tokens[0]

                if not total_is_supported(
                    first,
                    subset_totals,
                ):
                    problems.append(
                        f"Derived total "
                        f"{first} is not "
                        "supported by any "
                        "gold subset."
                    )

            start = (
                pos
                + len(keyword)
            )

    # 审计模型额外声明的百分比。
    percent_tokens = [
        match.group(1)
        for match
        in PERCENT_PATTERN.finditer(
            answer
        )
    ]

    for token in percent_tokens:
        if not percentage_is_supported(
            token,
            percent_candidates,
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
        "v311_fidelity_pass": passed,
        "v311_problems": problems,
    }


def main() -> None:
    args = parse_args()

    source_dir = Path(
        args.source
    )

    if not source_dir.is_absolute():
        source_dir = (
            ROOT
            / source_dir
        )

    source_dir = (
        source_dir.resolve()
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
            "sql_v311_fidelity_audit_"
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
            "v311_fidelity_pass"
        ]
    )

    failed = total - passed

    false_to_true = [
        item["id"]
        for item in audited
        if (
            item[
                "v3_answer_match"
            ]
            is False
            and item[
                "v311_fidelity_pass"
            ]
            is True
        )
    ]

    true_to_false = [
        item["id"]
        for item in audited
        if (
            item[
                "v3_answer_match"
            ]
            is True
            and item[
                "v311_fidelity_pass"
            ]
            is False
        )
    ]

    summary = {
        "evaluation": (
            "SQL V3.1.1 Answer "
            "Fidelity Audit"
        ),
        "source_directory": str(
            source_dir
        ),
        "source_case_results": str(
            source_file
        ),
        "total_cases": total,
        "v311_passed": passed,
        "v311_failed": failed,
        "v311_fidelity_rate": (
            passed / total
            if total
            else 0.0
        ),
        "v3_false_to_v311_true": (
            false_to_true
        ),
        "v3_true_to_v311_false": (
            true_to_false
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
        "=== SQL V3.1.1 "
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
                "v311_fidelity_pass"
            ]
            else "FAIL"
        )

        print(
            f"{item['id']}: {status}"
        )

        if item[
            "v311_problems"
        ]:
            for problem in item[
                "v311_problems"
            ]:
                print(
                    "  -",
                    problem,
                )

    print()
    print("=== SUMMARY ===")

    print(
        f"V3.1.1 fidelity: "
        f"{passed}/{total}"
    )

    print(
        "V3 FAIL -> V3.1.1 PASS:",
        false_to_true or "None",
    )

    print(
        "V3 PASS -> V3.1.1 FAIL:",
        true_to_false or "None",
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