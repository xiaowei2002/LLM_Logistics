
"""受限清洗执行器测试。"""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from core.graph.cleaning_approval import (
    CleaningApprovalError,
    build_approval_proposal,
)
from core.graph.cleaning_executor import (
    execute_approved_cleaning,
)


FIXTURE = (
    Path(__file__).resolve().parent
    / "real_dirty_orders.csv"
)


class CleaningExecutorTest(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        root = Path(self.temp.name)
        self.source = root / "orders.csv"
        self.output = root / "orders_cleaned.csv"

        self.source.write_bytes(FIXTURE.read_bytes())
        self.original = self.source.read_bytes()

        self.proposal = build_approval_proposal(
            self.source
        )

        self.approval = {
            "approval_type": "cleaning_operation",
            "approval_status": "approved",
            "source_sha256": self.proposal["source_sha256"],
            "approved_operations": [
                {
                    "operation": "remove_exact_duplicates",
                    "approved_csv_lines": [3],
                }
            ],
        }

    def execute(self):
        return execute_approved_cleaning(
            self.source,
            self.output,
            self.proposal,
            self.approval,
        )

    def read_output(self):
        with self.output.open(
            encoding="utf-8-sig",
            newline="",
        ) as file:
            return list(csv.DictReader(file))

    def test_approved_duplicate_only(self):
        result = self.execute()

        self.assertEqual(result["source_rows"], 8)
        self.assertEqual(result["output_rows"], 7)
        self.assertEqual(result["deleted_rows"], 1)

        rows = self.read_output()
        ids = [r["order_no"] for r in rows]

        self.assertEqual(ids.count("SO1001"), 1)
        self.assertIn("SO1003", ids)
        self.assertIn("SO1005", ids)

    def test_negative_and_outlier_preserved(self):
        self.execute()

        rows = {
            r["order_no"]: r
            for r in self.read_output()
        }

        self.assertEqual(rows["SO1003"]["qty"], "-5")
        self.assertEqual(rows["SO1005"]["qty"], "99999")

    def test_missing_and_time_preserved(self):
        self.execute()

        rows = {
            r["order_no"]: r
            for r in self.read_output()
        }

        self.assertEqual(rows["SO1002"]["qty"], "")
        self.assertEqual(
            rows["SO1004"]["warehouse"], ""
        )
        self.assertEqual(
            rows["SO1006"]["created_at"],
            "09-03-2026",
        )

    def test_source_unchanged(self):
        self.execute()
        self.assertEqual(
            self.original, self.source.read_bytes()
        )

    def test_unapproved_row_rejected(self):
        self.approval["approved_operations"][0][
            "approved_csv_lines"
        ] = [3, 5]

        with self.assertRaises(
            CleaningApprovalError
        ):
            self.execute()

        self.assertFalse(self.output.exists())

    def test_changed_source_rejected(self):
        with self.source.open(
            "a", encoding="utf-8"
        ) as file:
            file.write(
                "SO9999,SKU-999,1,2026-09-04,WH-A\n"
            )

        with self.assertRaises(
            CleaningApprovalError
        ):
            self.execute()

        self.assertFalse(self.output.exists())

    def test_unapproved_operation_rejected(self):
        self.approval["approved_operations"][0][
            "operation"
        ] = "delete_quantity_anomalies"

        with self.assertRaises(
            CleaningApprovalError
        ):
            self.execute()

    def test_existing_output_not_overwritten(self):
        self.output.write_text(
            "existing content", encoding="utf-8"
        )

        with self.assertRaises(FileExistsError):
            self.execute()

        self.assertEqual(
            self.output.read_text(encoding="utf-8"),
            "existing content",
        )

    def test_source_as_output_rejected(self):
        with self.assertRaises(
            CleaningApprovalError
        ):
            execute_approved_cleaning(
                self.source,
                self.source,
                self.proposal,
                self.approval,
            )


if __name__ == "__main__":
    unittest.main()