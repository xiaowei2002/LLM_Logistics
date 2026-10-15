"""生成无加工分组的联合调度场景；工序直接声明候选机器和对应加工时间。"""

from copy import deepcopy
from dataclasses import asdict, dataclass
import random

RANDOM_SEED = 2026
NUMBER_OF_JOBS = 20
INITIAL_JOBS = 6
MACHINE_COUNT = 6
EDGE_COUNT = 2


@dataclass(frozen=True)
class OperationSpec:
    eligible_machines: tuple
    eligible_processing_times: tuple
    inspection_data: float = 0.0
    inspection_workload: float = 0.0

    @property
    def requires_inspection(self):
        return self.inspection_workload > 0.0


@dataclass(frozen=True)
class JobSpec:
    job_id: int
    arrival: float
    operations: tuple
    due_date: float


def positive_integer(name, value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name}必须为正整数")
    return value


def generate_scenario(seed=RANDOM_SEED, mean_interarrival=5.0,
                      min_operations=3, max_operations=3,
                      min_inspections=1, max_inspections=1,
                      fixed_inspection_index=1, inspection_workload_scale=1.0,
                      machine_count=MACHINE_COUNT, number_of_jobs=NUMBER_OF_JOBS,
                      initial_jobs=INITIAL_JOBS, processing_time_low=5,
                      processing_time_high=15, machine_flexibility="partial",
                      eligible_machines_per_operation=3):
    positive_integer("机器数量", machine_count)
    positive_integer("工件数量", number_of_jobs)
    positive_integer("候选机器数量", eligible_machines_per_operation)
    if not 1 <= initial_jobs <= number_of_jobs:
        raise ValueError("初始工件数量必须在1与工件总量之间")
    if not 2 <= min_operations <= max_operations:
        raise ValueError("工序数量至少为2，且上限不能低于下限")
    if mean_interarrival <= 0 or processing_time_low < 1 or processing_time_high < processing_time_low:
        raise ValueError("到达间隔和加工时间范围不合法")
    if machine_flexibility not in ("partial", "full"):
        raise ValueError("柔性模式必须为partial或full")
    rng = random.Random(seed)
    arrivals = [0.0] * initial_jobs
    current = 0.0
    for _ in range(number_of_jobs - initial_jobs):
        current += rng.expovariate(1 / mean_interarrival)
        arrivals.append(round(current, 1))
    specs = []
    for job_id, arrival in enumerate(arrivals):
        operation_count = rng.randint(min_operations, max_operations)
        if fixed_inspection_index is not None:
            if not 0 <= fixed_inspection_index < operation_count - 1:
                raise ValueError("检测工序必须位于最终工序之前")
            inspection_indices = {fixed_inspection_index}
        else:
            inspection_count = min(rng.randint(min_inspections, max_inspections), operation_count - 1)
            inspection_indices = set(rng.sample(range(operation_count - 1), inspection_count))
        operations = []
        for index in range(operation_count):
            count = machine_count if machine_flexibility == "full" else min(machine_count, eligible_machines_per_operation)
            # 每道工序独立从全部机器抽取，不要求共享某个固定加工分组。
            candidates = tuple(sorted(rng.sample(range(machine_count), count)))
            processing = tuple(float(rng.randint(processing_time_low, processing_time_high)) for _ in candidates)
            data = workload = 0.0
            if index in inspection_indices:
                profile = rng.random()
                if profile < 0.20:
                    data, workload = rng.randint(18, 32), rng.randint(2, 7)
                elif profile < 0.45:
                    data, workload = rng.randint(2, 8), rng.randint(6, 16)
                elif profile < 0.80:
                    data, workload = rng.randint(8, 20), rng.randint(16, 36)
                else:
                    data, workload = rng.randint(2, 10), rng.randint(38, 70)
            operations.append(OperationSpec(candidates, processing, float(data), float(workload * inspection_workload_scale)))
        minimum_processing = sum(min(op.eligible_processing_times) for op in operations)
        due_date = arrival + 1.65 * minimum_processing + rng.uniform(8, 16)
        specs.append(JobSpec(job_id, arrival, tuple(operations), due_date))
    return tuple(specs)


def generate_formal_scenario(seed=RANDOM_SEED, mean_interarrival=4.0, **scale):
    return generate_scenario(seed=seed, mean_interarrival=mean_interarrival,
                             min_operations=3, max_operations=6, min_inspections=1,
                             max_inspections=2, fixed_inspection_index=None, **scale)


def generate_stress_scenario(seed=RANDOM_SEED, mean_interarrival=1.5,
                             inspection_workload_scale=1.5, **scale):
    return generate_scenario(seed=seed, mean_interarrival=mean_interarrival,
                             min_operations=4, max_operations=6, min_inspections=2,
                             max_inspections=3, fixed_inspection_index=None,
                             inspection_workload_scale=inspection_workload_scale, **scale)


def complete_compute_config(config):
    result = deepcopy(config)
    defaults = {"local_speed": 2.0, "local_slots": 1, "local_transfer_base": 0.2,
                "local_transfer_rate": None, "cloud_speed": 14.0, "cloud_slots": 4,
                "cloud_transfer_base": 2.5, "cloud_transfer_rate": 4.0}
    for name, value in defaults.items():
        result.setdefault(name, value)
    return result


def scalable_edge_compute_config(edge_count, machine_count, mode="scale_out",
                                  total_capacity=None, total_slots=None):
    """通信关系直接关联机器；不影响任何工序的候选机器集合。"""
    positive_integer("边缘节点数量", edge_count)
    positive_integer("机器数量", machine_count)
    if mode not in ("scale_out", "fixed_total"):
        raise ValueError("未知边缘扩容模式")
    nearby = [[] for _ in range(edge_count)]
    for machine in range(machine_count):
        nearby[min(edge_count - 1, machine * edge_count // machine_count)].append(machine)
    for index, machines in enumerate(nearby):
        if not machines:
            machines.append(index % machine_count)
    if mode == "scale_out":
        slots = [2 if index % 2 == 0 else 1 for index in range(edge_count)]
        speeds = [8.0 / count for count in slots]
    else:
        if total_capacity is None or total_capacity <= 0 or total_slots is None or total_slots < edge_count:
            raise ValueError("固定总容量模式需要足够的算力和槽位")
        base, extra = divmod(int(total_slots), edge_count)
        slots = [base + int(index < extra) for index in range(edge_count)]
        weights = [1.0 if edge_count == 1 else 0.85 + 0.3 * index / (edge_count - 1) for index in range(edge_count)]
        factor = total_capacity / sum(slot * weight for slot, weight in zip(slots, weights))
        speeds = [factor * weight for weight in weights]
    edges = []
    for index, (slot, speed, machines) in enumerate(zip(slots, speeds, nearby)):
        reliability = index / max(1, edge_count - 1)
        edges.append({"name": f"EDGE{index + 1}", "speed": speed, "slots": slot,
                      "transfer_base": 0.25, "transfer_rate": 14.0,
                      "remote_transfer_base": 2.0, "remote_transfer_rate": 7.0,
                      "near_machines": tuple(machines), "transfer_sigma": 0.18 + 0.12 * reliability,
                      "compute_sigma": 0.12 + 0.13 * reliability})
    return complete_compute_config({"edge_configs": tuple(edges)})


def formal_compute_config():
    return scalable_edge_compute_config(EDGE_COUNT, MACHINE_COUNT)


def generate_scene(seed=RANDOM_SEED, profile="stress", scenario_config=None, compute_config=None):
    config = dict(scenario_config or {})
    machine_count = positive_integer("机器数量", config.get("machine_count", MACHINE_COUNT))
    config.setdefault("initial_jobs", min(INITIAL_JOBS, config.get("number_of_jobs", NUMBER_OF_JOBS)))
    if profile not in ("stress", "formal"):
        raise ValueError("场景类型必须为stress或formal")
    generator = generate_stress_scenario if profile == "stress" else generate_formal_scenario
    jobs = generator(seed=seed, **config)
    return {"seed": seed, "profile": profile,
            "machine_flexibility": config.get("machine_flexibility", "partial"),
            "jobs": jobs, "machines": tuple({"machine_id": index} for index in range(machine_count)),
            "compute_config": complete_compute_config(compute_config) if compute_config is not None
            else scalable_edge_compute_config(EDGE_COUNT, machine_count)}


def export_scene(scene, output_dir):
    """导出场景和中间表；网络链路与候选加工机器分别保存。"""
    import csv
    import json
    from pathlib import Path
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(scene)
    payload["jobs"] = [asdict(job) for job in scene["jobs"]]
    payload["units"] = "仿真单位；计算速度为工作量/时间，带宽为数据量/时间"
    (output_dir / "scenario.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    def write_table(filename, headers, rows):
        with (output_dir / filename).open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(headers)
            writer.writerows(rows)
    write_table("operations.csv", ["工件编号", "到达时间", "交期", "工序顺序", "候选机器编号", "对应加工时间", "检测数据量", "检测计算量"],
                [[job.job_id, job.arrival, job.due_date, index + 1, json.dumps(op.eligible_machines),
                  json.dumps(op.eligible_processing_times), op.inspection_data, op.inspection_workload]
                 for job in scene["jobs"] for index, op in enumerate(job.operations)])
    write_table("machines.csv", ["机器编号"], [[m["machine_id"]] for m in scene["machines"]])
    config = scene["compute_config"]
    nodes = [[f"LOCAL-M{m['machine_id']}", "本地", config["local_speed"], config["local_slots"],
              config["local_transfer_base"], config["local_transfer_rate"]] for m in scene["machines"]]
    nodes.extend([[edge["name"], "边缘", edge["speed"], edge["slots"], edge["transfer_base"], edge["transfer_rate"]] for edge in config["edge_configs"]])
    nodes.append(["CLOUD", "云端", config["cloud_speed"], config["cloud_slots"], config["cloud_transfer_base"], config["cloud_transfer_rate"]])
    write_table("compute_nodes.csv", ["节点名称", "类型", "每槽位计算速度", "并行槽位数", "基础传输延迟", "传输速率（空值表示只计固定延迟）"], nodes)
    links = []
    for machine in scene["machines"]:
        source = machine["machine_id"]
        links.append([source, f"LOCAL-M{source}", config["local_transfer_base"], config["local_transfer_rate"]])
        for edge in config["edge_configs"]:
            near = source in edge.get("near_machines", ())
            links.append([source, edge["name"], edge["transfer_base"] if near else edge.get("remote_transfer_base", edge["transfer_base"]),
                          edge["transfer_rate"] if near else edge.get("remote_transfer_rate", edge["transfer_rate"])])
        links.append([source, "CLOUD", config["cloud_transfer_base"], config["cloud_transfer_rate"]])
    write_table("network_links.csv", ["源机器编号", "目标节点", "基础传输延迟", "传输速率"], links)
    return output_dir


if __name__ == "__main__":
    from pathlib import Path
    scene = generate_scene(seed=RANDOM_SEED, scenario_config={"machine_count": MACHINE_COUNT, "number_of_jobs": NUMBER_OF_JOBS})
    output_dir = export_scene(scene, Path(__file__).resolve().parent / "exports")
    print(f"已生成{len(scene['jobs'])}个工件、{len(scene['machines'])}台机器")
    print(f"场景数据与查看表格：{output_dir}")
