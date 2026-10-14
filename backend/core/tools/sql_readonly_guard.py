"""
SQL V2-A: conservative read-only SQL validator.

This is a fail-closed application-level guard.
It does not replace a database account restricted
to SELECT and appropriate schema privileges.

Supported:
    Simple MySQL SELECT statements.

Intentionally unsupported:
    CTE, JOIN, subqueries, UNION, locking clauses,
    variable access, comments, multiple statements,
    arbitrary SQL functions, and administrative commands.

V3.2 compatibility:
    Logical / predicate expressions such as
    AND, OR, NOT, IS NULL, BETWEEN, IN and LIKE
    are SQL operators rather than callable SQL functions.
    Some sqlglot expression classes may still inherit
    from exp.Func, so they must not be mistaken for
    arbitrary function calls.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


class SQLReadOnlyViolation(ValueError):
    """SQL does not satisfy the read-only policy."""


MAX_SQL_LENGTH = 10_000


ALLOWED_FUNCTIONS = frozenset(
    {
        "COUNT",
        "SUM",
        "AVG",
        "MIN",
        "MAX",
    }
)


# sqlglot may represent some operators / predicates
# with classes that also fall under exp.Func.
#
# These are SQL language operators, not arbitrary
# callable functions. They are allowed here while
# genuine function calls remain fail-closed.
ALLOWED_FUNC_LIKE_OPERATOR_TYPES = tuple(
    expression_type
    for expression_type in (
        getattr(exp, "And", None),
        getattr(exp, "Or", None),
        getattr(exp, "Not", None),
        getattr(exp, "Is", None),
        getattr(exp, "Between", None),
        getattr(exp, "In", None),
        getattr(exp, "Like", None),
        getattr(exp, "ILike", None),
    )
    if expression_type is not None
)


FORBIDDEN_KEYWORDS = re.compile(
    r"\b("
    r"INSERT|UPDATE|DELETE|DROP|TRUNCATE|"
    r"ALTER|CREATE|REPLACE|MERGE|"
    r"GRANT|REVOKE|CALL|EXEC|EXECUTE|"
    r"LOAD|OUTFILE|DUMPFILE|"
    r"SLEEP|BENCHMARK"
    r")\b",
    flags=re.IGNORECASE,
)


def _validate_function_node(
    node: exp.Expression,
) -> None:
    """
    Validate one sqlglot expression that is also
    represented as exp.Func.

    Logical/predicate operators are not treated as
    callable SQL functions.

    Genuine functions remain restricted to
    ALLOWED_FUNCTIONS.
    """

    if isinstance(
        node,
        ALLOWED_FUNC_LIKE_OPERATOR_TYPES,
    ):
        return

    if not isinstance(
        node,
        exp.Func,
    ):
        return

    function_name = str(
        node.sql_name()
    ).upper()

    if function_name not in ALLOWED_FUNCTIONS:
        raise SQLReadOnlyViolation(
            "SQL 使用了未列入白名单的函数："
            + function_name
        )


def validate_readonly_sql(sql: str) -> str:
    """
    Validate a conservative subset of MySQL SELECT.

    Returns the unchanged SQL string if accepted.

    Rejection is the default for unknown syntax.
    """

    if not isinstance(
        sql,
        str,
    ):
        raise SQLReadOnlyViolation(
            "SQL 必须是字符串。"
        )

    statement = sql.strip()

    if not statement:
        raise SQLReadOnlyViolation(
            "SQL 不能为空。"
        )

    if len(statement) > MAX_SQL_LENGTH:
        raise SQLReadOnlyViolation(
            "SQL 超过最大允许长度。"
        )

    # Conservative input restrictions.
    # Some legitimate SQL text is intentionally rejected.
    if any(
        marker in statement
        for marker in (
            ";",
            "--",
            "/*",
            "*/",
            "#",
            "@",
        )
    ):
        raise SQLReadOnlyViolation(
            "当前只读策略禁止分号、注释或变量标记。"
        )

    try:
        expressions = sqlglot.parse(
            statement,
            read="mysql",
        )
    except (
        ParseError,
        ValueError,
    ) as exc:
        raise SQLReadOnlyViolation(
            "SQL 语法解析失败。"
        ) from exc

    if len(expressions) != 1:
        raise SQLReadOnlyViolation(
            "必须且只能提供一条 SQL 语句。"
        )

    tree = expressions[0]

    if not isinstance(
        tree,
        exp.Select,
    ):
        raise SQLReadOnlyViolation(
            "当前仅允许 SELECT 查询。"
        )

    # Guard against parser-normalized dangerous syntax.
    # This token filter is not the sole security mechanism.
    if FORBIDDEN_KEYWORDS.search(
        statement
    ):
        raise SQLReadOnlyViolation(
            "SQL 包含当前策略禁止的关键字。"
        )

    for node in tree.walk():
        # A second Select means a nested SELECT/subquery.
        if isinstance(
            node,
            exp.Select,
        ):
            if node is not tree:
                raise SQLReadOnlyViolation(
                    "当前策略不允许子查询。"
                )

        # Complex or sensitive syntax remains closed.
        if isinstance(
            node,
            (
                exp.With,
                exp.Join,
                exp.Union,
                exp.Subquery,
                exp.Into,
                exp.Lock,
                exp.Command,
            ),
        ):
            raise SQLReadOnlyViolation(
                "SQL 包含尚未开放的复杂或敏感结构。"
            )

        _validate_function_node(
            node
        )

    return statement