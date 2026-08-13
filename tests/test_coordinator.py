"""Tests for the connection manager and the update coordinator."""

from __future__ import annotations

import decimal
import json
from unittest.mock import MagicMock, patch

from mysql.connector import errors as mysql_errors
import pytest

from custom_components.ha_mysql.coordinator import (
    DecimalEncoder,
    MySQLConnectionError,
    MySQLConnectionManager,
    MySQLQueryError,
    QueryResult,
    _convert_row,
)

DB_CONFIG = {
    "host": "db.local",
    "port": 3306,
    "username": "user",
    "password": "secret",
    "database": "testdb",
}


def _pool_returning(rows: list[dict], side_effect: Exception | None = None):
    """Build a fake connection pool that yields the given rows."""
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    if side_effect is not None:
        cursor.execute.side_effect = side_effect

    connection = MagicMock()
    connection.cursor.return_value = cursor

    pool = MagicMock()
    pool.get_connection.return_value = connection
    return pool, connection, cursor


def test_convert_row_stringifies_decimals() -> None:
    """Decimal values are converted to strings, NULL stays None."""
    row = {"amount": decimal.Decimal("10.25"), "note": None, "count": 3}
    assert _convert_row(row) == {"amount": "10.25", "note": None, "count": 3}


def test_decimal_encoder() -> None:
    """The JSON encoder renders Decimal values as strings."""
    dumped = json.dumps({"amount": decimal.Decimal("1.5")}, cls=DecimalEncoder)
    assert dumped == '{"amount": "1.5"}'


def test_query_result_row_count() -> None:
    """The row count follows the number of rows."""
    assert QueryResult().row_count == 0
    assert QueryResult(rows=[{"a": 1}, {"a": 2}]).row_count == 2


def test_execute_returns_rows() -> None:
    """A successful query returns the converted rows."""
    manager = MySQLConnectionManager(DB_CONFIG)
    pool, connection, cursor = _pool_returning([{"a": decimal.Decimal("1.5")}])

    with patch(
        "custom_components.ha_mysql.coordinator.MySQLConnectionPool",
        return_value=pool,
    ):
        assert manager.execute("SELECT 1") == [{"a": "1.5"}]

    cursor.execute.assert_called_once_with("SELECT 1")
    cursor.close.assert_called_once()
    # The connection is handed back to the pool.
    connection.close.assert_called_once()


def test_execute_pings_before_use() -> None:
    """A pooled connection is verified before the query runs."""
    manager = MySQLConnectionManager(DB_CONFIG)
    pool, connection, _ = _pool_returning([])

    with patch(
        "custom_components.ha_mysql.coordinator.MySQLConnectionPool",
        return_value=pool,
    ):
        manager.execute("SELECT 1")

    connection.ping.assert_called_once_with(reconnect=True, attempts=2, delay=1)


def test_execute_retries_after_lost_connection() -> None:
    """A dropped connection rebuilds the pool and the query succeeds."""
    manager = MySQLConnectionManager(DB_CONFIG)
    broken_pool, _, _ = _pool_returning(
        [], side_effect=mysql_errors.OperationalError("MySQL server has gone away")
    )
    healthy_pool, _, _ = _pool_returning([{"a": 1}])

    with (
        patch(
            "custom_components.ha_mysql.coordinator.MySQLConnectionPool",
            side_effect=[broken_pool, healthy_pool],
        ) as pool_factory,
        patch("custom_components.ha_mysql.coordinator.time.sleep"),
    ):
        assert manager.execute("SELECT 1") == [{"a": 1}]

    # The stale pool was discarded and a fresh one was built.
    assert pool_factory.call_count == 2


def test_execute_raises_connection_error_when_unreachable() -> None:
    """A database that stays unreachable raises MySQLConnectionError."""
    manager = MySQLConnectionManager(DB_CONFIG)

    with (
        patch(
            "custom_components.ha_mysql.coordinator.MySQLConnectionPool",
            side_effect=mysql_errors.InterfaceError("Can't connect"),
        ),
        patch("custom_components.ha_mysql.coordinator.time.sleep"),
        pytest.raises(MySQLConnectionError),
    ):
        manager.execute("SELECT 1")


def test_execute_does_not_retry_bad_query() -> None:
    """A rejected query fails immediately instead of being retried."""
    manager = MySQLConnectionManager(DB_CONFIG)
    pool, _, _ = _pool_returning(
        [], side_effect=mysql_errors.ProgrammingError("You have an error in your SQL")
    )

    with (
        patch(
            "custom_components.ha_mysql.coordinator.MySQLConnectionPool",
            return_value=pool,
        ) as pool_factory,
        pytest.raises(MySQLQueryError),
    ):
        manager.execute("SELECT nonsense")

    assert pool_factory.call_count == 1


def test_execute_uses_autocommit() -> None:
    """Autocommit is enabled so polls are not stuck on one snapshot."""
    manager = MySQLConnectionManager(DB_CONFIG)
    pool, _, _ = _pool_returning([])

    with patch(
        "custom_components.ha_mysql.coordinator.MySQLConnectionPool",
        return_value=pool,
    ) as pool_factory:
        manager.execute("SELECT 1")

    assert pool_factory.call_args.kwargs["autocommit"] is True
    assert pool_factory.call_args.kwargs["port"] == 3306
