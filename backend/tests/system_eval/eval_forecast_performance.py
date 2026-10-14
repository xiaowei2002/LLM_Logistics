
"""
ForecastAgent V3: Experiment 3 real forecasting evaluation.

- Real baseline_sh / state_only / state_conditioned models
- Chronological holdout with target-overlap purge
- Independent actual-demand labels from production + BOM
- MAE / RMSE / WMAPE by model and horizon
- No LLM calls
- No modification of original forecast model

Run from backend:
    python -m tests.system_eval.eval_forecast_performance
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from core.tools.demand_forecast import (
    state_conditioned_forecast_tool as model,
)


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "cases" / "forecast_performance_cases.json"
RESULT_ROOT = ROOT / "results" / "forecast_performance_v3"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(
            lambda: stream.read(1024 * 1024), b""
        ):
            digest.update(chunk)
    return digest.hexdigest()


def check_finite(value, description: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(
            f"{description} 存在非有限数值：{value}"
        )
    return result


def calculate_metrics(records: list[dict]) -> dict:
    if not records:
        raise ValueError("没有可计算的预测记录。")

    actual = np.asarray(
        [row["actual_demand"] for row in records],
        dtype=np.float64,
    )
    predicted = np.asarray(
        [row["predicted_demand"] for row in records],
        dtype=np.float64,
    )

    if not (
        np.isfinite(actual).all()
        and np.isfinite(predicted).all()
    ):
        raise ValueError("预测值或真实值含 NaN/Inf。")

    error = predicted - actual
    absolute_error = np.abs(error)
    denominator = float(np.abs(actual).sum())

    return {
        "count": len(records),
        "MAE": round(float(absolute_error.mean()), 6),
        "RMSE": round(
            float(np.sqrt(np.mean(error ** 2))), 6
        ),
        "WMAPE_percent": (
            round(
                100.0
                * float(absolute_error.sum())
                / denominator,
                6,
            )
            if denominator > 0
            else None
        ),
        "actual_abs_sum": round(denominator, 6),
        "absolute_error_sum": round(
            float(absolute_error.sum()), 6
        ),
    }


def save_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"没有内容可写入：{path}")

    with path.open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=list(rows[0].keys()),
        )
        writer.writeheader()
        writer.writerows(rows)


def build_test_indices(metadata, n_samples):
    train_indices, val_indices = model._split_indices(
        n_samples
    )

    if len(train_indices) == 0 or len(val_indices) == 0:
        raise ValueError("训练集或验证集为空。")

    n_train = len(train_indices)
    n_val = len(val_indices)

    if train_indices[-1] + 1 != val_indices[0]:
        raise ValueError("Train/Val 索引不连续。")

    test_start = n_train + n_val

    candidates = np.arange(
        test_start, n_samples, dtype=int
    )

    if len(candidates) == 0:
        raise ValueError("没有剩余测试样本。")

    # 验证集用于选择 Ridge alpha。
    # 测试样本的未来目标时间必须严格晚于
    # 最后一个验证样本的未来目标时间。
    last_val_sample = int(val_indices[-1])
    last_val_target_index = (
        int(
            metadata.iloc[last_val_sample][
                "history_end_index"
            ]
        )
        + int(model.FORECAST_HORIZON)
    )

    accepted = []

    for sample_id in candidates:
        history_end_index = int(
            metadata.iloc[sample_id][
                "history_end_index"
            ]
        )

        first_target_index = history_end_index + 1

        if first_target_index > last_val_target_index:
            accepted.append(int(sample_id))

    if not accepted:
        raise ValueError(
            "剔除边界重叠后，没有合格测试样本。"
        )

    return {
        "n_samples": n_samples,
        "n_train": n_train,
        "n_validation": n_val,
        "n_test_candidates": len(candidates),
        "n_test_purged": len(candidates) - len(accepted),
        "last_validation_target_index": (
            last_val_target_index
        ),
        "test_indices": accepted,
    }


def evaluate():
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(CONFIG_PATH)

    config = json.loads(
        CONFIG_PATH.read_text(encoding="utf-8")
    )

    variants = config["variants"]
    materials = config["materials"]
    horizons = config["forecast_horizons"]
    protocol = config["test_protocol"]

    allowed_variants = {
        "baseline_sh",
        "state_only",
        "state_conditioned",
    }

    if set(variants) != allowed_variants:
        raise ValueError("模型配置与原工具不一致。")

    if horizons != [1, 2]:
        raise ValueError("必须评价 t+1 和 t+2。")

    if materials != list(model.MATERIALS):
        raise ValueError("物料集合与原模型不一致。")

    if not protocol["purge_overlapping_targets"]:
        raise ValueError("禁止关闭边界目标隔离。")

    if not protocol["require_unique_timestamps"]:
        raise ValueError("必须检查时间戳唯一性。")

    if not protocol["require_finite_values"]:
        raise ValueError("必须检查数值有限性。")

    raw_file = Path(model.PRODUCTION_FILE)

    print("=== Forecast Performance V3 ===")
    print("真实 LLM：否")
    print("真实预测模型：是")
    print("数据来源：Experiment 3")
    print("模型数量：3")

    if not raw_file.is_file():
        raise FileNotFoundError(
            "缺少原始生产数据文件："
            f"{raw_file}\n"
            "评估已停止；请先确认原模型数据路径。"
            "不要创建虚构数据代替。"
        )

    raw_hash_before = file_sha256(raw_file)

    started_build = time.perf_counter()
    model._ensure_cache()
    model_build_seconds = (
        time.perf_counter() - started_build
    )

    cache = model._CACHE
    production = cache["production"]
    metadata = cache["metadata"]
    X = cache["X"]

    if len(X) != len(metadata):
        raise ValueError("特征与元数据样本数不一致。")

    if (
        production["timestamp"].isna().any()
        or production["timestamp"].duplicated().any()
    ):
        raise ValueError(
            "生产数据时间戳存在缺失或重复。"
        )

    if not production["timestamp"].is_monotonic_increasing:
        raise ValueError("生产数据时间顺序异常。")

    if (
        metadata["history_end"].isna().any()
        or metadata["history_end"].duplicated().any()
    ):
        raise ValueError("样本时间戳存在缺失或重复。")

    split = build_test_indices(
        metadata, len(X)
    )

    test_indices = split.pop("test_indices")

    max_samples = int(
        protocol.get("max_samples", 0)
    )

    if max_samples < 0:
        raise ValueError("max_samples 不能为负数。")

    if max_samples:
        test_indices = test_indices[:max_samples]

    if not test_indices:
        raise ValueError("实际选中的测试集为空。")

    print(f"样本总数：{split['n_samples']}")
    print(f"训练样本：{split['n_train']}")
    print(f"验证样本：{split['n_validation']}")
    print(
        "测试候选样本："
        f"{split['n_test_candidates']}"
    )
    print(
        "边界剔除样本："
        f"{split['n_test_purged']}"
    )
    print(f"实际测试样本：{len(test_indices)}")
    print(
        f"模型初始化耗时："
        f"{model_build_seconds:.4f}s"
    )

    prediction_rows = []
    timing_rows = []

    for position, sample_id in enumerate(
        test_indices, start=1
    ):
        meta = metadata.iloc[sample_id]
        history_index = int(
            meta["history_end_index"]
        )

        timestamp = pd.Timestamp(
            meta["history_end"]
        ).strftime("%Y-%m-%d %H:%M:%S")

        for variant in variants:
            started = time.perf_counter()

            result = model.run_state_conditioned_forecast(
                forecast_timestamp=timestamp,
                variant=variant,
            )

            elapsed = time.perf_counter() - started

            if result.get("status") != "success":
                raise RuntimeError(
                    f"真实模型执行失败："
                    f"{sample_id}/{variant}："
                    f"{result.get('message')}"
                )

            if pd.Timestamp(
                result["forecast_timestamp"]
            ) != pd.Timestamp(timestamp):
                raise ValueError(
                    "预测返回的基准时刻不匹配。"
                )

            timing_rows.append({
                "sample_id": sample_id,
                "forecast_timestamp": timestamp,
                "variant": variant,
                "elapsed_seconds": round(
                    elapsed, 6
                ),
            })

            for horizon in horizons:
                future_index = (
                    history_index + horizon
                )
                actual_row = production.iloc[
                    future_index
                ]

                forecast_block = result["forecast"][
                    f"t+{horizon}"
                ]

                actual_time = pd.Timestamp(
                    actual_row["timestamp"]
                )

                returned_time = pd.Timestamp(
                    forecast_block["timestamp"]
                )

                metadata_time = pd.Timestamp(
                    meta[f"t_plus_{horizon}"]
                )

                if not (
                    actual_time
                    == returned_time
                    == metadata_time
                ):
                    raise ValueError(
                        f"时间对齐失败："
                        f"{sample_id}/t+{horizon}"
                    )

                actual_production = check_finite(
                    actual_row["actual_output"],
                    "实际产量",
                )

                vehicle = str(
                    actual_row["vehicle_type"]
                )

                if vehicle not in model.BOM:
                    raise ValueError(
                        f"未知车型：{vehicle}"
                    )

                if str(
                    forecast_block["vehicle_type"]
                ) != vehicle:
                    raise ValueError(
                        "预测车型与实际车型不一致。"
                    )

                forecast_materials = (
                    forecast_block["material_demand"]
                )

                if set(forecast_materials) != set(materials):
                    raise ValueError(
                        "预测物料集合不一致。"
                    )

                for material in materials:
                    actual_demand = (
                        actual_production
                        * float(
                            model.BOM[vehicle][material]
                        )
                    )

                    predicted = check_finite(
                        forecast_materials[material],
                        "预测物料需求",
                    )

                    actual_demand = check_finite(
                        actual_demand,
                        "真实物料需求",
                    )

                    prediction_rows.append({
                        "sample_id": sample_id,
                        "forecast_timestamp": timestamp,
                        "target_timestamp": str(
                            actual_time
                        ),
                        "variant": variant,
                        "horizon": horizon,
                        "vehicle_type": vehicle,
                        "material": material,
                        "predicted_demand": predicted,
                        "actual_demand": actual_demand,
                        "absolute_error": abs(
                            predicted - actual_demand
                        ),
                        "squared_error": (
                            predicted - actual_demand
                        ) ** 2,
                    })

        if (
            position == 1
            or position % 50 == 0
            or position == len(test_indices)
        ):
            print(
                f"已完成测试时刻："
                f"{position}/{len(test_indices)}"
            )

    expected_rows = (
        len(test_indices)
        * len(variants)
        * len(horizons)
        * len(materials)
    )

    if len(prediction_rows) != expected_rows:
        raise ValueError(
            "预测记录数量不完整："
            f"{len(prediction_rows)} != {expected_rows}"
        )

    if file_sha256(raw_file) != raw_hash_before:
        raise RuntimeError(
            "执行前后原始数据哈希发生变化，"
            "评估结果无效。"
        )

    metrics_rows = []

    def append_metric(scope, variant, horizon, material, rows):
        values = calculate_metrics(rows)
        metrics_rows.append({
            "scope": scope,
            "variant": variant,
            "horizon": horizon,
            "material": material,
            **values,
        })

    for variant in variants:
        selected = [
            row for row in prediction_rows
            if row["variant"] == variant
        ]

        append_metric(
            "overall", variant, "ALL", "ALL",
            selected,
        )

        for horizon in horizons:
            h_rows = [
                row for row in selected
                if row["horizon"] == horizon
            ]

            append_metric(
                "horizon", variant, horizon, "ALL",
                h_rows,
            )

            for material in materials:
                m_rows = [
                    row for row in h_rows
                    if row["material"] == material
                ]

                append_metric(
                    "horizon_material",
                    variant,
                    horizon,
                    material,
                    m_rows,
                )

    overall = [
        row for row in metrics_rows
        if row["scope"] == "overall"
    ]

    timing_summary = {}

    for variant in variants:
        values = [
            row["elapsed_seconds"]
            for row in timing_rows
            if row["variant"] == variant
        ]

        timing_summary[variant] = {
            "calls": len(values),
            "mean_seconds": round(
                float(np.mean(values)), 6
            ),
            "median_seconds": round(
                float(np.median(values)), 6
            ),
            "max_seconds": round(
                float(np.max(values)), 6
            ),
        }

    output = RESULT_ROOT

    # 防止无意中覆盖已有的正式评估结果。
    if output.exists():
        raise FileExistsError(
            "V3 结果目录已存在，拒绝覆盖："
            f"{output}"
        )

    output.mkdir(parents=True)

    save_csv(
        output / "forecast_predictions.csv",
        prediction_rows,
    )
    save_csv(
        output / "forecast_metrics.csv",
        metrics_rows,
    )
    save_csv(
        output / "forecast_timing.csv",
        timing_rows,
    )

    summary = {
        "evaluation_id": config["evaluation_id"],
        "evaluated_at": datetime.now().isoformat(),
        "evaluation_type": config["evaluation_type"],
        "source_sha256": raw_hash_before,
        "source_file": str(raw_file),
        "split": split,
        "evaluated_test_samples": len(test_indices),
        "forecast_horizons": horizons,
        "materials": materials,
        "model_build_seconds": round(
            model_build_seconds, 6
        ),
        "model_timing": timing_summary,
        "overall_metrics": overall,
        "real_llm_used": False,
        "real_forecast_model_used": True,
        "future_covariates_audited": False,
        "prediction_label_overlap_purged": True,
        "note": (
            "按原 Experiment 3 Train/Val 协议"
            "重建模型，使用后段隔离测试样本；"
            "真实标签来自实际产量与 BOM。"
            "尚未独立证明未来协变量的预测时可得性。"
        ),
    }

    (
        output / "forecast_performance_summary.json"
    ).write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    print("\n=== V3 Overall Metrics ===")
    for row in overall:
        print(
            f"{row['variant']}: "
            f"MAE={row['MAE']}, "
            f"RMSE={row['RMSE']}, "
            f"WMAPE={row['WMAPE_percent']}%"
        )

    print("\n模型构建耗时：")
    print(f"{model_build_seconds:.4f}s")

    print("\n结果保存目录：")
    print(output)


if __name__ == "__main__":
    evaluate()