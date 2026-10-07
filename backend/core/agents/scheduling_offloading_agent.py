"""Application agent for the scheduling/offloading experiment."""

from __future__ import annotations

import time
import uuid
from typing import Any

from core.tools.scheduling_offloading import run_scheduling_offloading


class SchedulingOffloadingAgent:
    """Validate a scenario, run the selected engine, and normalize its result."""

    def optimize(self, request: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        result = run_scheduling_offloading(request)
        return {
            "status": "success",
            "run_id": str(uuid.uuid4()),
            "data_source": "simulation",
            "scenario": {
                "seed": request["seed"],
                "number_of_jobs": request["number_of_jobs"],
                "number_of_work_centers": 3,
                "machines_per_work_center": 2,
                "machine_count": 6,
                "machine_flexibility": "partial3",
                "compute_nodes": ["LOCAL", "EDGE1", "EDGE2", "CLOUD"],
            },
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            **result,
        }
