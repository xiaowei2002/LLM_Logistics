"""事件驱动的生产调度与计算卸载联合环境。

* ROUTING：工序就绪时选择加工机器；
* SEQUENCING：机器空闲且存在多个候选工序时选择加工顺序；
* OFFLOADING：关键工序结束后选择LOCAL、EDGE1、EDGE2或CLOUD。

该环境最初用于确定性环境验证，随后接入MAPPO策略。
"""

from collections import deque
import hashlib
import itertools
import math
import random
from statistics import mean

import numpy as np
import simpy

from .demo_coupled_env import (
    formal_compute_config,
    complete_compute_config,
    generate_scene,
)


ROUTING_RULES = ("EA", "SPTM", "LQ", "LWT")
SEQUENCING_RULES = ("FIFO", "SPT", "EDD", "CR")
OFFLOADING_ACTIONS = ("LOCAL", "EDGE1", "EDGE2", "CLOUD")


class ComputeNode:
    """具有一个或多个并行计算槽位的不可抢占计算节点。"""

    def __init__(self, name, speed, transfer_time, slots=1):
        self.name = name
        self.speed = float(speed)
        self.transfer_time = transfer_time
        if slots < 1:
            raise ValueError("A compute node must have at least one slot")
        self.slot_available_at = [0.0] * int(slots)
        self.busy_time = 0.0

    @property
    def slots(self):
        return len(self.slot_available_at)

    @property
    def available_at(self):
        """返回任一计算槽位最早空闲的时刻。"""
        return min(self.slot_available_at)

    def select_slot(self):
        return min(
            range(self.slots),
            key=lambda index: (self.slot_available_at[index], index),
        )

    def predict(self, now, data, workload):
        transfer = float(self.transfer_time(data))
        arrival_at_node = now + transfer
        slot_index = self.select_slot()
        compute_start = max(arrival_at_node, self.slot_available_at[slot_index])
        compute_time = workload / self.speed
        finish = compute_start + compute_time
        return finish, transfer, compute_start, compute_time

    def predict_slot(self, now, data, workload):
        slot_index = self.select_slot()
        finish, transfer, compute_start, compute_time = self.predict(
            now, data, workload
        )
        return finish, transfer, compute_start, compute_time, slot_index

    def reserve(self, now, data, workload):
        result = self.predict_slot(now, data, workload)
        finish, transfer, compute_start, compute_time, slot_index = result
        self.slot_available_at[slot_index] = finish
        self.busy_time += compute_time
        return finish, transfer, compute_start, compute_time


class JointMachine:
    """队列选择由外部策略控制的不可抢占加工机器。"""

    def __init__(self, shop, machine_id):
        self.shop = shop
        self.env = shop.env
        self.machine_id = machine_id
        self.waiting = []
        self.sequence = itertools.count()
        self.wake_event = self.env.event()
        self.current_end = 0.0
        self.current_item = None
        self.busy_time = 0.0
        self.env.process(self.worker())

    @property
    def is_busy(self):
        return self.current_end > self.env.now

    def estimated_workload(self):
        running = max(0.0, self.current_end - self.env.now)
        return running + sum(item["processing_time"] for item in self.waiting)

    def enqueue(self, job, operation_index, processing_time):
        finished = self.env.event()
        self.waiting.append({
            "job": job,
            "operation_index": operation_index,
            "processing_time": float(processing_time),
            "arrival_order": next(self.sequence),
            "enqueue_time": float(self.env.now),
            "finished": finished,
        })
        if not self.wake_event.triggered:
            self.wake_event.succeed()
        return finished

    def worker(self):
        while True:
            if not self.waiting:
                self.wake_event = self.env.event()
                yield self.wake_event

            # 收集在同一时刻释放的其他工序，使排序决策能够看到完整候选集合。
            yield self.env.timeout(0)
            if len(self.waiting) > 1:
                request = self.shop.create_decision(
                    "SEQUENCING", machine=self, candidates=list(self.waiting)
                )
                candidate_text = ", ".join(
                    f"J{x['job'].job_id}-O{x['operation_index'] + 1}"
                    for x in self.waiting
                )
                decision = yield request["action_event"]
                if self.shop.direct_scheduling:
                    selected = request["candidates"][decision]
                    self.shop.trace.append((
                        self.env.now,
                        f"直接排序：M{self.machine_id}候选=[{candidate_text}]，"
                        f"选择J{selected['job'].job_id}-O{selected['operation_index'] + 1}",
                    ))
                else:
                    rule = SEQUENCING_RULES[decision]
                    selected = self.shop.select_sequence(self, rule)
                    self.shop.trace.append((
                        self.env.now,
                        f"排序决策：M{self.machine_id}候选=[{candidate_text}]，"
                        f"选择规则{rule} → J{selected['job'].job_id}-O{selected['operation_index'] + 1}",
                    ))
            else:
                selected = self.waiting[0]

            self.waiting.remove(selected)
            duration = selected["processing_time"]
            job = selected["job"]
            operation_index = selected["operation_index"]
            self.current_item = selected
            self.current_end = self.env.now + duration
            self.shop.trace.append((
                self.env.now,
                f"加工开始：J{job.job_id}-O{operation_index + 1}在M{self.machine_id}，"
                f"加工时间={duration:.1f}",
            ))
            yield self.env.timeout(duration)
            self.busy_time += duration
            self.current_item = None
            self.shop.completed_operations[job.job_id] = max(
                self.shop.completed_operations[job.job_id], operation_index + 1
            )
            self.shop.trace.append((
                self.env.now,
                f"加工完成：J{job.job_id}-O{operation_index + 1}在M{self.machine_id}",
            ))
            selected["finished"].succeed(self.machine_id)


class JointDecisionShop:
    def __init__(self, specs, scenario_seed=2026, stochastic=False,
                 compute_config=None, direct_scheduling=False, machine_config=None):
        self.env = simpy.Environment()
        self.specs = specs
        self.scenario_seed = int(scenario_seed)
        self.stochastic = bool(stochastic)
        self.direct_scheduling = bool(direct_scheduling)
        self.trace = []
        self.completions = {}
        self.completed_operations = {job.job_id: 0 for job in specs}
        self.blocking_times = []
        self.inspection_deadlines = []
        self.inspection_timeouts = 0
        self.inspection_lateness = []
        self.compute_choices = []
        self.compute_records = []
        self.decision_history = []
        self.decisions = deque()
        self.decision_signal = self.env.event()
        self.all_done = self.env.event()

        if machine_config is None:
            ids = {machine for job in specs for operation in job.operations for machine in operation.eligible_machines}
            machine_config = [{"machine_id": index} for index in range(max(ids) + 1)]
        machine_ids = [item["machine_id"] for item in machine_config]
        if machine_ids != list(range(len(machine_ids))):
            raise ValueError("机器编号必须为从0开始的连续整数")
        self.machines = [JointMachine(self, index) for index in machine_ids]

        config = complete_compute_config(
            formal_compute_config() if compute_config is None else compute_config
        )
        local_base = config["local_transfer_base"]
        local_rate = config["local_transfer_rate"]
        self.local_nodes = {
            m.machine_id: ComputeNode(
                f"LOCAL-M{m.machine_id}", config["local_speed"],
                lambda data, b=local_base, r=local_rate: b if r is None else b + data / r,
                slots=config["local_slots"],
            )
            for m in self.machines
        }
        self.edges = []
        self.edge_profiles = {}
        for edge in config["edge_configs"]:
            base, rate = edge["transfer_base"], edge["transfer_rate"]
            node = ComputeNode(
                edge["name"], edge["speed"],
                lambda data, b=base, r=rate: b + data / r,
                edge["slots"],
            )
            self.edges.append(node)
            self.edge_profiles[node.name] = {
                "near_machines": tuple(edge.get("near_machines", ())),
                "transfer_base": base,
                "transfer_rate": rate,
                "remote_transfer_base": edge.get("remote_transfer_base", base),
                "remote_transfer_rate": edge.get("remote_transfer_rate", rate),
                "transfer_sigma": float(edge.get("transfer_sigma", 0.24)),
                "compute_sigma": float(edge.get("compute_sigma", 0.18)),
            }
        self.cloud = ComputeNode(
            "CLOUD", config["cloud_speed"],
            lambda data, b=config["cloud_transfer_base"], r=config["cloud_transfer_rate"]: b + data / r,
            config["cloud_slots"],
        )

    def operation_options(self, operation):
        """返回一一对应的候选``(机器, 加工时间)``组合。"""
        ids = operation.eligible_machines
        times = operation.eligible_processing_times
        if not ids or len(ids) != len(times) or len(set(ids)) != len(ids):
            raise ValueError("每道工序必须有不重复的候选机器及一一对应的加工时间")
        if any(not 0 <= machine < len(self.machines) for machine in ids):
            raise ValueError("工序引用了不存在的机器")
        if any(not math.isfinite(float(value)) or value <= 0 for value in times):
            raise ValueError("加工时间必须为正的有限数")
        return tuple((self.machines[machine], float(processing)) for machine, processing in zip(ids, times))

    def operation_machines(self, operation):
        return tuple(machine for machine, _ in self.operation_options(operation))

    def operation_processing_times(self, operation):
        return tuple(processing for _, processing in self.operation_options(operation))

    def min_processing(self, operation):
        return min(self.operation_processing_times(operation))

    def transfer_time(self, node, source_machine, data):
        """传输由源机器到目标节点的链路决定，与加工候选关系无关。"""
        if node is self.local_nodes[source_machine]:
            return float(node.transfer_time(data))
        if node.name in self.edge_profiles:
            profile = self.edge_profiles[node.name]
            near = source_machine in profile["near_machines"]
            base = profile["transfer_base"] if near else profile["remote_transfer_base"]
            rate = profile["transfer_rate"] if near else profile["remote_transfer_rate"]
            return base + data / rate
        return float(node.transfer_time(data))

    def predict_compute(self, node, source_machine, data, workload):
        transfer = self.transfer_time(node, source_machine, data)
        slot = node.select_slot()
        arrival = self.env.now + transfer
        compute_start = max(arrival, node.slot_available_at[slot])
        compute_time = workload / node.speed
        finish = compute_start + compute_time
        return finish, transfer, compute_start, compute_time, slot

    def uncertainty_sigmas(self, node):
        if node.name.startswith("LOCAL"):
            return 0.02, 0.18
        if node.name in self.edge_profiles:
            profile = self.edge_profiles[node.name]
            return profile["transfer_sigma"], profile["compute_sigma"]
        return 0.45, 0.10

    def predict_risk_compute(self, node, source_machine, data, workload):
        transfer_sigma, compute_sigma = self.uncertainty_sigmas(node)
        z90 = 1.2815515655446004
        transfer_factor = math.exp(
            -0.5 * transfer_sigma ** 2 + transfer_sigma * z90
        )
        compute_factor = math.exp(
            -0.5 * compute_sigma ** 2 + compute_sigma * z90
        )
        transfer = self.transfer_time(node, source_machine, data) * transfer_factor
        duration = workload / node.speed * compute_factor
        slot = node.select_slot()
        arrival = self.env.now + transfer
        start = max(arrival, node.slot_available_at[slot])
        return start + duration, transfer, start, duration, slot

    def uncertainty_multipliers(self, node, noise_key):
        """任务与节点相关的外生噪声，在动作真正执行前不可见。

        稳定哈希为配对实验提供共同随机数：即使不同策略产生不同事件顺序，
        同一任务选择同一节点时仍会受到相同的外生扰动。节点特有的方差用于
        表示名义预测中无法直接观察的可靠性差异。
        """
        if not self.stochastic:
            return 1.0, 1.0
        material = f"{self.scenario_seed}|{noise_key}|{node.name}".encode("utf-8")
        local_seed = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
        rng = random.Random(local_seed)
        transfer_sigma, compute_sigma = self.uncertainty_sigmas(node)
        transfer_factor = rng.lognormvariate(
            -0.5 * transfer_sigma ** 2, transfer_sigma
        )
        compute_factor = rng.lognormvariate(
            -0.5 * compute_sigma ** 2, compute_sigma
        )
        return (
            min(2.2, max(0.45, transfer_factor)),
            min(2.0, max(0.50, compute_factor)),
        )

    def reserve_compute(self, node, source_machine, data, workload, noise_key):
        nominal_transfer = self.transfer_time(node, source_machine, data)
        transfer_factor, compute_factor = self.uncertainty_multipliers(
            node, noise_key
        )
        transfer = nominal_transfer * transfer_factor
        duration = workload / node.speed * compute_factor
        slot = node.select_slot()
        arrival = self.env.now + transfer
        start = max(arrival, node.slot_available_at[slot])
        finish = start + duration
        node.slot_available_at[slot] = finish
        node.busy_time += duration
        return (
            finish, transfer, start, duration,
            nominal_transfer, workload / node.speed,
        )

    def inspection_deadline(self, job, operation_index):
        """根据生产状态为新生成的质检任务确定完成期限。

        下一道工序必须等候至少一台候选机器就绪。期限中包含这段自然产生的
        隐性等待时间和一小段质检响应缓冲，因此生产准备就绪时期限更紧，
        下游机器已经拥堵时期限会适当放宽。
        """
        next_operation = job.operations[operation_index + 1]
        machines = self.operation_machines(next_operation)
        hidden_wait = min(machine.estimated_workload() for machine in machines)
        buffer = max(3.0, 0.25 * self.min_processing(next_operation))
        return float(self.env.now + hidden_wait + buffer)

    @property
    def pending_decision(self):
        return self.decisions[0] if self.decisions else None

    def create_decision(self, kind, **payload):
        request = {
            "kind": kind,
            "time": float(self.env.now),
            "action_event": self.env.event(),
            **payload,
        }
        self.decisions.append(request)
        if len(self.decisions) == 1 and not self.decision_signal.triggered:
            self.decision_signal.succeed()
        return request

    def wake_next_decision(self):
        yield self.env.timeout(0)
        if self.decisions and not self.decision_signal.triggered:
            self.decision_signal.succeed()

    def compute_candidates(self, source_machine):
        return (self.local_nodes[source_machine], *self.edges, self.cloud)

    def select_machine(self, job, operation_index, rule):
        operation = job.operations[operation_index]
        candidates = self.operation_options(operation)
        scored = []
        for machine, processing in candidates:
            if rule == "EA":
                score = machine.estimated_workload() + processing
            elif rule == "SPTM":
                score = processing
            elif rule == "LQ":
                score = len(machine.waiting) + int(machine.is_busy)
            elif rule == "LWT":
                score = machine.estimated_workload()
            else:
                raise ValueError(rule)
            scored.append((score, machine.machine_id, machine, processing))
        return min(scored, key=lambda x: (x[0], x[1]))

    def remaining_min_processing(self, item):
        job = item["job"]
        start = item["operation_index"]
        return sum(self.min_processing(op) for op in job.operations[start:])

    def select_sequence(self, machine, rule):
        if rule == "FIFO":
            key = lambda x: (x["arrival_order"], x["job"].job_id)
        elif rule == "SPT":
            key = lambda x: (x["processing_time"], x["arrival_order"])
        elif rule == "EDD":
            key = lambda x: (x["job"].due_date, x["arrival_order"])
        elif rule == "CR":
            key = lambda x: (
                x["job"].due_date - self.env.now - self.remaining_min_processing(x),
                x["arrival_order"],
            )
        else:
            raise ValueError(rule)
        return min(machine.waiting, key=key)

    def job_process(self, job):
        yield self.env.timeout(job.arrival)
        self.trace.append((self.env.now, f"J{job.job_id}到达车间"))
        for operation_index, operation in enumerate(job.operations):
            request = self.create_decision(
                "ROUTING", job=job, operation_index=operation_index
            )
            decision = yield request["action_event"]
            if self.direct_scheduling:
                machine, processing = self.operation_options(operation)[decision]
                machine_id = machine.machine_id
                self.trace.append((
                    self.env.now,
                    f"直接路由：J{job.job_id}-O{operation_index + 1}进入"
                    f"从工序候选机器中选择M{machine_id}"
                    f"（加工={processing:.1f}，负载={machine.estimated_workload():.1f}）",
                ))
            else:
                rule = ROUTING_RULES[decision]
                score, machine_id, machine, processing = self.select_machine(
                    job, operation_index, rule
                )
                self.trace.append((
                    self.env.now,
                    f"路由决策：J{job.job_id}-O{operation_index + 1}进入"
                    f"选择规则{rule} → M{machine_id}"
                    f"（评分={score:.1f}）",
                ))
            source_machine = yield machine.enqueue(
                job, operation_index, processing
            )

            if operation.requires_inspection:
                deadline = self.inspection_deadline(job, operation_index)
                request = self.create_decision(
                    "OFFLOADING", job=job, operation_index=operation_index,
                    source_machine=source_machine, deadline=deadline,
                )
                action = yield request["action_event"]
                node = self.compute_candidates(source_machine)[action]
                start = self.env.now
                (
                    finish, transfer, compute_start, compute_time,
                    nominal_transfer, nominal_compute,
                ) = self.reserve_compute(
                    node, source_machine,
                    operation.inspection_data, operation.inspection_workload,
                    noise_key=f"J{job.job_id}-O{operation_index + 1}",
                )
                queueing = compute_start - (start + transfer)
                blocking = finish - start
                self.blocking_times.append(blocking)
                self.inspection_deadlines.append(deadline - start)
                timed_out = finish > deadline
                lateness = max(0.0, finish - deadline)
                self.inspection_timeouts += int(timed_out)
                self.inspection_lateness.append(lateness)
                self.compute_choices.append(node.name)
                self.compute_records.append({
                    "node": node.name,
                    "transfer": transfer,
                    "queueing": queueing,
                    "compute_time": compute_time,
                    "blocking": blocking,
                    "timed_out": bool(timed_out),
                })
                self.trace.append((
                    start,
                    f"卸载决策：J{job.job_id}-O{operation_index + 1}产生质检任务，"
                    f"选择{node.name}；传输={transfer:.1f}，排队={queueing:.1f}，"
                    f"计算={compute_time:.1f}，后续工序阻塞={blocking:.1f}，"
                    f"容忍时延={deadline - start:.1f}，超时={'是' if timed_out else '否'}",
                ))
                if self.stochastic:
                    self.trace.append((
                        start,
                        f"执行波动：{node.name}名义传输/计算="
                        f"{nominal_transfer:.1f}/{nominal_compute:.1f}，"
                        f"实际={transfer:.1f}/{compute_time:.1f}",
                    ))
                yield self.env.timeout(blocking)
                self.trace.append((
                    self.env.now,
                    f"质检返回：J{job.job_id}-O{operation_index + 2}解除阻塞并触发新路由决策",
                ))

        self.completions[job.job_id] = self.env.now
        self.trace.append((self.env.now, f"J{job.job_id}完成全部工序"))
        if len(self.completions) == len(self.specs):
            self.all_done.succeed()

    def start(self):
        for job in self.specs:
            self.env.process(self.job_process(job))

    def metrics(self):
        makespan = max(self.completions.values())
        tardiness = [
            max(0.0, self.completions[j.job_id] - j.due_date) for j in self.specs
        ]
        edge_metrics = {}
        for edge in self.edges:
            records = [x for x in self.compute_records if x["node"] == edge.name]
            edge_metrics[edge.name] = {
                "tasks": len(records),
                "utilization": edge.busy_time / (edge.slots * makespan),
                "mean_queueing": mean(x["queueing"] for x in records) if records else 0.0,
                "mean_blocking": mean(x["blocking"] for x in records) if records else 0.0,
                "timeout_rate": mean(x["timed_out"] for x in records) if records else 0.0,
            }
        return {
            "makespan": makespan,
            "total_tardiness": sum(tardiness),
            "mean_blocking": mean(self.blocking_times) if self.blocking_times else 0.0,
            "inspection_timeout_rate": (
                self.inspection_timeouts / len(self.blocking_times)
                if self.blocking_times else 0.0
            ),
            "deadline_satisfaction_rate": (
                1.0 - self.inspection_timeouts / len(self.blocking_times)
                if self.blocking_times else 1.0
            ),
            "total_inspection_lateness": sum(self.inspection_lateness),
            "mean_timeout_lateness": (
                mean(value for value in self.inspection_lateness if value > 0)
                if self.inspection_timeouts else 0.0
            ),
            "max_timeout_lateness": (
                max(self.inspection_lateness) if self.inspection_lateness else 0.0
            ),
            "machine_utilization": mean(m.busy_time / makespan for m in self.machines),
            "edge_metrics": edge_metrics,
            "compute_choices": {
                name: self.compute_choices.count(name)
                for name in sorted(set(self.compute_choices))
            },
        }


class JointDecisionEnv:
    """外部事件决策接口，可由人工规则或MAPPO策略调用。"""

    default_action_names = {
        "ROUTING": ROUTING_RULES,
        "SEQUENCING": SEQUENCING_RULES,
        "OFFLOADING": OFFLOADING_ACTIONS,
    }
    job_entity_feature_size = 12
    machine_entity_feature_size = 12
    compute_entity_feature_size = 12
    routing_candidate_feature_size = 17

    def __init__(self, seed=2026, stochastic=False, reward_mode="shared",
                 deadline_slack_features=False, risk_slack_features=False,
                 scenario_profile="normal", deadline_reward_weight=1.0,
                 compute_config=None, scenario_config=None,
                 scalable_scheduling_features=False,
                 scheduling_potential_weight=0.0,
                 direct_scheduling=False,
                 tardiness_reward_weight=1.0,
                 makespan_reward_weight=1.0,
                 coupling_aware_scheduling_features=False,
                 critic_decision_context=False,
                 tail_lateness_reward_weight=0.0, resource_state_slots=None):
        self.seed = seed
        self.resource_state_slots = resource_state_slots
        self.stochastic = bool(stochastic)
        if reward_mode not in ("shared", "role_weighted"):
            raise ValueError(f"Unknown reward mode: {reward_mode}")
        self.reward_mode = reward_mode
        self.deadline_slack_features = bool(deadline_slack_features)
        self.risk_slack_features = bool(risk_slack_features)
        if self.deadline_slack_features and self.risk_slack_features:
            raise ValueError("Choose nominal or risk slack features, not both")
        if scenario_profile not in ("normal", "stress"):
            raise ValueError(f"Unknown scenario profile: {scenario_profile}")
        self.scenario_profile = scenario_profile
        if deadline_reward_weight < 0:
            raise ValueError("Deadline reward weight must be non-negative")
        self.deadline_reward_weight = float(deadline_reward_weight)
        if scheduling_potential_weight < 0:
            raise ValueError("Scheduling potential weight must be non-negative")
        self.scheduling_potential_weight = float(scheduling_potential_weight)
        if tardiness_reward_weight < 0 or makespan_reward_weight < 0:
            raise ValueError("Production reward weights must be non-negative")
        self.tardiness_reward_weight = float(tardiness_reward_weight)
        self.makespan_reward_weight = float(makespan_reward_weight)
        self.direct_scheduling = bool(direct_scheduling)
        self.coupling_aware_scheduling_features = bool(
            coupling_aware_scheduling_features
        )
        self.critic_decision_context = bool(critic_decision_context)
        if tail_lateness_reward_weight < 0:
            raise ValueError("Tail-lateness reward weight must be non-negative")
        self.tail_lateness_reward_weight = float(tail_lateness_reward_weight)
        self.compute_config = (
            formal_compute_config() if compute_config is None else compute_config
        )
        self.scenario_config = (
            {} if scenario_config is None else dict(scenario_config)
        )
        self.scalable_scheduling_features = bool(scalable_scheduling_features)
        edge_names = tuple(x["name"] for x in self.compute_config["edge_configs"])
        self.action_names = {
            "ROUTING": ROUTING_RULES,
            "SEQUENCING": SEQUENCING_RULES,
            "OFFLOADING": ("LOCAL", *edge_names, "CLOUD"),
        }
        self.offloading_observation_size = 22 + 5 * len(edge_names)
        self.global_state_size = (
            max(64, 52 + 3 * len(edge_names))
            + 16 * int(self.critic_decision_context)
        )
        self.routing_candidate_count = 1
        self.sequencing_candidate_count = int(
            self.scenario_config.get("number_of_jobs", 13)
        )
        self.scheduling_candidate_feature_size = (
            12 if self.coupling_aware_scheduling_features else 8
        )
        self.shop = None

    def reset(self, seed=None):
        self.seed = self.seed if seed is None else seed
        scenario_config = dict(self.scenario_config)
        scenario_config.setdefault("machine_flexibility", "partial")
        scene = generate_scene(
            seed=self.seed,
            profile="stress" if self.scenario_profile == "stress" else "formal",
            scenario_config=scenario_config,
            compute_config=self.compute_config,
        )
        self.scene = scene
        specs = scene["jobs"]
        self.shop = JointDecisionShop(
            specs,
            scenario_seed=self.seed,
            stochastic=self.stochastic,
            compute_config=scene["compute_config"],
            direct_scheduling=self.direct_scheduling,
            machine_config=scene["machines"],
        )
        self.shop.start()
        self.global_state_size = max(
            64,
            10 + 7 * len(self.shop.machines) + 3 * len(self.shop.edges),
        ) + 16 * int(self.critic_decision_context)
        if self.resource_state_slots is not None:
            machine_slots, edge_slots = self.resource_state_slots
            self.global_state_size = max(64, 10 + 7 * machine_slots + 3 * edge_slots) + 16 * int(self.critic_decision_context)
        self.routing_candidate_count = max(
            len(self.shop.operation_options(operation))
            for job in self.shop.specs for operation in job.operations
        )
        self.sequencing_candidate_count = len(self.shop.specs)
        self._advance()
        return self.agent_view()

    def _advance(self):
        if self.shop.all_done.triggered:
            return
        trigger = simpy.AnyOf(
            self.shop.env, [self.shop.decision_signal, self.shop.all_done]
        )
        self.shop.env.run(until=trigger)

    def decision_view(self):
        if self.shop.all_done.triggered:
            return {"terminated": True, "time": float(self.shop.env.now)}
        request = self.shop.pending_decision
        view = {
            "terminated": False,
            "time": float(self.shop.env.now),
            "kind": request["kind"],
            "actions": self.action_names[request["kind"]],
        }
        if request["kind"] == "ROUTING":
            view.update(job=request["job"].job_id,
                        operation=request["operation_index"] + 1)
            if self.direct_scheduling:
                operation = request["job"].operations[request["operation_index"]]
                view["actions"] = tuple(
                    f"M{machine.machine_id}"
                    for machine in self.shop.operation_machines(operation)
                )
        elif request["kind"] == "SEQUENCING":
            view.update(
                machine=request["machine"].machine_id,
                candidates=[
                    f"J{x['job'].job_id}-O{x['operation_index'] + 1}"
                    for x in request["candidates"]
                ],
            )
            if self.direct_scheduling:
                view["actions"] = tuple(view["candidates"])
        else:
            view.update(job=request["job"].job_id,
                        operation=request["operation_index"] + 1)
        return view

    @staticmethod
    def _pad(values, size):
        values = list(values)[:size]
        return np.asarray(values + [0.0] * (size - len(values)), dtype=np.float32)

    def scheduling_observation(self):
        """机器分配与加工排序事件共用的固定32维局部观测。"""
        if self.shop.all_done.triggered:
            return np.zeros(32, dtype=np.float32)
        request = self.shop.pending_decision
        if self.scalable_scheduling_features:
            return self.scalable_scheduling_observation(request)
        values = [
            float(request["kind"] == "ROUTING"),
            float(request["kind"] == "SEQUENCING"),
        ]
        if request["kind"] == "ROUTING":
            job = request["job"]
            index = request["operation_index"]
            operation = job.operations[index]
            processing_times = self.shop.operation_processing_times(operation)
            focal = [
                min(processing_times) / 15.0,
                mean(processing_times) / 15.0,
                (len(job.operations) - index - 1) / 6.0,
                np.clip((job.due_date - self.shop.env.now) / 100.0, -2, 2),
                0.0,
                len(processing_times) / 2.0,
            ]
        elif request["kind"] == "SEQUENCING":
            candidates = request["candidates"]
            durations = [x["processing_time"] for x in candidates]
            slacks = [
                x["job"].due_date - self.shop.env.now
                - self.shop.remaining_min_processing(x)
                for x in candidates
            ]
            machine = request["machine"]
            focal = [
                min(durations) / 15.0,
                mean(durations) / 15.0,
                min(slacks) / 100.0,
                mean(slacks) / 100.0,
                0.0,
                len(candidates) / 10.0,
            ]
        else:
            focal = [0.0] * 6
        values.extend(focal)
        for machine in self.shop.machines:
            values.extend([
                machine.estimated_workload() / 60.0,
                len(machine.waiting) / 10.0,
                float(machine.is_busy),
            ])
        nodes = (*self.shop.edges, self.shop.cloud)
        values.extend(
            max(0.0, node.available_at - self.shop.env.now) / 40.0
            for node in nodes
        )
        values.extend([
            len(self.shop.completions) / len(self.shop.specs),
            len(self.shop.decisions) / 10.0,
            self.shop.env.now / 200.0,
        ])
        return self._pad(values, 32)

    def scalable_scheduling_observation(self, request):
        """通过池化得到的32维调度状态，不会忽略编号靠后的机器。"""
        values = [
            float(request["kind"] == "ROUTING"),
            float(request["kind"] == "SEQUENCING"),
        ]
        if request["kind"] == "ROUTING":
            job = request["job"]
            index = request["operation_index"]
            operation = job.operations[index]
            processing_times = self.shop.operation_processing_times(operation)
            focal = [
                min(processing_times) / 15.0,
                mean(processing_times) / 15.0,
                (len(job.operations) - index - 1) / 6.0,
                np.clip((job.due_date - self.shop.env.now) / 100.0, -2, 2),
                0.0,
                len(processing_times) / 4.0,
            ]
        elif request["kind"] == "SEQUENCING":
            candidates = request["candidates"]
            durations = [x["processing_time"] for x in candidates]
            slacks = [
                x["job"].due_date - self.shop.env.now
                - self.shop.remaining_min_processing(x)
                for x in candidates
            ]
            machine = request["machine"]
            focal = [
                min(durations) / 15.0,
                mean(durations) / 15.0,
                min(slacks) / 100.0,
                mean(slacks) / 100.0,
                0.0,
                len(candidates) / 10.0,
            ]
        else:
            focal = [0.0] * 6
        values.extend(focal)

        workloads = [machine.estimated_workload() for machine in self.shop.machines]
        queues = [len(machine.waiting) for machine in self.shop.machines]
        values.extend([
            min(workloads) / 60.0,
            mean(workloads) / 60.0,
            max(workloads) / 60.0,
            float(np.std(workloads)) / 60.0,
            mean(queues) / 10.0,
            max(queues) / 10.0,
            mean(float(machine.is_busy) for machine in self.shop.machines),
        ])

        # 定长的全车间分位数摘要；不代表任何加工分组。
        for quantile in (0.0, 0.25, 0.5, 0.75, 1.0):
            values.extend([
                float(np.quantile(workloads, quantile)) / 60.0,
                float(np.quantile(queues, quantile)) / 10.0,
                mean(float(machine.is_busy) for machine in self.shop.machines),
            ])
        values.extend([
            len(self.shop.completions) / len(self.shop.specs),
            self.shop.env.now / 250.0,
        ])
        return self._pad(values, 32)

    def offloading_observation(self):
        """可适配计算节点数量的卸载Actor局部观测。"""
        if self.shop.all_done.triggered:
            return np.zeros(self.offloading_observation_size, dtype=np.float32)
        request = self.shop.pending_decision
        if request["kind"] != "OFFLOADING":
            return np.zeros(self.offloading_observation_size, dtype=np.float32)
        job = request["job"]
        index = request["operation_index"]
        operation = job.operations[index]
        deadline = request["deadline"]
        values = [
            operation.inspection_data / 32.0,
            operation.inspection_workload / 70.0,
            (len(job.operations) - index - 1) / 6.0,
            np.clip((job.due_date - self.shop.env.now) / 100.0, -2, 2),
            np.clip((deadline - self.shop.env.now) / 40.0, 0, 2),
            0.0,
        ]
        deadline_slacks = []
        risk_deadline_slacks = []
        for node in self.shop.compute_candidates(request["source_machine"]):
            finish, transfer, start, duration, _ = self.shop.predict_compute(
                node, request["source_machine"],
                operation.inspection_data, operation.inspection_workload,
            )
            values.extend([
                transfer / 12.0,
                max(0.0, start - self.shop.env.now - transfer) / 25.0,
                duration / 35.0,
                (finish - self.shop.env.now) / 40.0,
            ])
            deadline_slacks.append(
                np.clip((deadline - finish) / 20.0, -2.0, 2.0)
            )
            risk_finish, *_ = self.shop.predict_risk_compute(
                node, request["source_machine"],
                operation.inspection_data, operation.inspection_workload,
            )
            risk_deadline_slacks.append(
                np.clip((deadline - risk_finish) / 20.0, -2.0, 2.0)
            )
        next_operation = job.operations[index + 1]
        machines = self.shop.operation_machines(next_operation)
        processing_times = self.shop.operation_processing_times(next_operation)
        workloads = [m.estimated_workload() for m in machines]
        values.extend([
            min(processing_times) / 15.0,
            mean(processing_times) / 15.0,
            min(workloads) / 60.0,
            mean(workloads) / 60.0,
            len(self.shop.decisions) / 10.0,
            self.shop.env.now / 200.0,
        ])
        # 保留原有28维特征的位置。此前补零的4个位置现在表示LOCAL、EDGE1、
        # EDGE2和CLOUD的带符号名义期限裕量；正值表示预计按时完成，负值表示预计超时。
        if self.risk_slack_features:
            values.extend(risk_deadline_slacks)
        elif self.deadline_slack_features:
            values.extend(deadline_slacks)
        else:
            values.extend([0.0] * len(deadline_slacks))
        return self._pad(values, self.offloading_observation_size)

    def direct_routing_candidates(self):
        """每台候选机器的路由特征，其中包含下游队列风险。

        前12维保留原有局部特征和耦合特征，后5维明确描述候选机器与当前工件
        之间的关系，包括预计交期裕量、机器队列中已有工件的紧迫程度，以及
        当前工件后续工序候选机器的压力。对全局机器和工件实体做池化时，这些关系
        信息会丢失。
        """
        features = np.zeros(
            (self.routing_candidate_count, self.routing_candidate_feature_size),
            dtype=np.float32,
        )
        mask = np.zeros(self.routing_candidate_count, dtype=np.int8)
        if self.shop.all_done.triggered or not self.direct_scheduling:
            return features, mask
        request = self.shop.pending_decision
        if request["kind"] != "ROUTING":
            return features, mask
        job = request["job"]
        index = request["operation_index"]
        operation = job.operations[index]
        remaining = sum(
            self.shop.min_processing(item) for item in job.operations[index:]
        )
        remaining_after = sum(
            self.shop.min_processing(item) for item in job.operations[index + 1:]
        )
        slack = job.due_date - self.shop.env.now - remaining
        options = self.shop.operation_options(operation)
        for candidate, (machine, processing) in enumerate(options):
            workload = machine.estimated_workload()
            production_features = (
                processing / 15.0,
                workload / 60.0,
                len(machine.waiting) / 10.0,
                float(machine.is_busy),
                (workload + processing) / 60.0,
                machine.busy_time / max(1.0, self.shop.env.now),
                np.clip(slack / 100.0, -2.0, 2.0),
                index / max(1, len(job.operations) - 1),
            )
            features[candidate, :8] = production_features
            if self.coupling_aware_scheduling_features:
                features[candidate, 8:12] = self._operation_compute_features(
                    operation, machine.machine_id
                )
            projected_slack = (
                job.due_date - self.shop.env.now - workload - processing
                - remaining_after
            )
            queue_slacks = [
                item["job"].due_date - self.shop.env.now
                - self.shop.remaining_min_processing(item)
                for item in machine.waiting
            ]
            minimum_queue_slack = min(queue_slacks) if queue_slacks else 0.0
            urgent_fraction = (
                sum(value < 0.0 for value in queue_slacks) / len(queue_slacks)
                if queue_slacks else 0.0
            )
            next_stage_pressure = 0.0
            if index + 1 < len(job.operations):
                next_operation = job.operations[index + 1]
                next_stage_pressure = min(
                    next_machine.estimated_workload() + next_processing
                    for next_machine, next_processing
                    in self.shop.operation_options(next_operation)
                )
            features[candidate, 12:] = (
                np.clip(projected_slack / 100.0, -2.0, 2.0),
                np.clip(max(0.0, -projected_slack) / 100.0, 0.0, 2.0),
                np.clip(minimum_queue_slack / 100.0, -2.0, 2.0),
                urgent_fraction,
                np.clip(next_stage_pressure / 60.0, 0.0, 2.0),
            )
            mask[candidate] = 1
        return features, mask

    def _operation_compute_features(self, operation, source_machine):
        """工序进入调度前可见的计算侧信息。"""
        if not operation.requires_inspection:
            return np.zeros(4, dtype=np.float32)
        risk_finishes = [
            self.shop.predict_risk_compute(
                node, source_machine,
                operation.inspection_data, operation.inspection_workload,
            )[0]
            for node in self.shop.compute_candidates(source_machine)
        ]
        best_risk_delay = max(0.0, min(risk_finishes) - self.shop.env.now)
        return np.asarray((
            1.0,
            np.clip(operation.inspection_data / 32.0, 0.0, 2.0),
            np.clip(operation.inspection_workload / 105.0, 0.0, 2.0),
            np.clip(best_risk_delay / 40.0, 0.0, 2.0),
        ), dtype=np.float32)

    def direct_sequencing_candidates(self):
        """用于直接选择加工队列的定长工序特征。"""
        features = np.zeros(
            (
                self.sequencing_candidate_count,
                self.scheduling_candidate_feature_size,
            ),
            dtype=np.float32,
        )
        mask = np.zeros(self.sequencing_candidate_count, dtype=np.int8)
        if self.shop.all_done.triggered or not self.direct_scheduling:
            return features, mask
        request = self.shop.pending_decision
        if request["kind"] != "SEQUENCING":
            return features, mask
        machine = request["machine"]
        for candidate, item in enumerate(request["candidates"]):
            if candidate >= self.sequencing_candidate_count:
                break
            job = item["job"]
            remaining = self.shop.remaining_min_processing(item)
            slack = job.due_date - self.shop.env.now - remaining
            production_features = (
                item["processing_time"] / 15.0,
                (self.shop.env.now - item["enqueue_time"]) / 50.0,
                np.clip(slack / 100.0, -2.0, 2.0),
                remaining / 100.0,
                item["operation_index"] / max(1, len(job.operations) - 1),
                machine.estimated_workload() / 60.0,
                len(machine.waiting) / 10.0,
                (self.shop.env.now - job.arrival) / 100.0,
            )
            features[candidate, :8] = production_features
            if self.coupling_aware_scheduling_features:
                operation = job.operations[item["operation_index"]]
                features[candidate, 8:] = self._operation_compute_features(
                    operation, machine.machine_id
                )
            mask[candidate] = 1
        return features, mask

    def action_masks(self):
        """双智能体动作掩码，其中卸载维度可适配计算节点数量。"""
        scheduling = np.zeros(8, dtype=np.int8)
        offloading = np.zeros(len(self.action_names["OFFLOADING"]), dtype=np.int8)
        if not self.shop.all_done.triggered:
            kind = self.shop.pending_decision["kind"]
            if kind == "ROUTING":
                scheduling[:4] = 1
            elif kind == "SEQUENCING":
                scheduling[4:] = 1
            else:
                offloading[:] = 1
        return scheduling, offloading

    @staticmethod
    def _resource_summary(rows, slots):
        """兼容旧检查点的定长摘要：所有资源参与平均，不截取前几台。"""
        if len(rows) == slots:
            return rows
        # 自适应区间池化；资源不足时允许区间重叠，避免虚构空闲机器。
        return [
            np.asarray(rows[int(index * len(rows) // slots):
                            max(int(np.ceil((index + 1) * len(rows) / slots)), int(index * len(rows) // slots) + 1)], dtype=float).mean(axis=0).tolist()
            for index in range(slots)
        ]

    def centralized_state(self):
        """整体状态摘要；完整资源信息同时保留在可变长度实体集合中。"""
        if self.shop.all_done.triggered:
            return np.zeros(self.global_state_size, dtype=np.float32)
        values = [
            self.shop.env.now / 200.0,
            len(self.shop.completions) / len(self.shop.specs),
            len(self.shop.decisions) / 10.0,
            self.shop.inspection_timeouts / 20.0,
        ]
        machine_rows = [[
            machine.estimated_workload() / 60.0,
            len(machine.waiting) / 10.0,
            float(machine.is_busy),
            machine.busy_time / max(1.0, self.shop.env.now),
        ] for machine in self.shop.machines]
        def node_rows(nodes):
            rows = []
            for node in nodes:
                waits = [max(0.0, t - self.shop.env.now) for t in node.slot_available_at]
                rows.append([min(waits) / 40.0, mean(waits) / 40.0, node.speed / 14.0])
            return rows
        locals_ = node_rows(self.shop.local_nodes.values())
        edges = node_rows(self.shop.edges)
        if self.resource_state_slots is not None:
            machine_slots, edge_slots = self.resource_state_slots
            machine_rows = self._resource_summary(machine_rows, machine_slots)
            locals_ = self._resource_summary(locals_, machine_slots)
            edges = self._resource_summary(edges, edge_slots)
        for row in (*machine_rows, *locals_, *edges, *node_rows([self.shop.cloud])):
            values.extend(row)
        request = self.shop.pending_decision
        values.extend([
            float(request["kind"] == "ROUTING"),
            float(request["kind"] == "SEQUENCING"),
            float(request["kind"] == "OFFLOADING"),
        ])
        if self.critic_decision_context:
            values.extend(self.critic_decision_context_features())
        return self._pad(values, self.global_state_size)

    def critic_decision_context_features(self):
        """为Critic提供当前待决策事件的定长描述。

        Actor已经能够观察候选动作和任务细节。这些特征可以避免集中式价值函数
        仅因总体负载相近，就把不同待处理工件或质检任务误认为相同状态。
        """
        request = self.shop.pending_decision
        kind = request["kind"]
        if kind == "ROUTING":
            job = request["job"]
            index = request["operation_index"]
            operation = job.operations[index]
            machines = self.shop.operation_machines(operation)
            processing = list(self.shop.operation_processing_times(operation))
            workloads = [machine.estimated_workload() for machine in machines]
            remaining = sum(
                self.shop.min_processing(item) for item in job.operations[index:]
            )
            compute = [
                self._operation_compute_features(operation, machine.machine_id)
                for machine in machines
            ]
            risks = [item[3] for item in compute]
            return np.asarray([
                index / max(1, len(job.operations) - 1),
                (len(job.operations) - index) / 6.0,
                np.clip((job.due_date - self.shop.env.now - remaining) / 100.0,
                        -2.0, 2.0),
                len(machines) / 5.0,
                min(processing) / 15.0,
                mean(processing) / 15.0,
                max(processing) / 15.0,
                min(workloads) / 60.0,
                mean(workloads) / 60.0,
                max(workloads) / 60.0,
                float(operation.requires_inspection),
                operation.inspection_data / 32.0,
                operation.inspection_workload / 105.0,
                min(risks),
                mean(risks),
                np.clip((job.due_date - self.shop.env.now) / 100.0, -2.0, 2.0),
            ], dtype=np.float32)

        if kind == "SEQUENCING":
            machine = request["machine"]
            candidates = request["candidates"]
            processing = [item["processing_time"] for item in candidates]
            slacks, progresses, compute = [], [], []
            for item in candidates:
                job = item["job"]
                index = item["operation_index"]
                slacks.append(
                    job.due_date - self.shop.env.now
                    - self.shop.remaining_min_processing(item)
                )
                progresses.append(index / max(1, len(job.operations) - 1))
                compute.append(self._operation_compute_features(
                    job.operations[index], machine.machine_id
                ))
            return np.asarray([
                len(candidates) / 10.0,
                machine.estimated_workload() / 60.0,
                len(machine.waiting) / 10.0,
                float(machine.is_busy),
                min(processing) / 15.0,
                mean(processing) / 15.0,
                max(processing) / 15.0,
                np.clip(min(slacks) / 100.0, -2.0, 2.0),
                np.clip(mean(slacks) / 100.0, -2.0, 2.0),
                np.clip(max(slacks) / 100.0, -2.0, 2.0),
                mean(progresses),
                mean(item[0] for item in compute),
                mean(item[1] for item in compute),
                mean(item[2] for item in compute),
                min(item[3] for item in compute),
                mean(item[3] for item in compute),
            ], dtype=np.float32)

        observation = self.offloading_observation()
        # 前6维描述任务；此后每个节点用4维分别描述传输、排队、计算和完成时延。
        node_count = len(self.shop.compute_candidates(request["source_machine"]))
        node_values = observation[6:6 + 4 * node_count].reshape(node_count, 4)
        return np.asarray([
            *observation[:6],
            node_count / 8.0,
            node_values[:, 0].min(), node_values[:, 0].mean(),
            node_values[:, 0].max(),
            node_values[:, 1].min(), node_values[:, 1].mean(),
            node_values[:, 1].max(),
            node_values[:, 3].min(), node_values[:, 3].mean(),
            node_values[:, 3].max(),
        ], dtype=np.float32)

    def entity_state(self):
        """用于表示生产与计算后续耦合结构的实体集合。"""
        now = self.shop.env.now
        assigned_jobs = set()
        for machine in self.shop.machines:
            if machine.current_item is not None:
                assigned_jobs.add(machine.current_item["job"].job_id)
            assigned_jobs.update(item["job"].job_id for item in machine.waiting)
        pending_jobs = {
            request["job"].job_id
            for request in self.shop.decisions
            if "job" in request
        }

        jobs = np.zeros(
            (len(self.shop.specs), self.job_entity_feature_size),
            dtype=np.float32,
        )
        job_mask = np.zeros(len(self.shop.specs), dtype=np.int8)
        for row, job in enumerate(self.shop.specs):
            if job.arrival > now:
                continue
            job_mask[row] = 1
            completed = self.shop.completed_operations[job.job_id]
            done = job.job_id in self.shop.completions
            remaining_operations = job.operations[completed:]
            remaining_processing = sum(
                self.shop.min_processing(operation)
                for operation in remaining_operations
            )
            inspection_operations = [
                operation for operation in remaining_operations
                if operation.requires_inspection
            ]
            assigned = job.job_id in assigned_jobs
            blocked = (
                not done and completed > 0 and not assigned
                and job.job_id not in pending_jobs
            )
            jobs[row] = (
                1.0,
                float(done),
                completed / max(1, len(job.operations)),
                len(remaining_operations) / 6.0,
                remaining_processing / 100.0,
                np.clip((job.due_date - now) / 100.0, -2.0, 2.0),
                np.clip(
                    (job.due_date - now - remaining_processing) / 100.0,
                    -2.0, 2.0,
                ),
                len(inspection_operations) / 6.0,
                sum(x.inspection_data for x in inspection_operations) / 192.0,
                sum(x.inspection_workload for x in inspection_operations) / 630.0,
                float(assigned),
                float(blocked),
            )

        machines = np.zeros(
            (len(self.shop.machines), self.machine_entity_feature_size),
            dtype=np.float32,
        )
        machine_mask = np.ones(len(self.shop.machines), dtype=np.int8)
        for row, machine in enumerate(self.shop.machines):
            running = max(0.0, machine.current_end - now)
            current_operation = None
            if machine.current_item is not None:
                item = machine.current_item
                current_operation = item["job"].operations[item["operation_index"]]
            waiting_operations = [
                item["job"].operations[item["operation_index"]]
                for item in machine.waiting
            ]
            waiting_inspections = [
                operation for operation in waiting_operations
                if operation.requires_inspection
            ]
            queue_slacks = [
                item["job"].due_date - now
                - self.shop.remaining_min_processing(item)
                for item in machine.waiting
            ]
            machines[row] = (
                0.0,
                float(machine.is_busy),
                running / 60.0,
                machine.estimated_workload() / 60.0,
                len(machine.waiting) / 10.0,
                machine.busy_time / max(1.0, now),
                float(current_operation.requires_inspection)
                if current_operation is not None else 0.0,
                current_operation.inspection_data / 32.0
                if current_operation is not None else 0.0,
                current_operation.inspection_workload / 105.0
                if current_operation is not None else 0.0,
                len(waiting_inspections) / max(1, len(waiting_operations)),
                sum(x.inspection_workload for x in waiting_inspections) / 420.0,
                np.clip(min(queue_slacks) / 100.0, -2.0, 2.0)
                if queue_slacks else 0.0,
            )

        all_nodes = (
            *self.shop.local_nodes.values(), *self.shop.edges, self.shop.cloud
        )
        compute = np.zeros(
            (len(all_nodes), self.compute_entity_feature_size),
            dtype=np.float32,
        )
        compute_mask = np.ones(len(all_nodes), dtype=np.int8)
        for row, node in enumerate(all_nodes):
            waits = np.asarray([
                max(0.0, available - now)
                for available in node.slot_available_at
            ], dtype=np.float32)
            is_local = node.name.startswith("LOCAL")
            is_edge = node.name.startswith("EDGE")
            # 旧区域特征位置保留为0，真实传输差异由候选动作中的链路特征描述。
            source_location = 0.0
            task_count = sum(
                record["node"] == node.name
                for record in self.shop.compute_records
            )
            compute[row] = (
                float(is_local), float(is_edge), float(node is self.shop.cloud),
                node.speed / 14.0,
                node.slots / 4.0,
                waits.min() / 40.0,
                waits.mean() / 40.0,
                waits.max() / 40.0,
                float(np.mean(waits > 0.0)),
                node.busy_time / max(1.0, node.slots * now),
                task_count / 20.0,
                source_location,
            )
        return {
            "job_entities": jobs, "job_entity_mask": job_mask,
            "machine_entities": machines, "machine_entity_mask": machine_mask,
            "compute_entities": compute, "compute_entity_mask": compute_mask,
        }

    def routing_relation_state(self):
        """可选关系型路由Actor使用的显式关联信息。

        实体池化能够告诉Actor当前有哪些工件和机器，但会丢失二者之间的分配关系。
        这些张量保留当前需要路由的工件、每台机器上正在加工或等待的工件，以及
        每个路由候选项对应的实际机器编号。
        """
        job_count = len(self.shop.specs)
        machine_count = len(self.shop.machines)
        job_row = {job.job_id: row for row, job in enumerate(self.shop.specs)}
        focal_job = np.zeros(job_count, dtype=np.float32)
        assignment = np.zeros((job_count, machine_count), dtype=np.float32)
        machine_ids = np.full(self.routing_candidate_count, -1, dtype=np.int64)
        for machine in self.shop.machines:
            items = list(machine.waiting)
            if machine.current_item is not None:
                items.append(machine.current_item)
            for item in items:
                row = job_row.get(item["job"].job_id)
                if row is not None:
                    assignment[row, machine.machine_id] = 1.0
        if not self.shop.all_done.triggered:
            request = self.shop.pending_decision
            if request["kind"] == "ROUTING":
                row = job_row.get(request["job"].job_id)
                if row is not None:
                    focal_job[row] = 1.0
                operation = request["job"].operations[request["operation_index"]]
                machines = self.shop.operation_machines(operation)
                for candidate, machine in enumerate(machines):
                    machine_ids[candidate] = machine.machine_id
        return {
            "routing_focal_job": focal_job,
            "job_machine_assignment": assignment,
            "routing_machine_ids": machine_ids,
        }

    def agent_view(self):
        decision = self.decision_view()
        scheduling_mask, offloading_mask = self.action_masks()
        routing_candidates, routing_mask = self.direct_routing_candidates()
        sequencing_candidates, sequencing_mask = (
            self.direct_sequencing_candidates()
        )
        if decision["terminated"]:
            active_agent = None
        elif decision.get("kind") == "OFFLOADING":
            active_agent = "offloading"
        elif self.direct_scheduling and decision.get("kind") == "ROUTING":
            active_agent = "routing"
        elif self.direct_scheduling and decision.get("kind") == "SEQUENCING":
            active_agent = "sequencing"
        else:
            active_agent = "scheduling"
        return {
            **decision,
            **self.entity_state(),
            **self.routing_relation_state(),
            "active_agent": active_agent,
            "scheduling_obs": self.scheduling_observation(),
            "offloading_obs": self.offloading_observation(),
            "scheduling_mask": scheduling_mask,
            "offloading_mask": offloading_mask,
            "routing_candidates": routing_candidates,
            "routing_mask": routing_mask,
            "sequencing_candidates": sequencing_candidates,
            "sequencing_mask": sequencing_mask,
            "global_state": self.centralized_state(),
        }

    def reward_potential(self):
        jobs = {job.job_id: job for job in self.shop.specs}
        realized_tardiness = sum(
            max(0.0, finish - jobs[job_id].due_date)
            for job_id, finish in self.shop.completions.items()
        )
        return {
            "tardiness": realized_tardiness,
            "blocking": sum(self.shop.blocking_times),
            "timeouts": float(self.shop.inspection_timeouts),
            "lateness": sum(self.shop.inspection_lateness),
            "lateness_square": sum(
                value * value for value in self.shop.inspection_lateness
            ),
            "max_lateness": (
                max(self.shop.inspection_lateness)
                if self.shop.inspection_lateness else 0.0
            ),
        }

    def production_tardiness_potential(self):
        """用于密集奖励塑形的负预计拖期下界。

        剩余加工时间按每道后续工序的最快候选机器计算。对于已经分配到机器的工序，
        下界还包含近似排队时延，以及相对最快机器下界多出的加工时间。因此数值越
        接近零，表示当前生产状态越接近满足交期；已完成工件的贡献为零。
        """
        if self.shop.all_done.triggered:
            return 0.0
        assigned_delay = {}
        for machine in self.shop.machines:
            running = max(0.0, machine.current_end - self.shop.env.now)
            waiting_total = sum(
                item["processing_time"] for item in machine.waiting
            )
            if machine.current_item is not None:
                item = machine.current_item
                operation = item["job"].operations[item["operation_index"]]
                assigned_delay[item["job"].job_id] = max(
                    0.0, running - self.shop.min_processing(operation)
                )
            for item in machine.waiting:
                operation = item["job"].operations[item["operation_index"]]
                average_ahead = 0.5 * (
                    waiting_total - item["processing_time"]
                )
                assigned_delay[item["job"].job_id] = (
                    running + average_ahead
                    + max(0.0, item["processing_time"]
                          - self.shop.min_processing(operation))
                )

        predicted_tardiness = 0.0
        for job in self.shop.specs:
            if job.job_id in self.shop.completions:
                continue
            start = self.shop.completed_operations[job.job_id]
            remaining = sum(
                self.shop.min_processing(operation)
                for operation in job.operations[start:]
            )
            predicted_finish = (
                self.shop.env.now + remaining
                + assigned_delay.get(job.job_id, 0.0)
            )
            predicted_tardiness += max(0.0, predicted_finish - job.due_date)
        return -predicted_tardiness / 50.0

    def step(self, action):
        if self.shop.all_done.triggered:
            raise RuntimeError("Episode terminated")
        before = self.reward_potential()
        production_potential_before = self.production_tardiness_potential()
        request = self.shop.decisions.popleft()
        action = int(action)
        if self.direct_scheduling and request["kind"] == "ROUTING":
            operation = request["job"].operations[request["operation_index"]]
            candidates = self.shop.operation_machines(operation)
            names = tuple(f"M{machine.machine_id}" for machine in candidates)
            local_action = action
        elif self.direct_scheduling and request["kind"] == "SEQUENCING":
            names = tuple(
                f"J{item['job'].job_id}-O{item['operation_index'] + 1}"
                for item in request["candidates"]
            )
            local_action = action
        else:
            names = self.action_names[request["kind"]]
            local_action = (
                action - 4 if request["kind"] == "SEQUENCING" else action
            )
        if not 0 <= local_action < len(names):
            raise ValueError(f"Invalid {request['kind']} action: {action}")
        self.shop.decision_history.append({
            "time": float(self.shop.env.now),
            "kind": request["kind"],
            "action": names[local_action],
        })
        self.shop.decision_signal = self.shop.env.event()
        request["action_event"].succeed(local_action)
        if self.shop.decisions:
            self.shop.env.process(self.shop.wake_next_decision())
        self._advance()
        terminated = self.shop.all_done.triggered
        after = self.reward_potential()
        production_potential_after = self.production_tardiness_potential()
        potential_shaping = self.scheduling_potential_weight * (
            0.985 * production_potential_after - production_potential_before
        )
        components = {
            "new_tardiness": -(after["tardiness"] - before["tardiness"]) / 50.0,
            "new_blocking": -(after["blocking"] - before["blocking"]) / 20.0,
            "new_timeout": -(after["timeouts"] - before["timeouts"]),
            "new_lateness": -(after["lateness"] - before["lateness"]) / 10.0,
            "new_lateness_square": -(
                after["lateness_square"] - before["lateness_square"]
            ) / 100.0,
            "terminal_makespan": (
                -self.shop.metrics()["makespan"] / 200.0 if terminated else 0.0
            ),
            "terminal_max_timeout_lateness": (
                -after["max_lateness"] if terminated else 0.0
            ),
        }
        if self.scheduling_potential_weight:
            components["scheduling_potential"] = potential_shaping
        base_production_reward = (
            self.tardiness_reward_weight * components["new_tardiness"]
            + self.makespan_reward_weight * components["terminal_makespan"]
        )
        production_reward = (
            base_production_reward
            + components.get("scheduling_potential", 0.0)
        )
        deadline_reward = (
            components["new_timeout"] + components["new_lateness"]
        )
        compute_reward = (
            components["new_blocking"]
            + self.deadline_reward_weight * deadline_reward
            + self.tail_lateness_reward_weight
            * components["new_lateness_square"]
        )
        shared_reward = float(production_reward + compute_reward)
        if self.reward_mode == "role_weighted":
            if request["kind"] == "OFFLOADING":
                # 调度势函数塑形不能用于训练卸载Actor，否则无法隔离调度动作的贡献。
                reward = compute_reward + 0.1 * base_production_reward
            else:
                reward = production_reward + 0.1 * compute_reward
        else:
            reward = shared_reward
        return self.agent_view(), float(reward), terminated, {
            "reward_components": components,
            "reward_mode": self.reward_mode,
            "deadline_reward_weight": self.deadline_reward_weight,
            "tail_lateness_reward_weight": self.tail_lateness_reward_weight,
            "scheduling_potential_weight": self.scheduling_potential_weight,
            "tardiness_reward_weight": self.tardiness_reward_weight,
            "makespan_reward_weight": self.makespan_reward_weight,
            "shared_reward": shared_reward,
            "production_reward": float(production_reward),
            "compute_reward": float(compute_reward),
        }


def run_manual_policy(seed=2026):
    """仅用于验证三类决策入口是否正常的可审计人工策略。"""
    env = JointDecisionEnv(seed)
    view = env.reset()
    decisions = 0
    while not view["terminated"]:
        if view["kind"] == "ROUTING":
            # 交替采用不同合法规则，以便通过轨迹验证路由决策确实由外部输入。
            action = (view["job"] + view["operation"]) % len(ROUTING_RULES)
        elif view["kind"] == "SEQUENCING":
            action = 6  # 全局调度动作偏移量4，加上EDD的局部索引2
        else:
            request = env.shop.pending_decision
            operation = request["job"].operations[request["operation_index"]]
            candidates = env.shop.compute_candidates(request["source_machine"])
            finishes = [
                env.shop.predict_compute(
                    n, request["source_machine"],
                    operation.inspection_data,
                    operation.inspection_workload,
                )[0]
                for n in candidates
            ]
            action = int(np.argmin(finishes))
        decisions += 1
        view, _, _, _ = env.step(action)
    return env, decisions

