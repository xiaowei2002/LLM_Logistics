from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


MAX_AUTOMATIC_REMOVAL_RATE = 0.10


def _read_dataframe(
    file_path: Path,
) -> pd.DataFrame:
    """
    根据文件扩展名读取数据。
    """

    suffix = file_path.suffix.lower()

    if suffix == ".csv":
        return pd.read_csv(file_path)

    if suffix in {
        ".xlsx",
        ".xls",
    }:
        return pd.read_excel(file_path)

    raise ValueError(
        f"暂不支持的数据文件格式：{suffix}"
    )


def validate_cleaned_file(
    file_path: str | None,
    source_file_path: str | None = None,
) -> dict[str, Any]:
    """
    对 CleaningAgent 实际落盘的数据进行确定性质量检查。

    参数
    ----
    file_path:
        清洗后文件路径。

    source_file_path:
        原始数据文件路径。
        如果提供，则额外进行清洗前后行数对比，
        检查是否发生超过 10% 的自动删行。

    当前检查包括：
    1. 清洗后文件是否真实存在
    2. 是否能够重新读取
    3. 行列规模
    4. 缺失值
    5. 完全重复记录
    6. qty 是否仍存在负值
    7. 原始时间信息是否保留
    8. 原始数据与清洗后数据的行数变化
    9. 自动删除比例是否超过 10%

    本 Gate 不替代具体业务规则判断。
    """

    result: dict[str, Any] = {
        "passed": False,

        # ----------------------------------------------
        # 文件信息
        # ----------------------------------------------
        "file_path": file_path,
        "source_file_path": source_file_path,

        # ----------------------------------------------
        # 清洗后数据结构
        # ----------------------------------------------
        "row_count": None,
        "column_count": None,
        "columns": [],

        # ----------------------------------------------
        # 清洗前后行数比较
        # ----------------------------------------------
        "source_row_count": None,
        "cleaned_row_count": None,
        "removed_row_count": None,
        "removal_rate": None,
        "large_deletion_detected": False,

        # ----------------------------------------------
        # 数据质量
        # ----------------------------------------------
        "missing_values": {},
        "duplicate_rows": None,
        "negative_qty_count": None,
        "time_raw_preserved": None,

        # ----------------------------------------------
        # Gate 信息
        # ----------------------------------------------
        "errors": [],
        "warnings": [],
    }

    # ==================================================
    # 1. 清洗后文件路径检查
    # ==================================================

    if not file_path:
        result["errors"].append(
            "CleaningAgent 未提供清洗后文件路径。"
        )
        return result

    cleaned_path = Path(
        file_path
    )

    if not cleaned_path.exists():
        result["errors"].append(
            f"清洗后文件不存在：{cleaned_path}"
        )
        return result

    if not cleaned_path.is_file():
        result["errors"].append(
            f"清洗结果路径不是文件：{cleaned_path}"
        )
        return result

    # ==================================================
    # 2. 读取清洗后文件
    # ==================================================

    try:
        cleaned_df = _read_dataframe(
            cleaned_path
        )

    except Exception as exc:
        result["errors"].append(
            f"无法重新读取清洗文件：{exc}"
        )
        return result

    # ==================================================
    # 3. 清洗后基础结构
    # ==================================================

    cleaned_row_count = int(
        len(cleaned_df)
    )

    result["row_count"] = (
        cleaned_row_count
    )

    result["cleaned_row_count"] = (
        cleaned_row_count
    )

    result["column_count"] = int(
        len(cleaned_df.columns)
    )

    result["columns"] = [
        str(column)
        for column in cleaned_df.columns
    ]

    if cleaned_df.empty:
        result["errors"].append(
            "清洗后数据为空。"
        )

    # ==================================================
    # 4. 原始数据与清洗后数据行数比较
    # ==================================================

    if source_file_path:
        source_path = Path(
            source_file_path
        )

        if not source_path.exists():
            result["errors"].append(
                f"原始数据文件不存在：{source_path}"
            )

        elif not source_path.is_file():
            result["errors"].append(
                f"原始数据路径不是文件：{source_path}"
            )

        else:
            try:
                source_df = _read_dataframe(
                    source_path
                )

                source_row_count = int(
                    len(source_df)
                )

                result[
                    "source_row_count"
                ] = source_row_count

                removed_row_count = (
                    source_row_count
                    - cleaned_row_count
                )

                result[
                    "removed_row_count"
                ] = removed_row_count

                # --------------------------------------
                # 原始文件非空时计算删行率
                # --------------------------------------

                if source_row_count > 0:
                    removal_rate = (
                        removed_row_count
                        / source_row_count
                    )

                    result[
                        "removal_rate"
                    ] = removal_rate

                    # ----------------------------------
                    # 删除超过 10%
                    # ----------------------------------

                    if (
                        removed_row_count > 0
                        and removal_rate
                        > MAX_AUTOMATIC_REMOVAL_RATE
                    ):
                        result[
                            "large_deletion_detected"
                        ] = True

                        result["errors"].append(
                            (
                                "清洗过程自动删除数据比例过高："
                                f"原始 {source_row_count} 行，"
                                f"清洗后 {cleaned_row_count} 行，"
                                f"共删除 {removed_row_count} 行，"
                                f"删除比例为 "
                                f"{removal_rate:.2%}，"
                                "超过允许的 10%。"
                                "根据数据清洗安全规则，"
                                "必须先由用户或业务方确认，"
                                "不得自动继续进入下游流程。"
                            )
                        )

                    # ----------------------------------
                    # 行数增加
                    # ----------------------------------

                    elif removed_row_count < 0:
                        added_count = abs(
                            removed_row_count
                        )

                        result["warnings"].append(
                            (
                                "清洗后数据行数增加："
                                f"原始 {source_row_count} 行，"
                                f"清洗后 {cleaned_row_count} 行，"
                                f"增加 {added_count} 行。"
                                "请确认是否为预期的数据展开、"
                                "拆分或补充操作。"
                            )
                        )

            except Exception as exc:
                result["errors"].append(
                    (
                        "无法读取原始数据文件进行"
                        f"清洗前后比较：{exc}"
                    )
                )

    else:
        result["warnings"].append(
            (
                "未提供原始数据文件路径，"
                "无法计算清洗前后行数变化和删行比例。"
            )
        )

    # ==================================================
    # 5. 缺失值检查
    # ==================================================

    missing = cleaned_df.isna().sum()

    result["missing_values"] = {
        str(column): int(count)
        for column, count
        in missing.items()
        if int(count) > 0
    }

    if result["missing_values"]:
        result["warnings"].append(
            (
                "清洗后仍存在缺失值；"
                "是否允许需要结合业务规则判断。"
            )
        )

    # ==================================================
    # 6. 完全重复记录检查
    # ==================================================

    duplicate_rows = int(
        cleaned_df.duplicated().sum()
    )

    result[
        "duplicate_rows"
    ] = duplicate_rows

    if duplicate_rows > 0:
        result["warnings"].append(
            (
                "清洗后仍存在 "
                f"{duplicate_rows} "
                "条完全重复记录。"
            )
        )

    # ==================================================
    # 7. qty 基础异常检查
    # ==================================================

    if "qty" in cleaned_df.columns:
        qty = pd.to_numeric(
            cleaned_df["qty"],
            errors="coerce",
        )

        negative_qty_count = int(
            (qty < 0).sum()
        )

        result[
            "negative_qty_count"
        ] = negative_qty_count

        if negative_qty_count > 0:
            result["errors"].append(
                (
                    "qty 中仍存在 "
                    f"{negative_qty_count} "
                    "个负值。"
                )
            )

    # ==================================================
    # 8. 时间信息保护检查
    # ==================================================

    if (
        "created_at_raw"
        in cleaned_df.columns
    ):
        raw_missing = int(
            cleaned_df[
                "created_at_raw"
            ]
            .isna()
            .sum()
        )

        result[
            "time_raw_preserved"
        ] = raw_missing == 0

        if raw_missing > 0:
            result["warnings"].append(
                (
                    "created_at_raw 存在缺失，"
                    "部分原始时间信息可能"
                    "未被完整保留。"
                )
            )

    elif (
        "created_at"
        in cleaned_df.columns
    ):
        # 原始时间字段仍存在，
        # 暂时认为仍有原始时间载体。
        result[
            "time_raw_preserved"
        ] = True

    else:
        result[
            "time_raw_preserved"
        ] = None

    # ==================================================
    # 9. 最终 Gate 判断
    # ==================================================

    result["passed"] = (
        len(result["errors"]) == 0
    )

    return result