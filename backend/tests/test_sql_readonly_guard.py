
"""
Unit tests for the SQL V2-A read-only guard.

No LLM, MCP server, or MySQL connection is used.

Run:
    python -m tests.test_sql_readonly_guard
"""

import unittest

from core.tools.sql_readonly_guard import (
    SQLReadOnlyViolation,
    validate_readonly_sql,
)


class SQLReadOnlyGuardTest(unittest.TestCase):

    def assert_allowed(self, sql: str) -> None:
        self.assertEqual(
            validate_readonly_sql(sql),
            sql.strip(),
        )

    def assert_blocked(self, sql: str) -> None:
        with self.assertRaises(
            SQLReadOnlyViolation
        ):
            validate_readonly_sql(sql)

    def test_sv201_plain_select(self):
        self.assert_allowed(
            "SELECT sku, quantity FROM inventory"
        )

    def test_sv202_filtered_select(self):
        self.assert_allowed(
            "SELECT quantity FROM inventory "
            "WHERE sku = 'SKU-001' LIMIT 10"
        )

    def test_sv203_aggregate_select(self):
        self.assert_allowed(
            "SELECT sku, SUM(quantity) "
            "FROM inventory GROUP BY sku"
        )

    def test_sv204_insert_blocked(self):
        self.assert_blocked(
            "INSERT INTO inventory(sku) VALUES ('A')"
        )

    def test_sv205_update_blocked(self):
        self.assert_blocked(
            "UPDATE inventory SET quantity = 99"
        )

    def test_sv206_delete_blocked(self):
        self.assert_blocked(
            "DELETE FROM inventory"
        )

    def test_sv207_drop_blocked(self):
        self.assert_blocked(
            "DROP TABLE inventory"
        )

    def test_sv208_multi_statement_blocked(self):
        self.assert_blocked(
            "SELECT * FROM inventory; "
            "DELETE FROM inventory"
        )

    def test_sv209_cte_blocked(self):
        self.assert_blocked(
            "WITH x AS (SELECT * FROM inventory) "
            "SELECT * FROM x"
        )

    def test_sv210_union_blocked(self):
        self.assert_blocked(
            "SELECT sku FROM inventory "
            "UNION SELECT sku FROM orders"
        )

    def test_sv211_show_blocked(self):
        self.assert_blocked(
            "SHOW TABLES"
        )

    def test_sv212_comment_blocked(self):
        self.assert_blocked(
            "SELECT * FROM inventory -- test"
        )

    def test_sv213_variable_blocked(self):
        self.assert_blocked(
            "SELECT @variable"
        )

    def test_sv214_into_outfile_blocked(self):
        self.assert_blocked(
            "SELECT * FROM inventory "
            "INTO OUTFILE '/tmp/export.csv'"
        )

    def test_sv215_function_blocked(self):
        self.assert_blocked(
            "SELECT SLEEP(1)"
        )

    def test_sv216_empty_blocked(self):
        self.assert_blocked(
            ""
        )

    def test_sv217_non_string_blocked(self):
        self.assert_blocked(
            None
        )

    def test_sv218_subquery_blocked(self):
        self.assert_blocked(
            "SELECT sku FROM inventory "
            "WHERE sku IN "
            "(SELECT sku FROM orders)"
        )


if __name__ == "__main__":
    unittest.main()