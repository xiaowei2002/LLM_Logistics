
"""
Safe Cleaning 事后 HITL 恢复验证节点。

职责：
1. 重新验证原始文件 SHA256 和操作审批
2. 验证执行证据与审批内容一致
3. 验证清洗结果文件 SHA256
4. 逐行核对清洗结果是否恰好对应获批删行
5. 验证通过后进入 Post-Cleaning Quality Gate

不重新执行 CleaningAgent 或 Restricted Executor。

注意：
本节点验证数据与状态的一致性，
不验证审批人员的身份或授权来源。
"""

from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path
from typing import Any

from core.graph.cleaning_approval import (
    CleaningApprovalError,
    build_approval_proposal,
    validate_cleaning_approval,
)
from core.graph.state import LogisticsAgentState


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_csv_snapshot(
    data: bytes,
) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    """
    读取简单 CSV 快照。

    与当前受限执行器保持相同的行号约定：
    表头为第 1 行，数据行从第 2 行开始。
    拒绝跨物理行的 CSV 字段。
    """
    content = data.decode("utf-8-sig")

    reader = csv.DictReader(
        io.StringIO(content, newline="")
    )

    columns = reader.fieldnames

    if not columns:
        raise CleaningApprovalError(
            "CSV 缺少表头。"
        )

    if len(columns) != len(set(columns)):
        raise CleaningApprovalError(
            "CSV 表头存在重复字段名。"
        )

    records = []

    for line_number, row in enumerate(
        reader, start=2
    ):
        if reader.line_num != line_number:
            raise CleaningApprovalError(
                "CSV 存在跨行字段，无法安全核对审批行号。"
            )

        if None in row or any(
            value is None for value in row.values()
        ):
            raise CleaningApprovalError(
                "CSV 字段数量不一致。"
            )

        records.append((line_number, row))

    return columns, records


def validate_safe_cleaning_resume(
    state: LogisticsAgentState,
) -> dict[str, Any]:
    """
    对已生成的清洗结果进行确定性恢复校验。
    """

    if state.get("task_mode") != "cleaning_safe_report":
        raise CleaningApprovalError(
            "当前任务不是安全清洗流程。"
        )

    if state.get(
        "cleaning_large_deletion_approved"
    ) is not True:
        raise CleaningApprovalError(
            "尚未确认事后大比例删行风险。"
        )

    source_value = state.get("data_file_path")
    cleaned_value = state.get("cleaned_file_path")
    approval = state.get("cleaning_approval")
    execution = state.get("cleaning_execution")

    if not source_value or not cleaned_value:
        raise CleaningApprovalError(
            "缺少原始文件或清洗结果路径。"
        )

    if not isinstance(approval, dict):
        raise CleaningApprovalError(
            "缺少具体操作审批记录。"
        )

    if not isinstance(execution, dict):
        raise CleaningApprovalError(
            "缺少受限清洗执行证据。"
        )

    source = Path(source_value).resolve()
    cleaned = Path(cleaned_value).resolve()

    if source == cleaned:
        raise CleaningApprovalError(
            "清洗结果不能覆盖原始文件。"
        )

    if not source.is_file() or not cleaned.is_file():
        raise CleaningApprovalError(
            "原始文件或清洗结果文件不存在。"
        )

    # 第一层：重新验证操作审批
    proposal = build_approval_proposal(source)

    verified = validate_cleaning_approval(
        source,
        proposal,
        approval,
    )

    source_bytes = source.read_bytes()
    cleaned_bytes = cleaned.read_bytes()

    source_hash = _sha256_bytes(source_bytes)
    cleaned_hash = _sha256_bytes(cleaned_bytes)

    if source_hash != verified["source_sha256"]:
        raise CleaningApprovalError(
            "原始数据版本发生变化。"
        )

    # 第二层：执行证据必须与当前文件匹配
    if execution.get("execution_type") != "restricted_cleaning":
        raise CleaningApprovalError(
            "执行证据不是受限清洗执行器生成的格式。"
        )

    if execution.get("status") != "success":
        raise CleaningApprovalError(
            "受限清洗执行记录不是成功状态。"
        )

    if execution.get("source_file") != str(source):
        raise CleaningApprovalError(
            "执行证据中的原始文件路径不一致。"
        )

    if execution.get("output_file") != str(cleaned):
        raise CleaningApprovalError(
            "执行证据中的输出文件路径不一致。"
        )

    if execution.get("source_sha256") != source_hash:
        raise CleaningApprovalError(
            "执行证据的源文件哈希不一致。"
        )

    if execution.get("output_sha256") != cleaned_hash:
        raise CleaningApprovalError(
            "清洗输出文件已经变化，拒绝恢复。"
        )

    approved_lines = verified["approved_csv_lines"]

    if execution.get("approved_operation") != (
        "remove_exact_duplicates"
    ):
        raise CleaningApprovalError(
            "执行操作类型不符合审批范围。"
        )

    if execution.get("approved_csv_lines") != approved_lines:
        raise CleaningApprovalError(
            "执行记录中的获批行号不一致。"
        )

    if execution.get("deleted_rows") != len(approved_lines):
        raise CleaningApprovalError(
            "执行删行数量与审批数量不一致。"
        )

    # 第三层：核对最终数据内容
    source_columns, source_records = _read_csv_snapshot(
        source_bytes
    )
    output_columns, output_records = _read_csv_snapshot(
        cleaned_bytes
    )

    if source_columns != output_columns:
        raise CleaningApprovalError(
            "输出字段结构发生未经授权的变化。"
        )

    approved_set = set(approved_lines)

    expected_rows = [
        row
        for line, row in source_records
        if line not in approved_set
    ]

    actual_rows = [
        row for _, row in output_records
    ]

    if actual_rows != expected_rows:
        raise CleaningApprovalError(
            "输出记录与获批删行方案不一致。"
        )

    if execution.get("source_rows") != len(source_records):
        raise CleaningApprovalError(
            "源记录数量与执行证据不一致。"
        )

    if execution.get("output_rows") != len(actual_rows):
        raise CleaningApprovalError(
            "输出记录数量与执行证据不一致。"
        )

    return {
        "valid": True,
        "source_sha256": source_hash,
        "output_sha256": cleaned_hash,
        "approved_csv_lines": approved_lines,
        "source_rows": len(source_records),
        "output_rows": len(actual_rows),
        "deleted_rows": len(approved_lines),
    }


def cleaning_resume_node(
    state: LogisticsAgentState,
) -> LogisticsAgentState:
    try:
        validation = validate_safe_cleaning_resume(
            state
        )

        return {
            "current_agent": "cleaning_resume",
            "cleaning_approval_validation": validation,
            "cleaning_status": "success",
            "cleaning_error": None,
            "needs_human_input": False,
            "human_reason": None,
            "human_question": None,
            "status": "running",
            "execution_trace": ["cleaning_resume"],
        }

    except (
        CleaningApprovalError,
        OSError,
        ValueError,
        TypeError,
        UnicodeError,
        KeyError,
    ) as exc:
        return {
            "current_agent": "cleaning_resume",
            "cleaning_status": "error",
            "cleaning_error": str(exc),
            "needs_human_input": False,
            "human_reason": None,
            "human_question": None,
            "status": "failed",
            "execution_trace": ["cleaning_resume"],
        }