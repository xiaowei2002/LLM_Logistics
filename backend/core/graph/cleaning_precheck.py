
"""
清洗前只读风险检查 V1。

功能：
1. 检测完全重复记录
2. 检测数量缺失、负数和统计离群值
3. 检测编码不规范和仓库缺失
4. 检测混合或歧义时间格式
5. 生成拟议操作与审批风险清单

不执行任何清洗操作。
"""

from __future__ import annotations

import csv
import math
import re
from collections import Counter
from pathlib import Path


def _blank(value):
    return value is None or not str(value).strip()


def _quantile(values, q):
    values = sorted(values)
    if not values:
        return None

    position = (len(values) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)

    return values[lower] + (
        values[upper] - values[lower]
    ) * (position - lower)


def _to_number(value):
    if _blank(value):
        return None

    try:
        number = float(str(value).strip())
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def inspect_cleaning_risks(file_path):
    path = Path(file_path).resolve()

    if not path.is_file():
        raise FileNotFoundError(path)

    with path.open(
        "r", encoding="utf-8-sig", newline=""
    ) as file:
        reader = csv.DictReader(file)
        columns = reader.fieldnames or []
        rows = list(reader)

    required = {"order_no", "sku", "qty", "created_at", "warehouse"}
    missing_columns = sorted(required - set(columns))

    if missing_columns:
        raise ValueError(
            f"缺少必要字段：{missing_columns}"
        )

    risks = []
    row_count = len(rows)

    # 完全重复：以原始字段值比较，不修改记录
    seen = set()
    duplicates = []

    for index, row in enumerate(rows, start=2):
        key = tuple(row.get(col) for col in columns)

        if key in seen:
            duplicates.append({
                "csv_line": index,
                "order_no": row.get("order_no"),
            })
        else:
            seen.add(key)

    if duplicates:
        risks.append({
            "type": "exact_duplicate",
            "severity": "review",
            "records": duplicates,
            "suggested_action": "propose_deduplication",
            "requires_approval": True,
        })

    # 数量检测
    valid_nonnegative = []

    for row in rows:
        number = _to_number(row.get("qty"))

        if number is not None and number >= 0:
            valid_nonnegative.append(number)

    # IQR 仅用于筛选待核查离群值，不代表非法值
    outlier_upper = None

    if len(valid_nonnegative) >= 4:
        q1 = _quantile(valid_nonnegative, 0.25)
        q3 = _quantile(valid_nonnegative, 0.75)
        outlier_upper = q3 + 1.5 * (q3 - q1)

    for index, row in enumerate(rows, start=2):
        order_no = row.get("order_no")
        raw_qty = row.get("qty")
        quantity = _to_number(raw_qty)

        if _blank(raw_qty):
            risks.append({
                "type": "missing_quantity",
                "csv_line": index,
                "order_no": order_no,
                "suggested_action": "retain_and_flag",
                "requires_approval": False,
            })

        elif quantity is None:
            risks.append({
                "type": "invalid_quantity_format",
                "csv_line": index,
                "order_no": order_no,
                "value": raw_qty,
                "suggested_action": "retain_and_flag",
                "requires_approval": False,
            })

        elif quantity < 0:
            risks.append({
                "type": "negative_quantity",
                "csv_line": index,
                "order_no": order_no,
                "value": quantity,
                "suggested_action": "retain_and_flag",
                "requires_approval": False,
            })

        elif (
            outlier_upper is not None
            and quantity > outlier_upper
        ):
            risks.append({
                "type": "statistical_outlier",
                "csv_line": index,
                "order_no": order_no,
                "value": quantity,
                "upper_fence": outlier_upper,
                "suggested_action": "retain_and_flag",
                "requires_approval": False,
            })

        for field in ("sku", "warehouse"):
            value = row.get(field)

            if _blank(value):
                if field == "warehouse":
                    risks.append({
                        "type": "missing_warehouse",
                        "csv_line": index,
                        "order_no": order_no,
                        "suggested_action": "retain_and_flag",
                        "requires_approval": False,
                    })
                continue

            normalized = str(value).strip().upper()

            if value != normalized:
                risks.append({
                    "type": "nonstandard_code",
                    "field": field,
                    "csv_line": index,
                    "order_no": order_no,
                    "value": value,
                    "suggested_value": normalized,
                    "suggested_action": "normalize_if_policy_allows",
                    "requires_approval": False,
                })

        raw_time = str(row.get("created_at") or "").strip()

        if re.fullmatch(r"\d{2}-\d{2}-\d{4}", raw_time):
            risks.append({
                "type": "ambiguous_date_format",
                "csv_line": index,
                "order_no": order_no,
                "value": raw_time,
                "suggested_action": "preserve_original",
                "requires_approval": False,
            })

    # 这只是完全重复记录的拟议去重比例，
    # 并不代表已执行删除。
    proposed_delete_count = len(duplicates)
    proposed_delete_rate = (
        proposed_delete_count / row_count
        if row_count else 0.0
    )

    # 即使重复记录比例较低，任何实际删行
    # 也需要经过审批策略判断。
    operations = [
        {
            "operation": "remove_exact_duplicates",
            "candidate_count": proposed_delete_count,
            "approved": False,
            "requires_approval": proposed_delete_count > 0,
        },
        {
            "operation": "delete_quantity_anomalies",
            "candidate_count": 0,
            "approved": False,
            "requires_approval": True,
            "policy": "not_proposed_without_business_rule",
        },
    ]

    requires_human_input = (
        proposed_delete_count > 0
        or proposed_delete_rate > 0.10
    )

    return {
        "inspection_type": "read_only_precleaning",
        "source_file": str(path),
        "source_rows": row_count,
        "risk_count": len(risks),
        "risks": risks,
        "proposed_operations": operations,
        "proposed_delete_count": proposed_delete_count,
        "proposed_delete_rate": round(
            proposed_delete_rate, 6
        ),
        "requires_human_input": requires_human_input,
        "execution_allowed": not requires_human_input,
        "deletion_approved": False,
        "note": (
            "本结果只包含数据风险及拟议操作。"
            "没有执行删行、填充、时间转换或文件写入。"
            "执行阶段必须再次校验实际操作是否获得授权。"
        ),
    }
