
"""
Cleaning 操作审批验证测试。

运行：
python -m tests.test_cleaning_approval
"""

from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from core.graph.cleaning_approval import (
    CleaningApprovalError,
    build_approval_proposal,
    calculate_file_sha256,
    validate_cleaning_approval,
)


SOURCE = (
    Path(__file__).resolve().parent
    / "real_dirty_orders.csv"
)


class CleaningApprovalTest(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.source = (
            Path(self.temp.name) / "orders.csv"
        )
        self.source.write_bytes(SOURCE.read_bytes())

        self.proposal = build_approval_proposal(
            self.source
        )

        self.approval = {
            "approval_type": "cleaning_operation",
            "approval_status": "approved",
            "source_sha256": (
                self.proposal["source_sha256"]
            ),
            "approved_operations": [
                {
                    "operation": "remove_exact_duplicates",
                    "approved_csv_lines": [3],
                }
            ],
        }

    def validate(self):
        return validate_cleaning_approval(
            self.source,
            self.proposal,
            self.approval,
        )

    def test_hash_is_stable(self):
        self.assertEqual(
            calculate_file_sha256(self.source),
            self.proposal["source_sha256"],
        )

    def test_duplicate_candidate_is_correct(self):
        candidate = self.proposal[
            "allowed_candidate_operations"
        ][0]

        self.assertEqual(
            candidate["candidate_csv_lines"], [3]
        )

    def test_valid_approval(self):
        result = self.validate()

        self.assertTrue(result["valid"])
        self.assertEqual(
            result["approved_csv_lines"], [3]
        )

    def test_unapproved_rejected(self):
        self.approval["approval_status"] = "pending"

        with self.assertRaises(CleaningApprovalError):
            self.validate()

    def test_file_modified_rejected(self):
        with self.source.open(
            "a", encoding="utf-8"
        ) as file:
            file.write(
                "SO9999,SKU-999,1,2026-09-01,WH-A\n"
            )

        with self.assertRaises(CleaningApprovalError):
            self.validate()

    def test_unapproved_row_rejected(self):
        self.approval["approved_operations"][0][
            "approved_csv_lines"
        ] = [3, 5]

        with self.assertRaises(CleaningApprovalError):
            self.validate()

    def test_anomaly_deletion_rejected(self):
        self.approval["approved_operations"][0][
            "operation"
        ] = "delete_quantity_anomalies"

        with self.assertRaises(CleaningApprovalError):
            self.validate()

    def test_duplicate_line_numbers_rejected(self):
        self.approval["approved_operations"][0][
            "approved_csv_lines"
        ] = [3, 3]

        with self.assertRaises(CleaningApprovalError):
            self.validate()

    def test_forged_proposal_rejected(self):
        forged = deepcopy(self.proposal)
        forged["allowed_candidate_operations"][0][
            "candidate_csv_lines"
        ] = [3, 5]

        with self.assertRaises(CleaningApprovalError):
            validate_cleaning_approval(
                self.source,
                forged,
                self.approval,
            )

    def test_extra_operation_field_rejected(self):
        self.approval["approved_operations"][0][
            "delete_anomalies"
        ] = True

        with self.assertRaises(CleaningApprovalError):
            self.validate()

    def test_source_file_unchanged(self):
        before = self.source.read_bytes()

        self.validate()

        self.assertEqual(
            before, self.source.read_bytes()
        )


if __name__ == "__main__":
    unittest.main()
