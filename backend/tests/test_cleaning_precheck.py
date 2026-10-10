
"""
Cleaning Precheck 单元测试。

运行：
python -m tests.test_cleaning_precheck
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.graph.cleaning_precheck import inspect_cleaning_risks


TEST_DATA = (
    Path(__file__).resolve().parent
    / "real_dirty_orders.csv"
)


class CleaningPrecheckTest(unittest.TestCase):

    def setUp(self):
        self.result = inspect_cleaning_risks(TEST_DATA)

    def test_source_row_count(self):
        self.assertEqual(
            self.result["source_rows"], 8
        )

    def test_duplicate_detected(self):
        duplicates = [
            r for r in self.result["risks"]
            if r["type"] == "exact_duplicate"
        ]

        self.assertEqual(len(duplicates), 1)
        self.assertEqual(
            duplicates[0]["records"][0]["order_no"],
            "SO1001",
        )

    def test_negative_quantity_retained(self):
        negative = [
            r for r in self.result["risks"]
            if r["type"] == "negative_quantity"
        ]

        self.assertTrue(any(
            r["order_no"] == "SO1003"
            and r["suggested_action"] == "retain_and_flag"
            for r in negative
        ))

    def test_outlier_retained(self):
        outliers = [
            r for r in self.result["risks"]
            if r["type"] == "statistical_outlier"
        ]

        self.assertTrue(any(
            r["order_no"] == "SO1005"
            and r["value"] == 99999
            for r in outliers
        ))

        self.assertTrue(all(
            r["suggested_action"] == "retain_and_flag"
            for r in outliers
        ))

    def test_missing_fields(self):
        risks = self.result["risks"]

        self.assertTrue(any(
            r["type"] == "missing_quantity"
            and r["order_no"] == "SO1002"
            for r in risks
        ))

        self.assertTrue(any(
            r["type"] == "missing_warehouse"
            and r["order_no"] == "SO1004"
            for r in risks
        ))

    def test_ambiguous_date(self):
        self.assertTrue(any(
            r["type"] == "ambiguous_date_format"
            and r["order_no"] == "SO1006"
            for r in self.result["risks"]
        ))

    def test_approval_required(self):
        self.assertTrue(
            self.result["requires_human_input"]
        )
        self.assertFalse(
            self.result["execution_allowed"]
        )
        self.assertFalse(
            self.result["deletion_approved"]
        )

    def test_read_only(self):
        before = TEST_DATA.read_bytes()

        inspect_cleaning_risks(TEST_DATA)

        after = TEST_DATA.read_bytes()

        self.assertEqual(before, after)

    def test_missing_file_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.csv"

            with self.assertRaises(FileNotFoundError):
                inspect_cleaning_risks(missing)


if __name__ == "__main__":
    unittest.main()
