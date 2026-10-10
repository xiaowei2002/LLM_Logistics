
"""
Cleaning Precheck 节点。

功能：
1. 对原始文件进行只读风险检查
2. 生成与 SHA256 绑定的清洗审批方案
3. 未批准时触发事前 HITL
4. 收到审批记录后执行确定性合法性验证
5. 通过验证后才允许进入受限清洗执行节点

不调用 LLM，不修改数据文件。

注意：
这里验证审批内容，不验证审批人身份。
"""

from __future__ import annotations

from core.graph.cleaning_approval import (
    CleaningApprovalError,
    build_approval_proposal,
    validate_cleaning_approval,
)
from core.graph.cleaning_precheck import (
    inspect_cleaning_risks,
)
from core.graph.state import (
    LogisticsAgentState,
)


def cleaning_precheck_node(
    state: LogisticsAgentState,
) -> LogisticsAgentState:

    source_path = state.get("data_file_path")
    approval = state.get("cleaning_approval")

    try:
        precheck = inspect_cleaning_risks(source_path)

        proposal = build_approval_proposal(
            source_path
        )

        candidate_lines = proposal[
            "allowed_candidate_operations"
        ][0]["candidate_csv_lines"]

        # ----------------------------------------------
        # 1. 已提交审批记录：重新校验
        # ----------------------------------------------
        if approval is not None:

            verified = validate_cleaning_approval(
                source_path,
                proposal,
                approval,
            )

            return {
                "current_agent": "cleaning_precheck",
                "cleaning_precheck": precheck,
                "cleaning_approval_proposal": proposal,
                "cleaning_approval_validation": verified,
                "cleaning_precheck_status": "success",
                "cleaning_precheck_error": None,
                "needs_human_input": False,
                "human_reason": None,
                "human_question": None,
                "status": "running",
                "execution_trace": ["cleaning_precheck"],
            }

        # ----------------------------------------------
        # 2. 存在删除候选：等待人工审批
        # ----------------------------------------------
        if candidate_lines:

            question = (
                "清洗前风险检查发现 "
                f"{precheck['risk_count']} 项风险。"
                f"其中完全重复记录候选行为 {candidate_lines}，"
                "请审核是否允许删除这些具体记录。"
                "负数、极端数量和其他异常记录不会"
                "因为去重审批而获得删除授权。"
            )

            return {
                "current_agent": "cleaning_precheck",
                "cleaning_precheck": precheck,
                "cleaning_approval_proposal": proposal,
                "cleaning_approval_validation": None,
                "cleaning_precheck_status": (
                    "needs_human_input"
                ),
                "cleaning_precheck_error": None,
                "needs_human_input": True,
                "human_reason": (
                    "precleaning_approval"
                ),
                "human_question": question,
                "status": "needs_human_input",
                "execution_trace": ["cleaning_precheck"],
            }

        # ----------------------------------------------
        # 3. 无删行候选：允许零删除执行
        # ----------------------------------------------
        # 仅表示可执行不删行的受限处理，
        # 不生成或伪造人工审批记录。

        return {
            "current_agent": "cleaning_precheck",
            "cleaning_precheck": precheck,
            "cleaning_approval_proposal": proposal,
            "cleaning_approval_validation": None,
            "cleaning_precheck_status": "success",
            "cleaning_precheck_error": None,
            "needs_human_input": False,
            "human_reason": None,
            "human_question": None,
            "status": "running",
            "execution_trace": ["cleaning_precheck"],
        }

    except (
        CleaningApprovalError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
    ) as exc:

        return {
            "current_agent": "cleaning_precheck",
            "cleaning_precheck_status": "error",
            "cleaning_precheck_error": str(exc),
            "cleaning_approval_validation": None,
            "needs_human_input": False,
            "human_reason": None,
            "human_question": None,
            "status": "failed",
            "execution_trace": ["cleaning_precheck"],
        }
