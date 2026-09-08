# ==========================================================
# 智慧仓储物流项目 V4
# Step 9.6C：
# Structured Hybrid Forecast Tool
# Timestamp Unified Interface
#
# 功能：
#
# 1. 保留底层structured_hybrid_forecast()
# 2. 新增structured_hybrid_forecast_by_time()
# 3. Qwen只需要提供forecast_timestamp
# 4. 工具自动读取：
#       当前生产状态
#       t+1计划
#       t+2计划
# 5. 输出M001~M008未来2小时需求及Peak
#
# ==========================================================


from pathlib import Path
from typing import Dict, Any

import json
import math

import pandas as pd


# ==========================================================
# 1. 项目根目录
# ==========================================================

PROJECT_ROOT = Path(__file__).resolve().parents[4]


# ==========================================================
# 2. 数据文件
# ==========================================================

PRODUCTION_FILE = (
    PROJECT_ROOT
    /
    "01_data"
    /
    "raw"
    /
    "production_data.csv"
)


# ==========================================================
# 3. 模型基本信息
# ==========================================================

MODEL_NAME = (
    "Multi-Horizon Structured Hybrid"
)

MODEL_VERSION = (
    "v4_step_9_4F"
)


RHO_1 = 0.670508

RHO_2 = 0.473079


FORECAST_HORIZON = 2


# ==========================================================
# 4. 物料与BOM
# ==========================================================

MATERIALS = [

    "M001",
    "M002",
    "M003",
    "M004",
    "M005",
    "M006",
    "M007",
    "M008",

]


BOM = {

    "A车型": {

        "M001": 1,
        "M002": 1,
        "M003": 2,
        "M004": 0,
        "M005": 0,
        "M006": 0,
        "M007": 0,
        "M008": 0,

    },

    "B车型": {

        "M001": 1,
        "M002": 0,
        "M003": 0,
        "M004": 2,
        "M005": 1,
        "M006": 0,
        "M007": 0,
        "M008": 0,

    },

    "C车型": {

        "M001": 0,
        "M002": 0,
        "M003": 0,
        "M004": 0,
        "M005": 0,
        "M006": 1,
        "M007": 1,
        "M008": 2,

    },

}


# ==========================================================
# 5. 基础检查
# ==========================================================

def validate_number(
    name: str,
    value: Any,
    minimum=None
) -> float:

    if not isinstance(
        value,
        (int, float)
    ):

        raise ValueError(
            f"{name}必须为数值"
        )


    value = float(
        value
    )


    if not math.isfinite(
        value
    ):

        raise ValueError(
            f"{name}必须为有限数值"
        )


    if (
        minimum is not None
        and
        value < minimum
    ):

        raise ValueError(
            f"{name}不能小于{minimum}"
        )


    return value


def validate_vehicle(
    vehicle_type: str
) -> str:

    if vehicle_type not in BOM:

        raise ValueError(
            f"未知车型：{vehicle_type}"
        )


    return vehicle_type


def check_file_exists():

    if not PRODUCTION_FILE.exists():

        raise FileNotFoundError(
            f"production_data.csv不存在："
            f"{PRODUCTION_FILE}"
        )


# ==========================================================
# 6. 单时距预测
# ==========================================================

def predict_one_horizon(
    *,
    horizon: int,
    rho_h: float,
    current_error: float,
    vehicle_type: str,
    planned_output: float,
    shift_factor: float,
    hour_factor: float,
) -> Dict[str, Any]:

    vehicle_type = validate_vehicle(
        vehicle_type
    )


    planned_output = validate_number(
        "planned_output",
        planned_output,
        minimum=0
    )


    shift_factor = validate_number(
        "shift_factor",
        shift_factor,
        minimum=0
    )


    hour_factor = validate_number(
        "hour_factor",
        hour_factor,
        minimum=0
    )


    # ------------------------------------------------------
    # 确定性生产量
    # ------------------------------------------------------

    deterministic_output = (

        planned_output

        *

        shift_factor

        *

        hour_factor

    )


    # ------------------------------------------------------
    # 动态执行偏差
    # ------------------------------------------------------

    predicted_error = (

        rho_h

        *

        current_error

    )


    # ------------------------------------------------------
    # 预测实际生产量
    # ------------------------------------------------------

    predicted_actual_output = (

        deterministic_output

        +

        predicted_error

    )


    predicted_actual_output = max(

        predicted_actual_output,

        0.0

    )


    # ------------------------------------------------------
    # BOM映射
    # ------------------------------------------------------

    material_demand = {}


    for material in MATERIALS:


        bom_amount = BOM[
            vehicle_type
        ][
            material
        ]


        demand = (

            predicted_actual_output

            *

            bom_amount

        )


        material_demand[
            material
        ] = round(

            float(
                demand
            ),

            4

        )


    return {

        "horizon":
            f"t+{horizon}",

        "vehicle_type":
            vehicle_type,

        "planned_output":
            planned_output,

        "shift_factor":
            shift_factor,

        "hour_factor":
            hour_factor,

        "deterministic_output":
            round(
                deterministic_output,
                4
            ),

        "predicted_production_error":
            round(
                predicted_error,
                4
            ),

        "predicted_actual_output":
            round(
                predicted_actual_output,
                4
            ),

        "material_demand":
            material_demand,

    }


# ==========================================================
# 7. 原底层Structured Hybrid计算函数
# ==========================================================

def structured_hybrid_forecast(
    *,
    current_planned_output: float,
    current_actual_output: float,
    current_shift_factor: float,
    current_hour_factor: float,

    t1_vehicle_type: str,
    t1_planned_output: float,
    t1_shift_factor: float,
    t1_hour_factor: float,

    t2_vehicle_type: str,
    t2_planned_output: float,
    t2_shift_factor: float,
    t2_hour_factor: float,

) -> Dict[str, Any]:


    # ------------------------------------------------------
    # 当前状态
    # ------------------------------------------------------

    current_planned_output = validate_number(

        "current_planned_output",

        current_planned_output,

        minimum=0

    )


    current_actual_output = validate_number(

        "current_actual_output",

        current_actual_output,

        minimum=0

    )


    current_shift_factor = validate_number(

        "current_shift_factor",

        current_shift_factor,

        minimum=0

    )


    current_hour_factor = validate_number(

        "current_hour_factor",

        current_hour_factor,

        minimum=0

    )


    # ------------------------------------------------------
    # 当前理论生产量
    # ------------------------------------------------------

    current_deterministic_output = (

        current_planned_output

        *

        current_shift_factor

        *

        current_hour_factor

    )


    # ------------------------------------------------------
    # 当前已观测执行偏差
    # ------------------------------------------------------

    current_error = (

        current_actual_output

        -

        current_deterministic_output

    )


    # ------------------------------------------------------
    # t+1
    # ------------------------------------------------------

    t1_result = predict_one_horizon(

        horizon=1,

        rho_h=RHO_1,

        current_error=current_error,

        vehicle_type=t1_vehicle_type,

        planned_output=t1_planned_output,

        shift_factor=t1_shift_factor,

        hour_factor=t1_hour_factor,

    )


    # ------------------------------------------------------
    # t+2
    # ------------------------------------------------------

    t2_result = predict_one_horizon(

        horizon=2,

        rho_h=RHO_2,

        current_error=current_error,

        vehicle_type=t2_vehicle_type,

        planned_output=t2_planned_output,

        shift_factor=t2_shift_factor,

        hour_factor=t2_hour_factor,

    )


    # ------------------------------------------------------
    # 2小时Peak
    # ------------------------------------------------------

    peak_demand = {}


    for material in MATERIALS:


        demand_t1 = (

            t1_result[
                "material_demand"
            ][
                material
            ]

        )


        demand_t2 = (

            t2_result[
                "material_demand"
            ][
                material
            ]

        )


        peak_demand[
            material
        ] = round(

            max(
                demand_t1,
                demand_t2
            ),

            4

        )


    # ------------------------------------------------------
    # 输出
    # ------------------------------------------------------

    return {

        "status":
            "success",

        "model": {

            "name":
                MODEL_NAME,

            "version":
                MODEL_VERSION,

            "rho_1":
                RHO_1,

            "rho_2":
                RHO_2,

        },

        "current_state": {

            "planned_output":
                current_planned_output,

            "actual_output":
                current_actual_output,

            "shift_factor":
                current_shift_factor,

            "hour_factor":
                current_hour_factor,

            "deterministic_output":
                round(
                    current_deterministic_output,
                    4
                ),

            "observed_production_error":
                round(
                    current_error,
                    4
                ),

        },

        "forecast": {

            "t+1":
                t1_result,

            "t+2":
                t2_result,

        },

        "two_hour_peak_demand":
            peak_demand,

    }


# ==========================================================
# 8. 读取生产数据
# ==========================================================

def load_production_data() -> pd.DataFrame:

    check_file_exists()


    production = pd.read_csv(
        PRODUCTION_FILE
    )


    if "timestamp" not in production.columns:

        raise ValueError(
            "production_data.csv缺少timestamp字段"
        )


    production[
        "timestamp"
    ] = pd.to_datetime(

        production[
            "timestamp"
        ]

    )


    production = (

        production
        .sort_values(
            "timestamp"
        )
        .reset_index(
            drop=True
        )

    )


    required_columns = [

        "timestamp",

        "vehicle_type",

        "planned_output",

        "actual_output",

        "shift_factor",

        "hour_factor",

    ]


    missing_columns = [

        column

        for column in required_columns

        if column not in production.columns

    ]


    if missing_columns:

        raise ValueError(

            "production_data.csv缺少字段："

            +

            ", ".join(
                missing_columns
            )

        )


    return production


# ==========================================================
# 9. 新统一接口
#
# forecast_timestamp表示当前已经完成观测的时刻t。
#
# 自动读取：
#
# t：
#   planned_output
#   actual_output
#   shift_factor
#   hour_factor
#
# t+1：
#   vehicle
#   planned_output
#   shift_factor
#   hour_factor
#
# t+2：
#   vehicle
#   planned_output
#   shift_factor
#   hour_factor
#
# ==========================================================

def structured_hybrid_forecast_by_time(
    forecast_timestamp: str
) -> Dict[str, Any]:


    # ------------------------------------------------------
    # 时间解析
    # ------------------------------------------------------

    try:

        forecast_time = pd.Timestamp(
            forecast_timestamp
        )

    except Exception as exc:

        raise ValueError(
            "forecast_timestamp无法解析，"
            "建议格式：YYYY-MM-DD HH:MM:SS"
        ) from exc


    production = load_production_data()


    # ------------------------------------------------------
    # 查找当前时间
    # ------------------------------------------------------

    matches = production.index[

        production[
            "timestamp"
        ]

        ==

        forecast_time

    ].tolist()


    if len(
        matches
    ) == 0:

        raise ValueError(

            f"数据中不存在预测时刻："
            f"{forecast_time}"

        )


    if len(
        matches
    ) > 1:

        raise ValueError(
            "预测时间戳存在重复记录"
        )


    current_index = int(
        matches[0]
    )


    # ------------------------------------------------------
    # 检查未来2小时
    # ------------------------------------------------------

    t1_index = (

        current_index

        +

        1

    )


    t2_index = (

        current_index

        +

        2

    )


    if t2_index >= len(
        production
    ):

        raise ValueError(
            "当前时刻之后不足2小时未来计划"
        )


    # ------------------------------------------------------
    # 连续时间检查
    # ------------------------------------------------------

    expected_t1 = (

        forecast_time

        +

        pd.Timedelta(
            hours=1
        )

    )


    expected_t2 = (

        forecast_time

        +

        pd.Timedelta(
            hours=2
        )

    )


    actual_t1 = production.loc[
        t1_index,
        "timestamp"
    ]


    actual_t2 = production.loc[
        t2_index,
        "timestamp"
    ]


    if actual_t1 != expected_t1:

        raise ValueError(
            "t+1生产计划时间不连续"
        )


    if actual_t2 != expected_t2:

        raise ValueError(
            "t+2生产计划时间不连续"
        )


    # ------------------------------------------------------
    # 当前状态
    # ------------------------------------------------------

    current_row = production.loc[
        current_index
    ]


    # ------------------------------------------------------
    # t+1计划
    # ------------------------------------------------------

    t1_row = production.loc[
        t1_index
    ]


    # ------------------------------------------------------
    # t+2计划
    # ------------------------------------------------------

    t2_row = production.loc[
        t2_index
    ]


    # ------------------------------------------------------
    # 调用原Structured Hybrid
    # ------------------------------------------------------

    result = structured_hybrid_forecast(

        current_planned_output=
            float(
                current_row[
                    "planned_output"
                ]
            ),

        current_actual_output=
            float(
                current_row[
                    "actual_output"
                ]
            ),

        current_shift_factor=
            float(
                current_row[
                    "shift_factor"
                ]
            ),

        current_hour_factor=
            float(
                current_row[
                    "hour_factor"
                ]
            ),

        t1_vehicle_type=
            str(
                t1_row[
                    "vehicle_type"
                ]
            ),

        t1_planned_output=
            float(
                t1_row[
                    "planned_output"
                ]
            ),

        t1_shift_factor=
            float(
                t1_row[
                    "shift_factor"
                ]
            ),

        t1_hour_factor=
            float(
                t1_row[
                    "hour_factor"
                ]
            ),

        t2_vehicle_type=
            str(
                t2_row[
                    "vehicle_type"
                ]
            ),

        t2_planned_output=
            float(
                t2_row[
                    "planned_output"
                ]
            ),

        t2_shift_factor=
            float(
                t2_row[
                    "shift_factor"
                ]
            ),

        t2_hour_factor=
            float(
                t2_row[
                    "hour_factor"
                ]
            ),

    )


    # ------------------------------------------------------
    # 增加时间信息
    # ------------------------------------------------------

    result[
        "forecast_timestamp"
    ] = str(
        forecast_time
    )


    result[
        "current_state"
    ][
        "timestamp"
    ] = str(
        current_row[
            "timestamp"
        ]
    )


    result[
        "current_state"
    ][
        "vehicle_type"
    ] = str(
        current_row[
            "vehicle_type"
        ]
    )


    result[
        "forecast"
    ][
        "t+1"
    ][
        "timestamp"
    ] = str(
        t1_row[
            "timestamp"
        ]
    )


    result[
        "forecast"
    ][
        "t+2"
    ][
        "timestamp"
    ] = str(
        t2_row[
            "timestamp"
        ]
    )


    return result


# ==========================================================
# 10. 新Qwen Schema
#
# 现在只传一个forecast_timestamp。
# ==========================================================

STRUCTURED_HYBRID_TIME_TOOL_SCHEMA = {

    "type":
        "function",

    "function": {

        "name":
            "structured_hybrid_forecast_by_time",

        "description":
            (
                "调用Multi-Horizon Structured Hybrid专业预测模型，"
                "根据指定预测基准时刻自动读取当前生产状态"
                "以及未来2小时生产计划，"
                "预测M001至M008未来t+1、t+2物料需求"
                "及两小时需求峰值。"
                "适用于高精度物料需求、补货和配送场景。"
            ),

        "parameters": {

            "type":
                "object",

            "properties": {

                "forecast_timestamp": {

                    "type":
                        "string",

                    "description":
                        (
                            "当前已经完成观测的预测基准时刻t，"
                            "格式建议YYYY-MM-DD HH:MM:SS。"
                        )

                }

            },

            "required": [

                "forecast_timestamp"

            ],

            "additionalProperties":
                False

        }

    }

}


# ==========================================================
# 11. 本地测试
# ==========================================================

def main():

    print(
        "=============================================="
    )

    print(
        "Structured Hybrid Timestamp Tool 本地测试"
    )

    print(
        "=============================================="
    )


    test_timestamp = (
        "2026-01-30 12:00:00"
    )


    print(
        "\n测试预测时刻:",
        test_timestamp
    )


    result = (
        structured_hybrid_forecast_by_time(

            forecast_timestamp=
                test_timestamp

        )
    )


    print(
        "\n=============================================="
    )

    print(
        "预测结果"
    )

    print(
        "=============================================="
    )


    print(

        json.dumps(

            result,

            ensure_ascii=False,

            indent=2

        )

    )


    print(
        "\n=============================================="
    )

    print(
        "统一时间戳Tool Schema"
    )

    print(
        "=============================================="
    )


    print(

        json.dumps(

            STRUCTURED_HYBRID_TIME_TOOL_SCHEMA,

            ensure_ascii=False,

            indent=2

        )

    )


if __name__ == "__main__":

    main()