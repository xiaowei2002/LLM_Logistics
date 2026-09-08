# ==========================================================
# logistics_prediction_v4
# Qwen Forecast Tool:
# State-Conditioned Structured Hybrid (Experiment 3)
#
# 文件位置：
#   03_llm_agent/tools/state_conditioned_forecast_tool.py
#
# 功能：
#   1. 从 Experiment 3 业务数据读取当前状态与未来2h计划
#   2. 严格按 Experiment 3 已冻结的 Train/Val 协议重建模型
#   3. 支持：
#        baseline_sh
#        state_only
#        state_conditioned
#   4. 输出未来2h M001~M008具体需求
#
# 说明：
#   - Qwen只负责调用本工具，不直接计算预测值。
#   - 不读取 experiment_3_latent_diagnostic.csv。
#   - 不使用未来 actual_output / true error / true material demand。
#   - 模型在首次调用时构建一次，后续进程内缓存复用。
# ==========================================================

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple, Any

import numpy as np
import pandas as pd

from sklearn.linear_model import Ridge, LinearRegression
from sklearn.preprocessing import StandardScaler


# ==========================================================
# 1. 路径与参数
# ==========================================================

THIS_FILE = Path(__file__).resolve()

# .../logistics_prediction_v4/03_llm_agent/tools/xxx.py
PROJECT_ROOT = Path(__file__).resolve().parents[4]

RAW_DIR = (
    PROJECT_ROOT
    / "01_data"
    / "experiment_3"
    / "raw"
)

PRODUCTION_FILE = (
    RAW_DIR
    / "production_data.csv"
)

METRICS_FILE = (
    PROJECT_ROOT
    / "05_results"
    / "experiment_3"
    / "csv"
    / "e3_state_conditioned_metrics.csv"
)

HISTORY_WINDOW = 24
FORECAST_HORIZON = 2

TRAIN_RATIO = 0.70
VAL_RATIO = 0.15

RIDGE_ALPHAS = [
    0.0,
    0.01,
    0.1,
    1.0,
    10.0,
]

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
        "M001": 1, "M002": 1, "M003": 2, "M004": 0,
        "M005": 0, "M006": 0, "M007": 0, "M008": 0,
    },
    "B车型": {
        "M001": 1, "M002": 0, "M003": 0, "M004": 2,
        "M005": 1, "M006": 0, "M007": 0, "M008": 0,
    },
    "C车型": {
        "M001": 0, "M002": 0, "M003": 0, "M004": 0,
        "M005": 0, "M006": 1, "M007": 1, "M008": 2,
    },
}


# ==========================================================
# 2. 进程内缓存
# ==========================================================

_CACHE: Dict[str, Any] = {}


# ==========================================================
# 3. 基础函数
# ==========================================================

def _vehicle_one_hot(vehicle: str) -> Dict[str, float]:

    return {
        "vehicle_A": 1.0 if vehicle == "A车型" else 0.0,
        "vehicle_B": 1.0 if vehicle == "B车型" else 0.0,
        "vehicle_C": 1.0 if vehicle == "C车型" else 0.0,
    }


def _load_production() -> pd.DataFrame:

    if not PRODUCTION_FILE.exists():
        raise FileNotFoundError(
            f"Experiment 3 production_data.csv不存在: {PRODUCTION_FILE}"
        )

    production = pd.read_csv(
        PRODUCTION_FILE
    )

    required = [
        "timestamp",
        "vehicle_type",
        "planned_output",
        "actual_output",
        "shift_factor",
        "hour_factor",
        "machine_load",
        "material_supply_status",
        "changeover_progress",
        "order_pressure",
        "line_congestion",
    ]

    missing = [
        column
        for column in required
        if column not in production.columns
    ]

    if missing:
        raise ValueError(
            f"Experiment 3 production_data.csv缺少字段: {missing}"
        )

    production["timestamp"] = pd.to_datetime(
        production["timestamp"]
    )

    production = (
        production
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    production["deterministic_output"] = (
        production["planned_output"]
        *
        production["shift_factor"]
        *
        production["hour_factor"]
    )

    production["stochastic_error"] = (
        production["actual_output"]
        -
        production["deterministic_output"]
    )

    return production


# ==========================================================
# 4. 构造与 Experiment 3 完全一致的样本特征
# ==========================================================

def _build_samples(
    production: pd.DataFrame
) -> Tuple[pd.DataFrame, np.ndarray, pd.DataFrame]:

    feature_records: List[Dict[str, float]] = []
    target_errors: List[List[float]] = []
    metadata: List[Dict[str, Any]] = []

    for history_end in range(
        HISTORY_WINDOW - 1,
        len(production) - FORECAST_HORIZON
    ):

        row_t = production.loc[
            history_end
        ]

        record: Dict[str, float] = {
            "current_error":
                float(
                    row_t["stochastic_error"]
                ),

            "machine_load_t":
                float(
                    row_t["machine_load"]
                ),

            "material_supply_status_t":
                float(
                    row_t["material_supply_status"]
                ),

            "line_congestion_t":
                float(
                    row_t["line_congestion"]
                ),

            "changeover_progress_t":
                float(
                    row_t["changeover_progress"]
                ),

            "order_pressure_t":
                float(
                    row_t["order_pressure"]
                ),
        }

        y_row: List[float] = []

        for horizon in [
            1,
            2
        ]:

            future_index = (
                history_end
                +
                horizon
            )

            future_row = (
                production.loc[
                    future_index
                ]
            )

            record[
                f"planned_output_t{horizon}"
            ] = float(
                future_row[
                    "planned_output"
                ]
            )

            record[
                f"shift_factor_t{horizon}"
            ] = float(
                future_row[
                    "shift_factor"
                ]
            )

            record[
                f"hour_factor_t{horizon}"
            ] = float(
                future_row[
                    "hour_factor"
                ]
            )

            record[
                f"order_pressure_t{horizon}"
            ] = float(
                future_row[
                    "order_pressure"
                ]
            )

            record[
                f"changeover_progress_t{horizon}"
            ] = float(
                future_row[
                    "changeover_progress"
                ]
            )

            one_hot = _vehicle_one_hot(
                str(
                    future_row[
                        "vehicle_type"
                    ]
                )
            )

            for key, value in one_hot.items():

                record[
                    f"{key}_t{horizon}"
                ] = value

            y_row.append(
                float(
                    future_row[
                        "stochastic_error"
                    ]
                )
            )

        sample_id = len(
            feature_records
        )

        feature_records.append(
            record
        )

        target_errors.append(
            y_row
        )

        metadata.append({
            "sample_id":
                sample_id,

            "history_end":
                row_t[
                    "timestamp"
                ],

            "history_end_index":
                history_end,

            "t_plus_1":
                production.loc[
                    history_end + 1,
                    "timestamp"
                ],

            "t_plus_2":
                production.loc[
                    history_end + 2,
                    "timestamp"
                ],
        })

    return (
        pd.DataFrame(
            feature_records
        ),
        np.asarray(
            target_errors,
            dtype=np.float64
        ),
        pd.DataFrame(
            metadata
        ),
    )


# ==========================================================
# 5. 时间顺序Train / Val
# ==========================================================

def _split_indices(
    n_samples: int
) -> Tuple[np.ndarray, np.ndarray]:

    n_train = int(
        n_samples
        *
        TRAIN_RATIO
    )

    n_val = int(
        n_samples
        *
        VAL_RATIO
    )

    train_indices = np.arange(
        0,
        n_train
    )

    val_indices = np.arange(
        n_train,
        n_train + n_val
    )

    return (
        train_indices,
        val_indices,
    )


# ==========================================================
# 6. 特征定义
# ==========================================================

def _feature_columns(
    variant: str,
    horizon: int
) -> List[str]:

    state_current = [
        "machine_load_t",
        "material_supply_status_t",
        "line_congestion_t",
        "changeover_progress_t",
        "order_pressure_t",
    ]

    future_plan = [
        f"order_pressure_t{horizon}",
        f"changeover_progress_t{horizon}",
        f"planned_output_t{horizon}",
        f"shift_factor_t{horizon}",
        f"hour_factor_t{horizon}",
        f"vehicle_A_t{horizon}",
        f"vehicle_B_t{horizon}",
        f"vehicle_C_t{horizon}",
    ]

    if variant == "state_only":

        return (
            state_current
            +
            future_plan
        )

    if variant == "state_conditioned":

        return (
            [
                "current_error"
            ]
            +
            state_current
            +
            future_plan
        )

    raise ValueError(
        f"未知variant: {variant}"
    )


# ==========================================================
# 7. Validation选择 Ridge alpha
# ==========================================================

def _fit_ridge_variant(
    X: pd.DataFrame,
    y_error: np.ndarray,
    train_indices: np.ndarray,
    val_indices: np.ndarray,
    variant: str,
    horizon: int
):

    columns = _feature_columns(
        variant,
        horizon
    )

    X_train = (
        X.loc[
            train_indices,
            columns
        ]
        .to_numpy(
            dtype=np.float64
        )
    )

    X_val = (
        X.loc[
            val_indices,
            columns
        ]
        .to_numpy(
            dtype=np.float64
        )
    )

    y_train = (
        y_error[
            train_indices,
            horizon - 1
        ]
    )

    y_val = (
        y_error[
            val_indices,
            horizon - 1
        ]
    )

    scaler = StandardScaler()

    X_train_scaled = scaler.fit_transform(
        X_train
    )

    X_val_scaled = scaler.transform(
        X_val
    )

    candidates = []

    for alpha in RIDGE_ALPHAS:

        if alpha == 0.0:
            model = LinearRegression()

        else:
            model = Ridge(
                alpha=alpha
            )

        model.fit(
            X_train_scaled,
            y_train
        )

        pred_val = model.predict(
            X_val_scaled
        )

        val_mae = float(
            np.mean(
                np.abs(
                    y_val
                    -
                    pred_val
                )
            )
        )

        candidates.append(
            (
                val_mae,
                alpha,
                model
            )
        )

    candidates.sort(
        key=lambda item: (
            item[0],
            item[1]
        )
    )

    (
        _,
        best_alpha,
        best_model

    ) = candidates[
        0
    ]

    return {
        "model":
            best_model,

        "scaler":
            scaler,

        "columns":
            columns,

        "alpha":
            float(
                best_alpha
            ),
    }


# ==========================================================
# 8. Baseline SH
# ==========================================================

def _fit_baseline_sh(
    X: pd.DataFrame,
    y_error: np.ndarray,
    train_indices: np.ndarray
) -> List[float]:

    x = (
        X.loc[
            train_indices,
            "current_error"
        ]
        .to_numpy(
            dtype=np.float64
        )
    )

    denominator = float(
        np.sum(
            x
            **
            2
        )
    )

    if denominator < 1e-12:
        raise ValueError(
            "Baseline SH无法拟合：Train current_error平方和过小。"
        )

    rho = []

    for horizon_index in range(
        FORECAST_HORIZON
    ):

        y = (
            y_error[
                train_indices,
                horizon_index
            ]
        )

        rho_h = float(
            np.sum(
                x
                *
                y
            )
            /
            denominator
        )

        rho.append(
            rho_h
        )

    return rho


# ==========================================================
# 9. 首次调用时构建全部模型
# ==========================================================

def _ensure_cache():

    if _CACHE:
        return

    production = _load_production()

    (
        X,
        y_error,
        metadata,

    ) = _build_samples(
        production
    )

    (
        train_indices,
        val_indices,

    ) = _split_indices(
        len(
            X
        )
    )

    baseline_rho = _fit_baseline_sh(
        X,
        y_error,
        train_indices
    )

    state_only = {}

    state_conditioned = {}

    for horizon in [
        1,
        2
    ]:

        state_only[
            horizon
        ] = _fit_ridge_variant(
            X,
            y_error,
            train_indices,
            val_indices,
            "state_only",
            horizon
        )

        state_conditioned[
            horizon
        ] = _fit_ridge_variant(
            X,
            y_error,
            train_indices,
            val_indices,
            "state_conditioned",
            horizon
        )

    offline_metrics = {}

    if METRICS_FILE.exists():

        try:

            metrics_df = pd.read_csv(
                METRICS_FILE
            )

            for model_name in [
                "Baseline SH",
                "State-Only",
                "State-Conditioned"
            ]:

                selected = metrics_df[
                    (
                        metrics_df[
                            "model"
                        ]
                        ==
                        model_name
                    )
                    &
                    (
                        metrics_df[
                            "scope"
                        ]
                        ==
                        "overall"
                    )
                    &
                    (
                        metrics_df[
                            "material"
                        ]
                        ==
                        "ALL"
                    )
                ]

                if not selected.empty:

                    offline_metrics[
                        model_name
                    ] = float(
                        selected.iloc[
                            0
                        ][
                            "WMAPE"
                        ]
                    )

        except Exception:
            # 指标文件只用于附带说明，不参与预测。
            pass

    _CACHE.update({
        "production":
            production,

        "X":
            X,

        "metadata":
            metadata,

        "baseline_rho":
            baseline_rho,

        "state_only":
            state_only,

        "state_conditioned":
            state_conditioned,

        "offline_metrics":
            offline_metrics,
    })


# ==========================================================
# 10. 单样本误差预测
# ==========================================================

def _predict_error(
    sample_id: int,
    variant: str
) -> Tuple[np.ndarray, Dict[str, Any]]:

    X = _CACHE[
        "X"
    ]

    if variant == "baseline_sh":

        current_error = float(
            X.loc[
                sample_id,
                "current_error"
            ]
        )

        rho = _CACHE[
            "baseline_rho"
        ]

        pred = np.asarray(
            [
                rho[0]
                *
                current_error,

                rho[1]
                *
                current_error,
            ],
            dtype=np.float64
        )

        details = {
            "rho_1":
                float(
                    rho[0]
                ),

            "rho_2":
                float(
                    rho[1]
                ),
        }

        return (
            pred,
            details
        )

    if variant not in [
        "state_only",
        "state_conditioned"
    ]:

        raise ValueError(
            f"未知模型variant: {variant}"
        )

    pred = np.zeros(
        FORECAST_HORIZON,
        dtype=np.float64
    )

    selected_alpha = {}

    for horizon in [
        1,
        2
    ]:

        artifact = (
            _CACHE[
                variant
            ][
                horizon
            ]
        )

        row = (
            X.loc[
                [
                    sample_id
                ],
                artifact[
                    "columns"
                ]
            ]
            .to_numpy(
                dtype=np.float64
            )
        )

        row_scaled = (
            artifact[
                "scaler"
            ]
            .transform(
                row
            )
        )

        pred[
            horizon - 1
        ] = float(
            artifact[
                "model"
            ]
            .predict(
                row_scaled
            )[
                0
            ]
        )

        selected_alpha[
            f"t+{horizon}"
        ] = float(
            artifact[
                "alpha"
            ]
        )

    return (
        pred,
        {
            "selected_alpha":
                selected_alpha
        }
    )


# ==========================================================
# 11. 对外预测接口
# ==========================================================

def run_state_conditioned_forecast(
    forecast_timestamp: str,
    variant: str = "state_conditioned"
) -> Dict[str, Any]:

    """
    Parameters
    ----------
    forecast_timestamp:
        预测基准时刻t，格式推荐 YYYY-MM-DD HH:MM:SS。

    variant:
        baseline_sh / state_only / state_conditioned
    """

    _ensure_cache()

    production = _CACHE[
        "production"
    ]

    X = _CACHE[
        "X"
    ]

    metadata = _CACHE[
        "metadata"
    ]

    timestamp = pd.to_datetime(
        forecast_timestamp
    )

    matches = metadata[
        metadata[
            "history_end"
        ]
        ==
        timestamp
    ]

    if matches.empty:

        valid_start = (
            metadata[
                "history_end"
            ]
            .min()
        )

        valid_end = (
            metadata[
                "history_end"
            ]
            .max()
        )

        return {
            "status":
                "error",

            "message":
                (
                    "forecast_timestamp不在Experiment 3可预测样本范围内。"
                    f" 可用history_end范围约为 {valid_start} 至 {valid_end}。"
                ),

            "forecast_timestamp":
                str(
                    timestamp
                ),
        }

    sample_id = int(
        matches.iloc[
            0
        ][
            "sample_id"
        ]
    )

    history_end_index = int(
        matches.iloc[
            0
        ][
            "history_end_index"
        ]
    )

    current_row = (
        production.loc[
            history_end_index
        ]
    )

    (
        error_pred,
        model_details,

    ) = _predict_error(
        sample_id,
        variant
    )

    variant_names = {
        "baseline_sh":
            "Baseline Multi-Horizon Structured Hybrid",

        "state_only":
            "State-Only Ridge",

        "state_conditioned":
            "State-Conditioned Structured Hybrid",
    }

    version_names = {
        "baseline_sh":
            "e3_baseline_sh",

        "state_only":
            "e3_state_only",

        "state_conditioned":
            "e3_state_conditioned_final",
    }

    forecast = {}

    peak_demand = {
        material:
            0.0
        for material in MATERIALS
    }

    for horizon in [
        1,
        2
    ]:

        future_index = (
            history_end_index
            +
            horizon
        )

        future_row = (
            production.loc[
                future_index
            ]
        )

        deterministic_output = float(
            future_row[
                "planned_output"
            ]
            *
            future_row[
                "shift_factor"
            ]
            *
            future_row[
                "hour_factor"
            ]
        )

        predicted_actual_output = float(
            max(
                0.0,
                deterministic_output
                +
                error_pred[
                    horizon - 1
                ]
            )
        )

        vehicle = str(
            future_row[
                "vehicle_type"
            ]
        )

        material_demand = {}

        for material in MATERIALS:

            demand = float(
                predicted_actual_output
                *
                BOM[
                    vehicle
                ][
                    material
                ]
            )

            material_demand[
                material
            ] = round(
                demand,
                4
            )

            peak_demand[
                material
            ] = max(
                peak_demand[
                    material
                ],
                demand
            )

        forecast[
            f"t+{horizon}"
        ] = {
            "horizon":
                f"t+{horizon}",

            "timestamp":
                str(
                    future_row[
                        "timestamp"
                    ]
                ),

            "vehicle_type":
                vehicle,

            "planned_output":
                float(
                    future_row[
                        "planned_output"
                    ]
                ),

            "shift_factor":
                float(
                    future_row[
                        "shift_factor"
                    ]
                ),

            "hour_factor":
                float(
                    future_row[
                        "hour_factor"
                    ]
                ),

            "order_pressure":
                float(
                    future_row[
                        "order_pressure"
                    ]
                ),

            "changeover_progress":
                float(
                    future_row[
                        "changeover_progress"
                    ]
                ),

            "deterministic_output":
                round(
                    deterministic_output,
                    4
                ),

            "predicted_production_error":
                round(
                    float(
                        error_pred[
                            horizon - 1
                        ]
                    ),
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

    offline_model_key = {
        "baseline_sh":
            "Baseline SH",

        "state_only":
            "State-Only",

        "state_conditioned":
            "State-Conditioned",
    }[
        variant
    ]

    offline_test_wmape = (
        _CACHE[
            "offline_metrics"
        ]
        .get(
            offline_model_key
        )
    )

    result = {
        "status":
            "success",

        "model": {
            "name":
                variant_names[
                    variant
                ],

            "version":
                version_names[
                    variant
                ],

            "experiment":
                "Experiment 3",

            "forecast_horizon_hours":
                2,

            "offline_test_overall_wmape_percent":
                (
                    round(
                        offline_test_wmape,
                        4
                    )
                    if offline_test_wmape is not None
                    else
                    None
                ),

            **model_details,
        },

        "forecast_timestamp":
            str(
                timestamp
            ),

        "current_state": {
            "timestamp":
                str(
                    current_row[
                        "timestamp"
                    ]
                ),

            "vehicle_type":
                str(
                    current_row[
                        "vehicle_type"
                    ]
                ),

            "planned_output":
                float(
                    current_row[
                        "planned_output"
                    ]
                ),

            "actual_output":
                float(
                    current_row[
                        "actual_output"
                    ]
                ),

            "observed_production_error":
                round(
                    float(
                        current_row[
                            "stochastic_error"
                        ]
                    ),
                    4
                ),

            "machine_load":
                float(
                    current_row[
                        "machine_load"
                    ]
                ),

            "material_supply_status":
                float(
                    current_row[
                        "material_supply_status"
                    ]
                ),

            "line_congestion":
                float(
                    current_row[
                        "line_congestion"
                    ]
                ),

            "changeover_progress":
                float(
                    current_row[
                        "changeover_progress"
                    ]
                ),

            "order_pressure":
                float(
                    current_row[
                        "order_pressure"
                    ]
                ),
        },

        "forecast":
            forecast,

        "two_hour_peak_demand": {
            material:
                round(
                    value,
                    4
                )
            for material, value
            in peak_demand.items()
        },
    }

    return result


# ==========================================================
# 12. 本地测试
# ==========================================================

if __name__ == "__main__":

    import json

    TEST_TIMESTAMP = (
        "2026-01-30 12:00:00"
    )

    for variant in [
        "baseline_sh",
        "state_only",
        "state_conditioned"
    ]:

        print(
            "\n"
            +
            "=" * 70
        )

        print(
            variant
        )

        print(
            "=" * 70
        )

        result = (
            run_state_conditioned_forecast(
                TEST_TIMESTAMP,
                variant=variant
            )
        )

        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2
            )
        )
