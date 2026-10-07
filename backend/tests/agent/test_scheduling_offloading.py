from core.agents.scheduling_offloading_agent import SchedulingOffloadingAgent


def test_demo_engine_returns_normalized_result():
    result = SchedulingOffloadingAgent().optimize({
        "engine": "demo",
        "algorithm": "midterm",
        "seed": 41055,
        "number_of_jobs": 4,
        "population_size": 2,
        "generations": 0,
        "decision_horizon": 2,
        "time_horizon": 10.0,
    })
    assert result["status"] == "success"
    assert result["data_source"] == "simulation"
    assert result["is_demo"] is True
    assert result["metrics"]["makespan"] > 0
    assert result["decision_summary"]["by_kind"]["ROUTING"] > 0
    assert result["scenario"]["compute_nodes"] == [
        "LOCAL", "EDGE1", "EDGE2", "CLOUD"
    ]
