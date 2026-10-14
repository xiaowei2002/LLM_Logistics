"""
Regression tests for SQL read-only guard V3.2.

Purpose:
    Verify that legitimate logical/predicate expressions
    are not misclassified as arbitrary SQL functions.

No LLM, MCP server, or MySQL connection is used.

Run:
    python -m tests.test_sql_readonly_guard_v32
"""

from __future__ import annotations

import unittest

from core.tools.sql_readonly_guard import (
    SQLReadOnlyViolation,
    validate_readonly_sql,
)


class SQLReadOnlyGuardV32Test(
    unittest.TestCase
):

    def assert_allowed(
        self,
        sql: str,
    ) -> None:
        self.assertEqual(
            validate_readonly_sql(sql),
            sql.strip(),
        )

    def assert_blocked(
        self,
        sql: str,
    ) -> None:
        with self.assertRaises(
            SQLReadOnlyViolation
        ):
            validate_readonly_sql(sql)

    def test_sv321_and_date_range_allowed(
        self,
    ):
        self.assert_allowed(
            "SELECT COUNT(*) AS order_count "
            "FROM orders "
            "WHERE order_purchase_timestamp "
            ">= '2018-08-01 00:00:00' "
            "AND order_purchase_timestamp "
            "< '2018-08-02 00:00:00'"
        )

    def test_sv322_and_multiple_filters_allowed(
        self,
    ):
        self.assert_allowed(
            "SELECT COUNT(*) "
            "FROM orders "
            "WHERE order_status = 'delivered' "
            "AND order_purchase_timestamp "
            ">= '2018-01-01 00:00:00'"
        )

    def test_sv323_or_allowed(
        self,
    ):
        self.assert_allowed(
            "SELECT order_id "
            "FROM orders "
            "WHERE order_status = 'created' "
            "OR order_status = 'approved'"
        )

    def test_sv324_is_null_allowed(
        self,
    ):
        self.assert_allowed(
            "SELECT COUNT(*) "
            "FROM orders "
            "WHERE order_delivered_customer_date "
            "IS NULL"
        )

    def test_sv325_is_not_null_allowed(
        self,
    ):
        self.assert_allowed(
            "SELECT COUNT(*) "
            "FROM orders "
            "WHERE order_delivered_customer_date "
            "IS NOT NULL"
        )

    def test_sv326_between_allowed(
        self,
    ):
        self.assert_allowed(
            "SELECT order_id "
            "FROM order_items "
            "WHERE price BETWEEN 100 AND 500"
        )

    def test_sv327_in_literal_list_allowed(
        self,
    ):
        self.assert_allowed(
            "SELECT order_id "
            "FROM orders "
            "WHERE order_status IN "
            "('delivered', 'shipped')"
        )

    def test_sv328_like_allowed(
        self,
    ):
        self.assert_allowed(
            "SELECT customer_id "
            "FROM customers "
            "WHERE customer_city LIKE 'sao%'"
        )

    def test_sv329_allowed_aggregate_with_and(
        self,
    ):
        self.assert_allowed(
            "SELECT AVG(freight_value) "
            "FROM order_items "
            "WHERE price >= 100 "
            "AND freight_value >= 0"
        )

    def test_sv330_unknown_function_still_blocked(
        self,
    ):
        self.assert_blocked(
            "SELECT ABS(price) "
            "FROM order_items"
        )

    def test_sv331_sleep_still_blocked(
        self,
    ):
        self.assert_blocked(
            "SELECT SLEEP(1)"
        )

    def test_sv332_subquery_still_blocked(
        self,
    ):
        self.assert_blocked(
            "SELECT order_id "
            "FROM orders "
            "WHERE customer_id IN "
            "(SELECT customer_id "
            "FROM customers)"
        )

    def test_sv333_join_still_blocked(
        self,
    ):
        self.assert_blocked(
            "SELECT orders.order_id "
            "FROM orders "
            "JOIN customers "
            "ON orders.customer_id "
            "= customers.customer_id"
        )

    def test_sv334_update_still_blocked(
        self,
    ):
        self.assert_blocked(
            "UPDATE orders "
            "SET order_status = 'x'"
        )

    def test_sv335_delete_still_blocked(
        self,
    ):
        self.assert_blocked(
            "DELETE FROM orders"
        )


if __name__ == "__main__":
    unittest.main()