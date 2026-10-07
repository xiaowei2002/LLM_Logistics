"""Pluggable execution engines for joint scheduling and computation offloading."""

from __future__ import annotations

import importlib
import os
import random
import sys
import threading
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_RESEARCH_ROOT = Path(
    r"E:\fuxian\websearch\Deep-reinforcement-learning-for-dynamic-scheduling-of-a-flexible-job-shop"
)
ENGINE_OPTIONS = {
    "auto": "自动选择可用算法引擎",
    "research": "调用联合调度仿真算法",
    "demo": "使用内置演示数据检查软件流程",
}
ALGORITHM_OPTIONS = {
    "midterm": "路由与计算卸载联合 MAPPO（固定 SPT）",
    "rolling_ga": "滚动时域遗传算法",
    "rolling_nsga2": "滚动时域 NSGA-II",
}

DEFAULT_MAPPO_CHECKPOINT = DEFAULT_RESEARCH_ROOT / (
    "results/event_mappo/"
    "event_candidate_mappo_cross_v4_route_offload_jobs20_ep600_s2031.pt"
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


def _demo_result(payload: dict[str, Any], warning: str | None = None) -> dict[str, Any]:
    seed = int(payload["seed"])
    jobs = int(payload["number_of_jobs"])
    rng = random.Random(seed)
    decisions: list[dict[str, Any]] = []
    clock = 0.0
    nodes = ("LOCAL", "EDGE1", "EDGE2", "CLOUD")
    compute_choices: Counter[str] = Counter()
    for job in range(1, jobs + 1):
        operations = 3 + rng.randrange(4)
        for operation in range(1, operations + 1):
            machine = rng.randrange(6)
            decisions.append({
                "time": round(clock, 2), "kind": "ROUTING",
                "action": f"J{job}-O{operation} → M{machine}",
            })
            decisions.append({
                "time": round(clock, 2), "kind": "SEQUENCING",
                "action": "SPT",
            })
            node = nodes[(job + operation + seed) % len(nodes)]
            compute_choices[node] += 1
            decisions.append({
                "time": round(clock + 4.0, 2), "kind": "OFFLOADING",
                "action": node,
            })
            clock += rng.uniform(2.8, 6.5)

    makespan = round(clock / 2.2, 2)
    metrics = {
        "makespan": makespan,
        "total_tardiness": round(makespan * rng.uniform(0.28, 0.48), 2),
        "machine_utilization": round(rng.uniform(0.68, 0.86), 4),
        "mean_blocking": round(rng.uniform(2.0, 7.0), 2),
        "inspection_timeout_rate": round(rng.uniform(0.04, 0.15), 4),
        "deadline_satisfaction_rate": 0.0,
        "mean_timeout_lateness": round(rng.uniform(1.0, 4.5), 2),
        "max_timeout_lateness": round(rng.uniform(5.0, 13.0), 2),
        "compute_choices": dict(compute_choices),
    }
    metrics["deadline_satisfaction_rate"] = round(
        1.0 - metrics["inspection_timeout_rate"], 4
    )
    warnings = [
        "当前显示的是演示数据，仅用于开发调试，不代表实际算法结果。"
    ]
    if warning:
        warnings.append(f"研究实验未能启动：{warning}")
    return {
        "engine": "demo",
        "algorithm": payload["algorithm"],
        "is_demo": True,
        "metrics": metrics,
        "decisions": decisions,
        "decision_summary": _decision_summary(decisions),
        "diagnostics": {"seed": seed, "jobs": jobs},
        "warnings": warnings,
    }


def _load_research_module():
    root = Path(
        os.getenv("SCHEDULING_ALGORITHM_DIR", str(DEFAULT_RESEARCH_ROOT))
    ).expanduser().resolve()
    entry = root / "evaluate_rolling_horizon_ea.py"
    if not entry.is_file():
        raise FileNotFoundError(
            f"未找到调度算法入口：{entry}；可通过 SCHEDULING_ALGORITHM_DIR 配置。"
        )
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    return importlib.import_module("evaluate_rolling_horizon_ea")


def _replay_decisions(module, seed: int, jobs: int, trace: list[int]):
    env = module.make_fixed_env(seed, jobs)
    view = env.reset(seed=seed)
    for action in trace:
        if view["terminated"]:
            break
        view, _, _, _ = env.step(int(action))
    return [dict(item) for item in env.shop.decision_history]


def _run_joint_mappo(seed: int, jobs: int) -> tuple[dict[str, Any], list[int], dict[str, Any]]:
    """Run the report's deterministic routing+offloading MAPPO checkpoint."""
    import torch

    checkpoint_path = Path(
        os.getenv("SCHEDULING_MAPPO_CHECKPOINT", str(DEFAULT_MAPPO_CHECKPOINT))
    ).expanduser().resolve()
    if not checkpoint_path.is_file():
        raise FileNotFoundError(
            f"未找到联合 MAPPO 模型：{checkpoint_path}；"
            "可通过 SCHEDULING_MAPPO_CHECKPOINT 配置。"
        )

    cache_key = str(checkpoint_path)
    cached = _MAPPO_CACHE.get(cache_key)
    if cached is None:
        module = importlib.import_module("train_event_candidate_mappo_dual")
        checkpoint = torch.load(
            checkpoint_path, map_location="cpu", weights_only=False
        )
        model = module.DualCandidateMAPPO(
            checkpoint["base_dim"],
            checkpoint["entity_counts"],
            separate_actor_heads=checkpoint.get("separate_actor_heads", False),
            independent_actors=checkpoint.get("independent_actors", False),
            sequencing_action_q=checkpoint.get("sequencing_action_q", False),
            relational_routing_actor=checkpoint.get(
                "relational_routing_actor", False
            ),
        )
        model.load_state_dict(checkpoint["network"])
        model.eval()
        cached = (module, checkpoint, model)
        _MAPPO_CACHE[cache_key] = cached

    module, checkpoint, model = cached
    model_jobs = int(checkpoint["jobs"])
    if jobs != model_jobs:
        raise ValueError(
            f"当前联合 MAPPO 模型固定支持 {model_jobs} 个工件，"
            f"收到 {jobs} 个；请使用 20 工件场景。"
        )

    args = checkpoint.get("args", {})
    rows, metrics, episode_return = module.collect_dual_episode(
        model=model,
        device=torch.device("cpu"),
        seed=seed,
        jobs=jobs,
        scales=checkpoint["common_scales"],
        max_actions=int(checkpoint["max_actions"]),
        entity_state=checkpoint.get("entity_counts") is not None,
        deterministic=True,
        learned_roles=checkpoint.get("learned_roles"),
        gate_sequencing=bool(args.get("gate_sequencing", False)),
        gate_margin=float(args.get("gate_margin", 1.0)),
        reward_variant=str(args.get("reward_variant", "cross_v4")),
        routing_downstream_features=bool(
            args.get("routing_downstream_features", False)
        ),
        routing_relations=bool(args.get("routing_relations", False)),
    )
    trace = [int(row["action"]) for row in rows]
    diagnostics = {
        "policy": "MAPPO_ROUTING_OFFLOADING+SPT",
        "checkpoint": checkpoint_path.name,
        "checkpoint_training_seed": args.get("seed"),
        "best_episode": checkpoint.get("best_episode"),
        "best_validation_return": checkpoint.get("best_return"),
        "learned_roles": list(checkpoint.get("learned_roles") or ()),
        "decision_count": len(trace),
        "episode_return": float(episode_return),
        "inference_device": "cpu",
    }
    return metrics, trace, diagnostics


def _research_result(payload: dict[str, Any]) -> dict[str, Any]:
    seed = int(payload["seed"])
    jobs = int(payload["number_of_jobs"])
    algorithm = payload["algorithm"]
    module = _load_research_module()
    module.set_scenario_overrides({
        "machine_flexibility": "partial",
        "eligible_machines_per_operation": 3,
    })
    try:
        if algorithm == "midterm":
            metrics, trace, diagnostics = _run_joint_mappo(seed, jobs)
        else:
            config = module.PlannerConfig(
                algorithm="ga" if algorithm == "rolling_ga" else "nsga2",
                population_size=int(payload["population_size"]),
                generations=int(payload["generations"]),
                decision_horizon=int(payload["decision_horizon"]),
                commit_actions=1,
                evaluation="window",
                time_horizon=float(payload["time_horizon"]),
                random_seed=seed + 1000,
            )
            metrics, trace, diagnostics = module.run_evolutionary(seed, jobs, config)
        decisions = _replay_decisions(module, seed, jobs, trace)
    finally:
        module.set_scenario_overrides({})
    return {
        "engine": "research",
        "algorithm": algorithm,
        "is_demo": False,
        "metrics": _jsonable(metrics),
        "decisions": _jsonable(decisions),
        "decision_summary": _decision_summary(decisions),
        "diagnostics": _jsonable(diagnostics),
        "warnings": [],
    }


def run_scheduling_offloading(payload: dict[str, Any]) -> dict[str, Any]:
    """Run one normalized scheduling/offloading request."""
    engine = payload["engine"]
    if engine == "demo":
        return _demo_result(payload)
    with _ENGINE_LOCK:
        try:
            return _research_result(payload)
        except Exception as exc:
            if engine == "research":
                raise
            return _demo_result(payload, warning=str(exc))
