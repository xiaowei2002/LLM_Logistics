from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn
from torch.distributions import Categorical

from .demo_coupled_env import scalable_edge_compute_config
from .joint_decision_env import JointDecisionEnv


CANDIDATE_DIM = 17
ROLES = {"ROUTING": 0, "SEQUENCING": 1, "OFFLOADING": 2}


class StateSetEncoder(nn.Module):
    def __init__(self, base_dim: int, entity_counts=None, hidden=128, entity_hidden=64):
        super().__init__()
        self.base_dim = base_dim
        self.entity_counts = entity_counts
        self.base = nn.Sequential(
            nn.Linear(base_dim, hidden), nn.ReLU(),
            nn.Linear(hidden, hidden), nn.ReLU(),
        )
        if entity_counts is not None:
            self.entity_encoders = nn.ModuleList([
                nn.Sequential(
                    nn.Linear(12, entity_hidden), nn.ReLU(),
                    nn.Linear(entity_hidden, entity_hidden), nn.ReLU(),
                )
                for _ in range(3)
            ])
            self.project = nn.Sequential(
                nn.Linear(hidden + 6 * entity_hidden, hidden), nn.ReLU(),
                nn.Linear(hidden, hidden), nn.ReLU(),
            )

    @staticmethod
    def _pool(encoded, mask):
        valid = mask.unsqueeze(-1).float()
        mean = (encoded * valid).sum(1) / valid.sum(1).clamp_min(1.0)
        maximum = encoded.masked_fill(~mask.unsqueeze(-1), -1e9).max(1).values
        maximum = torch.where(torch.isfinite(maximum), maximum, torch.zeros_like(maximum))
        return mean, maximum

    def forward(self, state):
        if isinstance(state, dict):
            # 实体数量由各张表决定，不再按照检查点的20个位置切分。
            base_hidden = self.base(state["global_state"])
            if self.entity_counts is None:
                return base_hidden
            pooled = []
            for name, encoder in zip(("job", "machine", "compute"), self.entity_encoders):
                entities = state[f"{name}_entities"]
                mask = state[f"{name}_entity_mask"] > 0.5
                pooled.extend(self._pool(encoder(entities), mask))
            return self.project(torch.cat((base_hidden, *pooled), dim=-1))

        # 保留旧向量输入，便于与原20工件推理逐步核对。
        base_hidden = self.base(state[:, :self.base_dim])
        if self.entity_counts is None:
            return base_hidden
        cursor = self.base_dim
        pooled = []
        for count, encoder in zip(self.entity_counts, self.entity_encoders):
            size = count * 12
            entities = state[:, cursor:cursor + size].reshape(-1, count, 12)
            cursor += size
            mask = state[:, cursor:cursor + count] > 0.5
            cursor += count
            pooled.extend(self._pool(encoder(entities), mask))
        return self.project(torch.cat((base_hidden, *pooled), dim=-1))


class CandidatePolicy(nn.Module):
    def __init__(self, base_dim, entity_counts=None, hidden=128, role_dim=12):
        super().__init__()
        self.state = StateSetEncoder(base_dim, entity_counts, hidden)
        self.candidate = nn.Sequential(
            nn.Linear(CANDIDATE_DIM, hidden), nn.ReLU(),
            nn.Linear(hidden, hidden), nn.ReLU(),
        )
        self.role = nn.Embedding(3, role_dim)
        context_dim = hidden * 3 + role_dim
        self.score = nn.Sequential(
            nn.Linear(context_dim + hidden, hidden), nn.ReLU(),
            nn.Linear(hidden, 1),
        )

    def forward(self, state, role, candidates, mask):
        state_hidden = self.state(state)
        candidate_hidden = self.candidate(candidates)
        valid = mask.unsqueeze(-1).float()
        mean = (candidate_hidden * valid).sum(1) / valid.sum(1).clamp_min(1.0)
        maximum = candidate_hidden.masked_fill(~mask.unsqueeze(-1), -1e9).max(1).values
        maximum = torch.where(torch.isfinite(maximum), maximum, torch.zeros_like(maximum))
        context = torch.cat((state_hidden, mean, maximum, self.role(role)), -1)
        expanded = context.unsqueeze(1).expand(-1, candidates.shape[1], -1)
        logits = self.score(torch.cat((expanded, candidate_hidden), -1)).squeeze(-1)
        return logits.masked_fill(~mask, -1e9)


class DualCentralizedValue(nn.Module):
    def __init__(self, base_dim, entity_counts=None, hidden=128, role_dim=12):
        super().__init__()
        self.state = StateSetEncoder(base_dim, entity_counts, hidden)
        self.candidate = nn.Sequential(
            nn.Linear(CANDIDATE_DIM, hidden), nn.ReLU(),
            nn.Linear(hidden, hidden), nn.ReLU(),
        )
        self.role = nn.Embedding(3, role_dim)
        self.value = nn.Sequential(
            nn.Linear(hidden * 3 + role_dim, hidden), nn.ReLU(),
            nn.Linear(hidden, 2),
        )

    def forward(self, state, role, candidates, mask):
        candidate_hidden = self.candidate(candidates)
        valid = mask.unsqueeze(-1).float()
        mean = (candidate_hidden * valid).sum(1) / valid.sum(1).clamp_min(1.0)
        maximum = candidate_hidden.masked_fill(~mask.unsqueeze(-1), -1e9).max(1).values
        maximum = torch.where(torch.isfinite(maximum), maximum, torch.zeros_like(maximum))
        context = torch.cat((self.state(state), mean, maximum, self.role(role)), -1)
        return self.value(context)


class ValueNormalizer(nn.Module):
    def __init__(self, epsilon=1e-5):
        super().__init__()
        self.epsilon = epsilon
        self.register_buffer("mean", torch.zeros(()))
        self.register_buffer("variance", torch.ones(()))
        self.register_buffer("count", torch.as_tensor(1e-4))

    def denormalize(self, values):
        return values * torch.sqrt(self.variance + self.epsilon) + self.mean


class DualCandidateMAPPO(nn.Module):
    """与项目内置检查点结构兼容的推理网络。"""

    def __init__(self, base_dim, entity_counts=None, hidden=128):
        super().__init__()
        self.actor = CandidatePolicy(base_dim, entity_counts, hidden)
        self.critic = DualCentralizedValue(base_dim, entity_counts, hidden)
        self.production_normalizer = ValueNormalizer()
        self.computation_normalizer = ValueNormalizer()

    def distribution(self, state, role, candidates, mask):
        return Categorical(logits=self.actor(state, role, candidates, mask))


@dataclass(frozen=True)
class RuntimeBundle:
    checkpoint: dict[str, Any]
    model: DualCandidateMAPPO
    path: Path


def load_checkpoint(path: str | Path, device: str | torch.device = "cpu") -> RuntimeBundle:
    checkpoint_path = Path(path).expanduser().resolve()
    checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=False)
    unsupported = {
        name: checkpoint.get(name)
        for name in (
            "separate_actor_heads", "independent_actors",
            "sequencing_action_q", "relational_routing_actor",
        )
        if checkpoint.get(name)
    }
    if unsupported:
        raise ValueError(f"当前内置推理运行时不支持该检查点结构：{unsupported}")
    model = DualCandidateMAPPO(checkpoint["base_dim"], checkpoint["entity_counts"])
    model.load_state_dict(checkpoint["network"], strict=True)
    model.to(device)
    model.eval()
    return RuntimeBundle(checkpoint=checkpoint, model=model, path=checkpoint_path)


def _make_env(seed: int, jobs: int, scenario_overrides: dict[str, Any] | None = None):
    scenario = {
        "machine_count": 6,
        "number_of_jobs": jobs,
        "initial_jobs": min(6, jobs),
    }
    scenario.update(scenario_overrides or {})
    edge_count = scenario.pop("edge_count", 2)
    machine_count = scenario["machine_count"]
    env = JointDecisionEnv(
        seed=seed,
        stochastic=True,
        reward_mode="role_weighted",
        risk_slack_features=True,
        scenario_profile="stress",
        compute_config=scalable_edge_compute_config(edge_count, machine_count, mode="scale_out"),
        resource_state_slots=(6, 2),
        scenario_config=scenario,
        scalable_scheduling_features=True,
        scheduling_potential_weight=0.0,
        direct_scheduling=True,
        tardiness_reward_weight=1.0,
        makespan_reward_weight=3.0,
        coupling_aware_scheduling_features=True,
        critic_decision_context=True,
        tail_lateness_reward_weight=1.0,
    )
    env.stochastic = False
    env.reward_mode = "shared"
    return env


def _risk_greedy_action(env):
    request = env.shop.pending_decision
    operation = request["job"].operations[request["operation_index"]]
    predictions = []
    for index, node in enumerate(env.shop.compute_candidates(request["source_machine"])):
        finish, *_ = env.shop.predict_risk_compute(
            node,
            request["source_machine"],
            operation.inspection_data,
            operation.inspection_workload,
        )
        predictions.append((finish, index))
    return min(predictions)[1]


def _teacher_action(env, view):
    request = env.shop.pending_decision
    if view["kind"] == "ROUTING":
        job = request["job"]
        index = request["operation_index"]
        _, _, machine, _ = env.shop.select_machine(job, index, "EA")
        return env.shop.operation_machines(job.operations[index]).index(machine)
    if view["kind"] == "SEQUENCING":
        selected = min(
            request["candidates"],
            key=lambda item: (item["processing_time"], item["arrival_order"]),
        )
        return request["candidates"].index(selected)
    return _risk_greedy_action(env)


def _offloading_candidates(view):
    observation = np.asarray(view["offloading_obs"], dtype=np.float32)
    count = len(view["offloading_mask"])
    common = observation[:6]
    nodes = observation[6:6 + 4 * count].reshape(count, 4)
    tail_start = 6 + 4 * count
    tail = observation[tail_start:tail_start + 6]
    slacks = observation[tail_start + 6:tail_start + 6 + count]
    return np.asarray([
        np.concatenate((common, nodes[index], tail, [slacks[index]]))
        for index in range(count)
    ], dtype=np.float32)


def _structured_state(view):
    """分开保留整体、工件、机器和计算节点信息以及有效性标记。"""
    names = (
        "global_state", "job_entities", "job_entity_mask",
        "machine_entities", "machine_entity_mask",
        "compute_entities", "compute_entity_mask",
    )
    return {name: np.asarray(view[name], dtype=np.float32) for name in names}


def _tensor_state(state, device):
    return {
        name: torch.as_tensor(values[None], dtype=torch.float32, device=device)
        for name, values in state.items()
    }


def _encode_view(view, max_actions, routing_downstream_features=False):
    state = _structured_state(view)
    kind = view["kind"]
    if kind == "ROUTING":
        raw, raw_mask = view["routing_candidates"], view["routing_mask"]
    elif kind == "SEQUENCING":
        raw, raw_mask = view["sequencing_candidates"], view["sequencing_mask"]
    else:
        raw, raw_mask = _offloading_candidates(view), view["offloading_mask"]
    raw = np.asarray(raw, dtype=np.float32)
    if kind == "ROUTING" and not routing_downstream_features:
        raw = raw.copy()
        raw[:, 12:] = 0.0
    raw_mask = np.asarray(raw_mask, dtype=np.int8)
    # max_actions仅保留旧填充长度，不再作为截断候选的上限。
    capacity = max(max_actions, len(raw))
    candidates = np.zeros((capacity, CANDIDATE_DIM), dtype=np.float32)
    mask = np.zeros(capacity, dtype=np.int8)
    count = len(raw)
    candidates[:count, :raw.shape[-1]] = raw[:count]
    mask[:count] = raw_mask[:count]
    return state, ROLES[kind], candidates, mask


def _dual_reward(info, scales, reward_variant):
    component = info["reward_components"]
    production = float(
        component["new_tardiness"] * 200.0 / scales["tardiness"]
        + component["terminal_makespan"] * 10.0 / scales["makespan"]
    )
    computation = float(
        component["new_timeout"] / scales["timeouts"]
        + component["new_lateness"] * 10.0 / scales["lateness"]
        + component["new_lateness_square"] * 100.0 / scales["lateness_square"]
    )
    blocking = float(component["new_blocking"] * 20.0 / scales["blocking"])
    if reward_variant == "legacy_v2":
        production += blocking
    elif reward_variant == "cross_v3":
        computation += blocking
    elif reward_variant == "shared_block_v4":
        production += blocking
        computation += blocking
    elif reward_variant == "cross_v4":
        computation += blocking
        computation += component["terminal_max_timeout_lateness"] / scales["max_lateness"]
    else:
        raise ValueError(f"未知奖励版本：{reward_variant}")
    return production, computation


def run_episode(
    bundle: RuntimeBundle,
    seed: int,
    jobs: int,
    scenario_overrides: dict[str, Any] | None = None,
):
    checkpoint = bundle.checkpoint
    if isinstance(jobs, bool) or not isinstance(jobs, (int, np.integer)) or jobs < 1:
        raise ValueError("工件数量必须为正整数。")
    env = _make_env(seed, jobs, scenario_overrides)
    view = env.reset(seed=seed)
    if len(view["global_state"]) != int(checkpoint["base_dim"]):
        raise ValueError("整体状态摘要维度与模型不兼容。")
    counts = checkpoint.get("entity_counts")
    if counts is None:
        raise ValueError("当前检查点没有集合编码器，无法进行本次可变工件推理。")
    for name in ("job", "machine", "compute"):
        table = view[f"{name}_entities"]
        if table.ndim != 2 or table.shape[1] != 12 or len(table) < 1:
            raise ValueError(f"{name}实体特征必须为非空的12维记录集合。")
    device = next(bundle.model.parameters()).device
    learned_roles = set(checkpoint.get("learned_roles") or ())
    args = checkpoint.get("args", {})
    max_actions = int(checkpoint["max_actions"])
    routing_features = bool(args.get("routing_downstream_features", False))
    reward_variant = str(args.get("reward_variant", "legacy_v2"))
    rows = []
    episode_return = 0.0
    while not view["terminated"]:
        state, role, candidates, mask = _encode_view(
            view, max_actions, routing_downstream_features=routing_features
        )
        with torch.no_grad():
            distribution = bundle.model.distribution(
                _tensor_state(state, device),
                torch.as_tensor([role], dtype=torch.long, device=device),
                torch.as_tensor(candidates[None], dtype=torch.float32, device=device),
                torch.as_tensor(mask[None], dtype=torch.bool, device=device),
            )
            teacher_action = int(_teacher_action(env, view))
            action = (
                int(distribution.logits.argmax(-1).item())
                if view["kind"].lower() in learned_roles
                else teacher_action
            )
        next_view, _, done, info = env.step(action)
        reward_prod, reward_comp = _dual_reward(
            info, checkpoint["common_scales"], reward_variant
        )
        rows.append({
            "action": action,
            "kind": view["kind"],
            "teacher_action": teacher_action,
            "trainable": view["kind"].lower() in learned_roles,
        })
        episode_return += reward_prod + reward_comp
        view = next_view
    return rows, env.shop.metrics(), float(episode_return)


def replay_decisions(
    seed: int,
    jobs: int,
    trace: list[int],
    scenario_overrides: dict[str, Any] | None = None,
):
    env = _make_env(seed, jobs, scenario_overrides)
    view = env.reset(seed=seed)
    for action in trace:
        if view["terminated"]:
            break
        view, _, _, _ = env.step(int(action))
    return [dict(item) for item in env.shop.decision_history]
