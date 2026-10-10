
"""
清洗操作审批验证模块 P0-3A。

功能：
1. 计算源文件 SHA256
2. 根据只读预检查生成审批方案
3. 验证审批操作及候选 CSV 行号
4. 拒绝源文件变化、额外删行和未授权操作

本模块不执行清洗，不验证审批人身份。
审批记录只能由后续可信 HITL 入口提交。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from core.graph.cleaning_precheck import inspect_cleaning_risks


class CleaningApprovalError(ValueError):
    """审批方案不合法或已经失效。"""


def calculate_file_sha256(file_path: str | Path) -> str:
    path = Path(file_path).resolve()

    if not path.is_file():
        raise FileNotFoundError(path)

    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(
            lambda: file.read(1024 * 1024), b""
        ):
            digest.update(chunk)

    return digest.hexdigest()


def build_approval_proposal(
    file_path: str | Path,
) -> dict[str, Any]:
    """
    构建只读审批方案。

    唯一允许提出的删除候选：
    预检查识别出的完全重复 CSV 行。
    """
    path = Path(file_path).resolve()
    precheck = inspect_cleaning_risks(path)

    duplicate_lines = []

    for risk in precheck["risks"]:
        if risk["type"] == "exact_duplicate":
            duplicate_lines.extend(
                item["csv_line"]
                for item in risk["records"]
            )

    return {
        "proposal_version": 1,
        "source_file": str(path),
        "source_sha256": calculate_file_sha256(path),
        "source_rows": precheck["source_rows"],
        "allowed_candidate_operations": [
            {
                "operation": "remove_exact_duplicates",
                "candidate_csv_lines": sorted(
                    duplicate_lines
                ),
            }
        ],
        "forbidden_operations": [
            "delete_quantity_anomalies",
            "delete_negative_quantity",
            "delete_statistical_outliers",
        ],
        "approval_status": "pending",
    }


def validate_cleaning_approval(
    file_path: str | Path,
    proposal: dict[str, Any],
    approval: dict[str, Any],
) -> dict[str, Any]:
    """
    验证审批记录是否符合原始审批方案。

    注意：
    这里验证的是审批内容的合法性，
    而不是审批人的真实身份。
    """
    if not isinstance(proposal, dict):
        raise CleaningApprovalError(
            "审批方案必须是字典。"
        )

    if not isinstance(approval, dict):
        raise CleaningApprovalError(
            "审批记录必须是字典。"
        )

    actual_path = Path(file_path).resolve()
    actual_hash = calculate_file_sha256(actual_path)

    # 1. 源文件路径与版本一致
    if proposal.get("source_file") != str(actual_path):
        raise CleaningApprovalError(
            "审批方案对应的原始文件路径不一致。"
        )

    if proposal.get("source_sha256") != actual_hash:
        raise CleaningApprovalError(
            "原始文件内容已经变化，审批方案失效。"
        )

    if approval.get("source_sha256") != actual_hash:
        raise CleaningApprovalError(
            "审批记录未绑定当前原始文件版本。"
        )

    # 2. 使用当前文件重新计算候选操作，
    # 防止外部传入被篡改的 proposal。
    expected = build_approval_proposal(actual_path)

    if proposal != expected:
        raise CleaningApprovalError(
            "审批方案与当前只读预检查结果不一致。"
        )

    # 3. 审批状态
    if approval.get("approval_status") != "approved":
        raise CleaningApprovalError(
            "清洗操作尚未获得批准。"
        )

    if approval.get("approval_type") != "cleaning_operation":
        raise CleaningApprovalError(
            "审批类型不正确。"
        )

    # 4. 只允许明确批准的操作
    operations = approval.get("approved_operations")

    if not isinstance(operations, list):
        raise CleaningApprovalError(
            "approved_operations 必须是列表。"
        )

    if len(operations) != 1:
        raise CleaningApprovalError(
            "当前版本只支持一项明确的去重审批。"
        )

    operation = operations[0]

    if not isinstance(operation, dict):
        raise CleaningApprovalError(
            "审批操作格式错误。"
        )

    if operation.get("operation") != "remove_exact_duplicates":
        raise CleaningApprovalError(
            "存在未经允许的清洗操作。"
        )

    approved_lines = operation.get("approved_csv_lines")

    if not isinstance(approved_lines, list):
        raise CleaningApprovalError(
            "必须明确列出获批的 CSV 行号。"
        )

    if any(
        type(line) is not int or line < 2
        for line in approved_lines
    ):
        raise CleaningApprovalError(
            "CSV 行号必须是有效整数。"
        )

    if len(set(approved_lines)) != len(approved_lines):
        raise CleaningApprovalError(
            "审批行号存在重复。"
        )

    candidate_lines = set(
        expected["allowed_candidate_operations"][0][
            "candidate_csv_lines"
        ]
    )

    if not set(approved_lines).issubset(candidate_lines):
        raise CleaningApprovalError(
            "审批包含非重复记录或无效行号，拒绝删除。"
        )

    # 5. 拒绝额外字段，避免将其他操作
    # 隐藏在审批对象中。
    if set(operation) != {
        "operation", "approved_csv_lines"
    }:
        raise CleaningApprovalError(
            "审批操作存在未识别字段。"
        )

    if set(approval) != {
        "approval_type",
        "approval_status",
        "source_sha256",
        "approved_operations",
    }:
        raise CleaningApprovalError(
            "审批记录包含未识别字段。"
        )

    return {
        "valid": True,
        "source_sha256": actual_hash,
        "approved_operation": "remove_exact_duplicates",
        "approved_csv_lines": sorted(approved_lines),
        "approved_delete_count": len(approved_lines),
        "source_rows": expected["source_rows"],
        "note": (
            "审批内容验证通过，但本模块未验证"
            "审批人身份，也没有执行任何数据清洗。"
        ),
    }
