
"""
物流多智能体系统统一状态。

在保留原有专业 Agent 接口的基础上，
增加清洗前审批、受限执行及审计字段。
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class LogisticsAgentState(TypedDict, total=False):
    """
    外层多智能体工作流共享状态。

    所有字段均为可选字段。
    """

    # ======================================================
    # 1. 用户任务
    # ======================================================

    user_query: str

    # sql_report / cleaning_report /
    # cleaning_safe_report / forecast_report
    task_mode: str

    current_agent: str | None

    # ======================================================
    # 2. 数据来源
    # ======================================================

    data_file_path: str | None
    cleaned_file_path: str | None

    # ======================================================
    # 3. SQL / Data Query
    # ======================================================

    sql_result: str | None
    sql_status: str | None
    sql_error: str | None

    # ======================================================
    # 4. Cleaning
    # ======================================================

    cleaning_result: str | None
    cleaning_status: str | None
    cleaning_error: str | None

    # ----------------------------------------------
    # 清洗前只读检查
    # ----------------------------------------------

    cleaning_precheck: dict[str, Any] | None
    cleaning_precheck_status: str | None
    cleaning_precheck_error: str | None

    # 保留旧字段以避免其他调用方读取时报错。
    # 新安全流程不再信任这个布尔值。
    cleaning_precheck_approved: bool

    # ----------------------------------------------
    # 操作级审批
    # ----------------------------------------------

    # 由只读检查生成的审批方案
    cleaning_approval_proposal: dict[str, Any] | None

    # 外部提交的具体审批记录
    cleaning_approval: dict[str, Any] | None

    # 审批内容验证结果
    cleaning_approval_validation: dict[str, Any] | None

    # ----------------------------------------------
    # 受限清洗执行结果
    # ----------------------------------------------

    cleaning_execution: dict[str, Any] | None

    # ----------------------------------------------
    # Post-Cleaning Quality Gate
    # ----------------------------------------------

    cleaning_quality: dict[str, Any] | None

    # success / error / needs_human_input
    cleaning_quality_status: str | None

    cleaning_quality_error: str | None

    # 原有大比例删行事后 HITL 字段
    cleaning_large_deletion_approved: bool

    # ======================================================
    # 5. Demand Forecast
    # ======================================================

    forecast_result: dict[str, Any] | None

    forecast_status: str | None
    forecast_error: str | None

    forecast_thread_id: str | None

    # ======================================================
    # 6. Report
    # ======================================================

    report_result: str | None
    report_status: str | None
    report_error: str | None

    # ======================================================
    # 7. Human-in-the-loop
    # ======================================================

    needs_human_input: bool

    human_reason: str | None
    human_question: str | None

    # ======================================================
    # 8. 外层工作流状态
    # ======================================================

    # running / completed / failed / needs_human_input
    status: str

    execution_trace: Annotated[
        list[str],
        operator.add,
    ]