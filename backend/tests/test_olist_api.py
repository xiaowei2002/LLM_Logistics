"""Actual HTTP + actual LangGraph integration; scripted LLMs, synthetic CSVs."""

import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.olist import router, get_olist_service
from core.agents.olist_tool_caller import OlistToolCaller, ControlledToolModel
from core.graph.olist_api_service import (
    OlistAPIService,
    OlistAPISettings,
    APIProblem,
)
from core.graph.olist_structured_report import DEFAULT_PLAN
from tests import test_olist_workflow as workflow_fixture


class OlistAPITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = workflow_fixture.OlistWorkflowTest(
            "test_native_interrupt_before_disk_changes_and_resume_once"
        )
        cls.fixture.setUp()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDown()

    def setUp(self):
        f = self.fixture
        self.settings = OlistAPISettings(
            f.source,
            f.weekly,
            f.selection,
            f.root / "api_runs",
        )
        self.service = OlistAPIService(
            self.settings,
            tool_caller_factory=lambda: OlistToolCaller(
                ControlledToolModel(),
                controlled=True,
            ),
            report_generator=lambda _: json.dumps(DEFAULT_PLAN),
        )

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_olist_service] = lambda: self.service
        self.client = TestClient(app)

    def test_complete_http_chain_and_saved_result(self):
        response = self.client.post(
            "/olist/runs",
            json={
                "category_key": "a",
                "origin_week": "2018-05-21",
            },
        )
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(
            body["execution_trace"],
            ["request", "cleaning", "sql", "forecast", "report"],
        )
        self.assertEqual(
            body["forecast"]["forecasts"][0]["predicted_item_count"],
            1,
        )
        self.assertIn("1.0000", body["report"])

        result = self.client.get("/olist/runs/" + body["run_id"])
        self.assertEqual(result.json(), body)

        saved = self.settings.output_root / body["run_id"]
        self.assertEqual(
            json.loads(
                (saved / "api_result.json").read_text(encoding="utf-8")
            ),
            body,
        )

        state = json.loads(
            (saved / "api_state.json").read_text(encoding="utf-8")
        )
        self.assertTrue(state["sql_tool_call"]["arguments_validated"])
        self.assertTrue(state["forecast_tool_call"]["arguments_validated"])
        self.assertTrue(state["preparation"]["source_unchanged"])
        self.assertNotIn("source_dir", body)

    def test_missing_date_resume_and_duplicate_resume_rejected(self):
        body = self.client.post(
            "/olist/runs",
            json={"category_key": "a"},
        ).json()

        self.assertEqual(body["status"], "awaiting_input")
        self.assertFalse(
            (self.settings.output_root / body["run_id"]).exists()
        )

        path = "/olist/runs/" + body["run_id"] + "/resume"

        # Invalid date must preserve the pending interrupt.
        response = self.client.post(
            path,
            json={"origin_week": "2018-05-22"},
        )
        self.assertEqual(response.status_code, 422)

        result = self.client.post(
            path,
            json={"origin_week": "2018-05-21"},
        ).json()
        self.assertEqual(result["status"], "completed")

        response = self.client.post(
            path,
            json={"origin_week": "2018-05-21"},
        )
        self.assertEqual(response.status_code, 409)

    def test_invalid_category_date_and_client_path_do_not_allocate_run(self):
        invalid_requests = [
            {
                "category_key": "unknown",
                "origin_week": "2018-05-21",
            },
            {
                "category_key": "a",
                "origin_week": "2030-01-07",
            },
            {
                "category_key": "a",
                "origin_week": "2018-05-22",
            },
            {
                "category_key": "a",
                "source_dir": "/tmp/override",
            },
            {
                "category_key": "a",
                "origin_week": 20180521,
            },
        ]

        for body in invalid_requests:
            with self.subTest(body=body):
                response = self.client.post("/olist/runs", json=body)
                self.assertEqual(response.status_code, 422)

        self.assertEqual(len(self.service.runs), 0)

    def test_unknown_id_and_concurrent_request(self):
        response = self.client.get("/olist/runs/unknown")
        self.assertEqual(response.status_code, 404)

        with self.service.lock:
            response = self.client.post(
                "/olist/runs",
                json={"category_key": "a"},
            )
            self.assertEqual(response.status_code, 409)

    def test_invalid_planner_is_not_completed(self):
        self.service.report_generator = lambda _: "{}"

        body = self.client.post(
            "/olist/runs",
            json={
                "category_key": "a",
                "origin_week": "2018-05-21",
            },
        ).json()

        self.assertEqual(body["status"], "needs_review")
        self.assertEqual(body["report"], "")
        self.assertTrue(body["error"])

    def test_configuration_and_capacity_fail_without_llm(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(APIProblem):
                OlistAPISettings.from_env()

        with self.assertRaises(APIProblem):
            OlistAPIService(
                OlistAPISettings(
                    self.fixture.source,
                    self.fixture.weekly,
                    self.fixture.selection,
                    self.fixture.source / "outputs",
                )
            )

        self.service.max_runs = 0
        response = self.client.post(
            "/olist/runs",
            json={"category_key": "a"},
        )
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()