"""
物流多智能体系统统一状态。

该 State 只负责在外层 LangGraph 中传递各专业 Agent 的结果，
不改变 SQLAgent、CleaningAgent、DemandForecastAgent、
ReportAgent 本身的内部实现。
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class LogisticsAgentState(TypedDict, total=False):
    """
    外层多智能体工作流共享状态。

    所有字段均为可选字段，只有对应 Agent 实际运行后
    才会写入相应结果。
    """

    # ======================================================
    # 1. 用户任务
    # ======================================================

    user_query: str

    # 工作流模式：
    # sql_report / cleaning_report / forecast_report
    task_mode: str

    # 当前正在执行或最后执行的 Agent / Gate
    current_agent: str | None

    # ======================================================
    # 2. 数据来源
    # ======================================================

    # 原始数据文件路径
    data_file_path: str | None

    # CleaningAgent 实际落盘后的数据文件路径
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

    # Cleaning Quality Gate 的确定性检查结果
    cleaning_quality: dict[str, Any] | None

    # success / error / needs_human_input
    cleaning_quality_status: str | None

    cleaning_quality_error: str | None

    # 大比例删行是否已经得到人工确认
    #
    # False / 未设置：
    #     删除率 > 10% 时进入 HITL
    #
    # True：
    #     已获得用户或业务方明确确认，
    #     Quality Gate 可放行“大比例删除”这一项风险。
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

    # 例如：
    # missing_time
    # large_deletion
    human_reason: str | None

    human_question: str | None

    # ======================================================
    # 8. 外层工作流状态
    # ======================================================

    # running / completed / failed / needs_human_input
    status: str

    # 保存实际执行过的 Agent / Gate
    execution_trace: Annotated[
        list[str],
        operator.add,
    ]