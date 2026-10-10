
"""
确定性受限清洗执行器 P0-3B。

仅执行已批准的完全重复记录删除。
不调用 LLM，不执行任意 pandas 代码。
不覆盖原始文件或已有输出文件。

注意：
本模块验证审批内容，但不验证审批人身份。
必须由可信服务传入审批记录。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any

from core.graph.cleaning_approval import (
    CleaningApprovalError,
    validate_cleaning_approval,
)


def execute_approved_cleaning(
    source_path: str | Path,
    output_path: str | Path,
    proposal: dict[str, Any],
    approval: dict[str, Any],
) -> dict[str, Any]:
    source = Path(source_path).resolve()
    output = Path(output_path).resolve()

    if source == output:
        raise CleaningApprovalError(
            "禁止覆盖原始数据文件。"
        )

    if output.exists():
        raise FileExistsError(
            f"输出文件已经存在：{output}"
        )

    # 先验证审批方案与当前源文件版本。
    verified = validate_cleaning_approval(
        source, proposal, approval
    )

    # 从磁盘读取一次快照。
    raw_bytes = source.read_bytes()

    snapshot_hash = hashlib.sha256(
        raw_bytes
    ).hexdigest()

    if snapshot_hash != verified["source_sha256"]:
        raise CleaningApprovalError(
            "验证后源文件发生变化，拒绝执行。"
        )

    content = raw_bytes.decode("utf-8-sig")

    reader = csv.DictReader(
        io.StringIO(content, newline="")
    )

    columns = reader.fieldnames

    if not columns:
        raise ValueError("CSV 缺少表头。")

    records = []
    seen = set()
    actual_duplicates = set()

    for index, row in enumerate(reader, start=2):
        # 本版审批行号按简单 CSV 的记录序号计算。
        # 对跨物理行的引号字段，先拒绝处理，
        # 避免审批行号与记录位置不一致。
        if reader.line_num != index:
            raise CleaningApprovalError(
                "检测到跨行 CSV 字段；"
                "当前执行器不支持此类行号审批。"
            )

        if None in row:
            raise ValueError(
                f"CSV 第 {index} 行字段数量异常。"
            )

        key = tuple(row.get(col) for col in columns)

        if key in seen:
            actual_duplicates.add(index)
        else:
            seen.add(key)

        records.append((index, row))

    approved_lines = set(
        verified["approved_csv_lines"]
    )

    if not approved_lines.issubset(actual_duplicates):
        raise CleaningApprovalError(
            "获批删除行不再是完全重复记录。"
        )

    # 不对未批准的重复记录进行任何删除。
    retained = [
        row
        for line, row in records
        if line not in approved_lines
    ]

    # 写入前再次确认当前源文件版本。
    if hashlib.sha256(
        source.read_bytes()
    ).hexdigest() != snapshot_hash:
        raise CleaningApprovalError(
            "执行过程中源文件发生变化。"
        )

    output.parent.mkdir(
        parents=True, exist_ok=True
    )

    # x 模式：只创建新文件，不覆盖已有文件。
    # utf-8-sig 保持与现有 CSV 读取约定兼容。
    try:
        with output.open(
            "x",
            encoding="utf-8-sig",
            newline="",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=columns,
                extrasaction="raise",
            )
            writer.writeheader()
            writer.writerows(retained)

        # 输出复核：读取落盘数据，
        # 确认记录内容与计划一致。
        with output.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as file:
            saved = list(csv.DictReader(file))

        if saved != retained:
            raise RuntimeError(
                "输出复核失败：落盘数据与批准操作不一致。"
            )

        # 检查源文件仍然没有被修改。
        if hashlib.sha256(
            source.read_bytes()
        ).hexdigest() != snapshot_hash:
            raise RuntimeError(
                "源文件发生变化，执行结果不可采信。"
            )

    except Exception:
        # 如果本次已经生成了输出，则移除不合格结果。
        # 原始文件始终不被本执行器删除。
        output.unlink(missing_ok=True)
        raise

    return {
        "execution_type": "restricted_cleaning",
        "status": "success",
        "source_file": str(source),
        "output_file": str(output),
        "source_sha256": snapshot_hash,
        "approved_operation": "remove_exact_duplicates",
        "approved_csv_lines": sorted(approved_lines),
        "source_rows": len(records),
        "output_rows": len(retained),
        "deleted_rows": len(approved_lines),
        "deletion_rate": (
            len(approved_lines) / len(records)
            if records else 0.0
        ),
        "output_verified": True,
        "source_unchanged": True,
        "note": (
            "只执行批准的重复记录删除；"
            "异常数量、时间及其他字段不做业务性修改。"
        ),
    }
