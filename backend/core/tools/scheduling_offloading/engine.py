
from __future__ import annotations

import os
import threading
from collections import Counter
from pathlib import Path
from typing import Any

from .runtime import (
    load_checkpoint,
    replay_decisions,
    run_episode,
)

DEFAULT_MODEL_PATH = (
    Path(__file__).resolve().parent
    / "models"
    / "joint_mappo_route_offload_jobs20.pt"
)

_ENGINE_LOCK = threading.Lock()
_MAPPO_CACHE: dict[str, Any] = {}


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "item"):
        return value.item()
    return value


def _decision_summary(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind = Counter(item["kind"] for item in decisions)
    by_action = Counter((item["kind"], item["action"]) for item in decisions)
    return {
        "total": len(decisions),
        "by_kind": dict(by_kind),
        "by_action": [
            {"kind": kind, "action": action, "count": count}
            for (kind, action), count in sorted(by_action.items())
        ],
    }


def _run_joint_mappo(seed: int, jobs: int, resources=None) -> tuple[dict[str, Any], list[int], dict[str, Any]]:
    """运行项目内置的确定性“路由选择+计算卸载”MAPPO模型。"""
    checkpoint_path = Path(
        os.getenv("SCHEDULING_MAPPO_CHECKPOINT", str(DEFAULT_MODEL_PATH))
    ).expanduser().resolve()
    if not checkpoint_path.is_file():
        raise FileNotFoundError(
            f"未找到联合 MAPPO 模型：{checkpoint_path}；"
            "可通过 SCHEDULING_MAPPO_CHECKPOINT 配置。"
        )

    cache_key = str(checkpoint_path)
    cached = _MAPPO_CACHE.get(cache_key)
    if cached is None:
        cached = load_checkpoint(checkpoint_path)
        _MAPPO_CACHE[cache_key] = cached

    checkpoint = cached.checkpoint
    args = checkpoint.get("args", {})
    model_jobs = int(checkpoint["jobs"])
    rows, metrics, episode_return = run_episode(
        bundle=cached,
        seed=seed,
        jobs=jobs,
        scenario_overrides={
            "machine_flexibility": "partial",
            "eligible_machines_per_operation": 3,
            **(resources or {}),
        },
    )
    trace = [int(row["action"]) for row in rows]
    diagnostics = {
        "policy": "MAPPO_ROUTING_OFFLOADING+SPT",
        "checkpoint": checkpoint_path.name,
        "checkpoint_training_jobs": model_jobs,
        "scenario_jobs": jobs,
        "state_encoding": "variable_entity_sets",
        "cross_scale_inference": jobs != model_jobs or (resources or {}).get("machine_count", 6) != 6 or (resources or {}).get("edge_count", 2) != 2,
        "machine_topology": "operation_candidates",
        "topology_shift": True,
        "scenario_machines": (resources or {}).get("machine_count", 6),
        "scenario_edges": (resources or {}).get("edge_count", 2),
        "global_state_encoding": "legacy_resource_summary_6_machines_2_edges",
        "checkpoint_training_seed": args.get("seed"),
        "best_episode": checkpoint.get("best_episode"),
        "best_validation_return": checkpoint.get("best_return"),
        "learned_roles": list(checkpoint.get("learned_roles") or ()),
        "decision_count": len(trace),
        "episode_return": float(episode_return),
        "inference_device": "cpu",
    }
    return metrics, trace, diagnostics


def _mappo_result(payload: dict[str, Any]) -> dict[str, Any]:
    seed = int(payload["seed"])
    jobs = int(payload["number_of_jobs"])
    resources = {"machine_count": payload.get("machine_count", 6), "edge_count": payload.get("edge_count", 2)}
    for name, value in resources.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name}必须为正整数。")
    metrics, trace, diagnostics = _run_joint_mappo(seed, jobs, resources)
    decisions = replay_decisions(
        seed,
        jobs,
        trace,
        scenario_overrides={
            "machine_flexibility": "partial",
            "eligible_machines_per_operation": 3,
            **(resources or {}),
        },
    )
    return {
        "metrics": _jsonable(metrics),
        "decisions": _jsonable(decisions),
        "decision_summary": _decision_summary(decisions),
        "diagnostics": _jsonable(diagnostics),
        "warnings": [
            f"本次使用{jobs}个工件、{resources['machine_count']}台机器、{resources['edge_count']}个边缘节点；"
            "工序直接指定候选机器；复用原策略，尚未针对新加工拓扑和多资源规模重新训练。"
        ] if diagnostics["cross_scale_inference"] or diagnostics["topology_shift"] else [],
    }


def run_scheduling_offloading(payload: dict[str, Any]) -> dict[str, Any]:
    """执行一次MAPPO调度与计算卸载请求。"""
    with _ENGINE_LOCK:
        return _mappo_result(payload)
