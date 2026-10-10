
"""
受限 Cleaning 执行节点。

仅调用确定性执行器，不调用原有 CleaningAgent。

输出文件放在独立临时目录中，
并记录输出 SHA256，供 HITL 恢复校验使用。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from core.graph.cleaning_approval import (
    CleaningApprovalError,
    build_approval_proposal,
    validate_cleaning_approval,
)
from core.graph.cleaning_executor import (
    execute_approved_cleaning,
)
from core.graph.state import LogisticsAgentState


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(
            lambda: file.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def restricted_cleaning_node(
    state: LogisticsAgentState,
) -> LogisticsAgentState:

    source_path = state.get("data_file_path")
    approval = state.get("cleaning_approval")

    output_dir = None

    try:
        if not source_path:
            raise CleaningApprovalError(
                "缺少原始数据文件路径。"
            )

        source = Path(source_path).resolve()

        proposal = build_approval_proposal(
            source
        )

        candidate_lines = proposal[
            "allowed_candidate_operations"
        ][0]["candidate_csv_lines"]

        # 未提交审批时，只允许无删除候选的零删除执行。
        if approval is None:
            if candidate_lines:
                raise CleaningApprovalError(
                    "存在删除候选，但没有具体操作审批记录。"
                )

            execution_approval = {
                "approval_type": "cleaning_operation",
                "approval_status": "approved",
                "source_sha256": proposal["source_sha256"],
                "approved_operations": [
                    {
                        "operation": "remove_exact_duplicates",
                        "approved_csv_lines": [],
                    }
                ],
            }
        else:
            execution_approval = approval

        verified = validate_cleaning_approval(
            source,
            proposal,
            execution_approval,
        )

        output_dir = Path(
            tempfile.mkdtemp(
                prefix="restricted_cleaning_"
            )
        )

        output_path = (
            output_dir /
            f"{source.stem}_cleaned.csv"
        )

        execution = execute_approved_cleaning(
            source_path=source,
            output_path=output_path,
            proposal=proposal,
            approval=execution_approval,
        )

        if not execution.get("output_verified"):
            raise RuntimeError(
                "清洗输出未通过复核。"
            )

        if not execution.get("source_unchanged"):
            raise RuntimeError(
                "原始数据完整性验证失败。"
            )

        if execution.get("deleted_rows") != (
            verified["approved_delete_count"]
        ):
            raise RuntimeError(
                "实际删除数量与审批数量不一致。"
            )

        # 记录真实输出文件哈希，用于安全恢复。
        execution["output_sha256"] = (
            _file_sha256(output_path)
        )

        return {
            "current_agent": "restricted_cleaning",
            "cleaning_result": json.dumps(
                execution,
                ensure_ascii=False,
                indent=2,
            ),
            "cleaned_file_path": str(output_path),
            "cleaning_execution": execution,
            "cleaning_status": "success",
            "cleaning_error": None,
            "status": "running",
            "execution_trace": ["restricted_cleaning"],
        }

    except (
        CleaningApprovalError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
    ) as exc:

        if output_dir is not None:
            shutil.rmtree(
                output_dir,
                ignore_errors=True,
            )

        return {
            "current_agent": "restricted_cleaning",
            "cleaned_file_path": None,
            "cleaning_execution": None,
            "cleaning_status": "error",
            "cleaning_error": str(exc),
            "status": "failed",
            "execution_trace": ["restricted_cleaning"],
        }
