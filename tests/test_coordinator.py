"""Tests for the connection manager and the update coordinator."""

from __future__ import annotations

import contextlib
from datetime import timedelta
import decimal
import json
from typing import Any
from unittest.mock import MagicMock, patch

from mysql.connector import errors as mysql_errors
import pytest

from .conftest import FakePool
from custom_components.ha_mysql.const import (
    BINARY_PREVIEW_BYTES,
    CONNECT_TIMEOUT,
    POOL_ACQUIRE_INTERVAL,
    POOL_SIZE,
    READ_TIMEOUT,
    WRITE_TIMEOUT,
)
from custom_components.ha_mysql.coordinator import (
    MySQLConnectionError,
    MySQLConnectionManager,
    MySQLQueryError,
    QueryResult,
    QueryResultEncoder,
    _convert_row,
)

DB_CONFIG = {
    "host": "db.local",
    "port": 3306,
    "username": "user",
    "password": "secret",
    "database": "testdb",
}

CONNECT = "custom_components.ha_mysql.coordinator.mysql.connector.connect"
POOL = "custom_components.ha_mysql.coordinator.MySQLConnectionPool"
SLEEP = "custom_components.ha_mysql.coordinator.time.sleep"


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


def test_convert_row_decodes_text_in_binary_columns() -> None:
    """A BINARY or BLOB column holding text is returned as text."""
    row = {"note": b"hello", "raw": bytearray(b"world")}
    assert _convert_row(row) == {"note": "hello", "raw": "world"}


def test_convert_row_previews_binary_data() -> None:
    """A BLOB that is not text becomes a short hexadecimal preview."""
    assert _convert_row({"blob": b"\xff\xfe"}) == {"blob": "0xfffe"}

    long_blob = b"\xff" * (BINARY_PREVIEW_BYTES + 10)
    converted = _convert_row({"blob": long_blob})["blob"]
    assert converted == f"0x{'ff' * BINARY_PREVIEW_BYTES}..."


def test_convert_row_handles_time_and_set_columns() -> None:
    """TIME and SET columns become values that can be stored and serialised."""
    row = {"duration": timedelta(hours=1, minutes=30), "tags": {"b", "a"}}
    assert _convert_row(row) == {"duration": "1:30:00", "tags": ["a", "b"]}


def test_query_result_encoder() -> None:
    """The JSON encoder falls back to strings instead of raising."""
    dumped = json.dumps(
        {"amount": decimal.Decimal("1.5"), "raw": b"\xff", "when": timedelta(hours=2)},
        cls=QueryResultEncoder,
    )
    assert json.loads(dumped) == {
        "amount": "1.5",
        "raw": "0xff",
        "when": "2:00:00",
    }


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


@pytest.mark.parametrize(
    "failure",
    [
        mysql_errors.ProgrammingError("You have an error in your SQL"),
        mysql_errors.OperationalError("MySQL server has gone away"),
        mysql_errors.InterfaceError("Lost connection to MySQL server"),
        RuntimeError("the driver exploded"),
    ],
)
def test_execute_hands_the_connection_back_on_failure(failure: Exception) -> None:
    """A failing query never keeps a connection checked out.

    Repeating a failing query more often than the pool is large is what used
    to drain the pool, after which every later query ran into a read timeout
    instead of reaching the database.
    """
    manager = MySQLConnectionManager(DB_CONFIG)
    pool = FakePool(query_error=failure)

    with patch(POOL, return_value=pool), patch(SLEEP):
        for _ in range(POOL_SIZE * 2):
            with contextlib.suppress(Exception):
                manager.execute("SELECT 1")

    assert pool.in_use == 0
    # One query at a time never needs more than one connection.
    assert pool.peak_in_use == 1


def test_execute_hands_back_a_connection_that_fails_its_health_check() -> None:
    """A connection that fails its ping goes back to the pool as well."""
    manager = MySQLConnectionManager(DB_CONFIG)
    pool = FakePool(ping_error=mysql_errors.InterfaceError("Lost connection"))

    with (
        patch(POOL, return_value=pool),
        patch(SLEEP),
        pytest.raises(MySQLConnectionError),
    ):
        manager.execute("SELECT 1")

    assert pool.in_use == 0


def test_execute_hands_the_connection_back_after_success() -> None:
    """Repeated successful queries keep reusing the same single connection."""
    manager = MySQLConnectionManager(DB_CONFIG)
    pool = FakePool(rows=[{"a": 1}])

    with patch(POOL, return_value=pool):
        for _ in range(POOL_SIZE * 2):
            assert manager.execute("SELECT 1") == [{"a": 1}]

    assert pool.in_use == 0
    assert pool.peak_in_use == 1


def test_execute_waits_for_a_free_connection() -> None:
    """A pool that is full for a moment is waited out, not given up on."""
    manager = MySQLConnectionManager(DB_CONFIG)
    pool = FakePool(rows=[{"a": 1}])
    pool.in_use = pool.size

    waits: list[float] = []

    def release(seconds: float) -> None:
        """Let a connection come free while the wait is polled."""
        waits.append(seconds)
        pool.in_use = 0

    with patch(POOL, return_value=pool), patch(SLEEP, side_effect=release):
        assert manager.execute("SELECT 1") == [{"a": 1}]

    assert waits == [POOL_ACQUIRE_INTERVAL]
    assert pool.in_use == 0


def test_execute_reports_a_full_pool_as_a_connection_error() -> None:
    """A pool that stays full is reported as cannot_connect, and is kept.

    The connections are in use by queries that are still running, so throwing
    the pool away would pull them out from under those queries.
    """
    manager = MySQLConnectionManager(DB_CONFIG)
    pool = FakePool()
    pool.in_use = pool.size

    with (
        patch(POOL, return_value=pool) as pool_factory,
        patch("custom_components.ha_mysql.coordinator.POOL_ACQUIRE_TIMEOUT", 0),
        patch(SLEEP),
        pytest.raises(MySQLConnectionError) as caught,
    ):
        manager.execute("SELECT 1")

    assert "in use" in str(caught.value)
    assert pool_factory.call_count == 1
    assert pool.removed is False


def test_invalidate_pool_keeps_a_pool_that_was_just_rebuilt() -> None:
    """A late failure does not tear down the pool another query rebuilt.

    Every sensor shares one pool and they fail together. Without this check
    the sensor that noticed the outage second would close the connections the
    first one had just opened, and the two would keep replacing each other's
    pool until the database ran out of connections.
    """
    manager = MySQLConnectionManager(DB_CONFIG)
    stale, fresh = FakePool(), FakePool()

    with patch(POOL, side_effect=[stale, fresh]):
        assert manager._get_pool() is stale
        manager._invalidate_pool(stale)
        assert manager._get_pool() is fresh
        # The second sensor reports the pool it was using, which by now has
        # been replaced.
        manager._invalidate_pool(stale)
        assert manager._get_pool() is fresh

    assert stale.removed is True
    assert fresh.removed is False


def test_close_drops_the_current_pool() -> None:
    """Unloading the entry releases the pooled connections."""
    manager = MySQLConnectionManager(DB_CONFIG)
    pool = FakePool()

    with patch(POOL, return_value=pool):
        manager.execute("SELECT 1")
        manager.close()

    assert pool.removed is True
    assert pool.in_use == 0


def test_test_connection_uses_a_single_connection() -> None:
    """Checking the settings opens one connection and closes it again.

    Going through the pool would open POOL_SIZE connections for one SELECT 1,
    on every setup and on every submitted form.
    """
    manager = MySQLConnectionManager(DB_CONFIG)
    connection = MagicMock()

    with (
        patch(CONNECT, return_value=connection) as connect,
        patch(POOL) as pool_factory,
    ):
        manager.test_connection()

    assert connect.call_args.kwargs["database"] == "testdb"
    connection.cursor.return_value.execute.assert_called_once_with("SELECT 1")
    connection.cursor.return_value.close.assert_called_once()
    connection.close.assert_called_once()
    pool_factory.assert_not_called()


def test_test_connection_closes_after_a_refused_query() -> None:
    """A server that refuses the query still gets its connection back."""
    manager = MySQLConnectionManager(DB_CONFIG)
    connection = MagicMock()
    connection.cursor.return_value.execute.side_effect = mysql_errors.ProgrammingError(
        "Access denied", 1045
    )

    with (
        patch(CONNECT, return_value=connection),
        pytest.raises(MySQLQueryError) as caught,
    ):
        manager.test_connection()

    assert caught.value.errno == 1045
    connection.close.assert_called_once()


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (mysql_errors.InterfaceError("Can't connect"), MySQLConnectionError),
        (mysql_errors.OperationalError("Too many connections"), MySQLConnectionError),
        (mysql_errors.ConnectionTimeoutError(errno=2003), MySQLConnectionError),
        (mysql_errors.ReadTimeoutError(errno=3024), MySQLConnectionError),
        (mysql_errors.ProgrammingError("Access denied", 1045), MySQLQueryError),
    ],
)
def test_test_connection_reports_why_it_failed(
    failure: Exception, expected: type[Exception]
) -> None:
    """An unreachable server and a refused login are told apart."""
    manager = MySQLConnectionManager(DB_CONFIG)

    with patch(CONNECT, side_effect=failure), pytest.raises(expected):
        manager.test_connection()


@pytest.mark.parametrize(
    "failure",
    [
        mysql_errors.ConnectionTimeoutError(errno=2003),
        mysql_errors.ReadTimeoutError(errno=3024),
        mysql_errors.WriteTimeoutError(errno=3024),
    ],
)
def test_execute_treats_a_timeout_as_a_connection_problem(failure: Exception) -> None:
    """A driver timeout is a broken connection, not a rejected query.

    These three errors derive straight from Error, so unless they are named
    they land on the branch for a query the server refused: the entry then
    fails for good instead of retrying, and the pool keeps handing out
    connections that will never answer again.
    """
    manager = MySQLConnectionManager(DB_CONFIG)
    pool = FakePool(query_error=failure)

    with (
        patch(POOL, return_value=pool),
        patch(SLEEP),
        pytest.raises(MySQLConnectionError),
    ):
        manager.execute("SELECT 1")

    assert pool.in_use == 0
    # The pool was rebuilt rather than kept, so the dead connections are gone.
    assert pool.removed is True


def test_connections_bound_their_reads_and_writes() -> None:
    """Every read and write is bounded, not only opening the connection.

    The driver drops the connect timeout once the handshake is done, so
    without this a read that never gets an answer holds on to its pooled
    connection for good.
    """
    manager = MySQLConnectionManager(DB_CONFIG)
    pool = FakePool()

    with patch(POOL, return_value=pool) as pool_factory:
        manager.execute("SELECT 1")

    kwargs = pool_factory.call_args.kwargs
    assert kwargs["connection_timeout"] == CONNECT_TIMEOUT
    assert kwargs["read_timeout"] == READ_TIMEOUT
    assert kwargs["write_timeout"] == WRITE_TIMEOUT


def test_connections_use_tls_when_the_server_offers_it() -> None:
    """TLS is used when it is available, without demanding a certificate.

    A database on a home network nearly always has a self signed certificate,
    so verifying it would lock out every existing setup. The settings are
    spelled out so upgrading the driver cannot quietly change them.
    """
    manager = MySQLConnectionManager(DB_CONFIG)
    pool = FakePool()

    with patch(POOL, return_value=pool) as pool_factory:
        manager.execute("SELECT 1")

    with patch(CONNECT) as connect:
        manager.test_connection()

    for kwargs in (pool_factory.call_args.kwargs, connect.call_args.kwargs):
        assert kwargs["ssl_disabled"] is False
        assert kwargs["ssl_verify_cert"] is False
        assert kwargs["ssl_verify_identity"] is False


def test_execute_survives_a_pool_that_cannot_be_built() -> None:
    """A pool that fails to open is reported instead of leaving a broken one."""
    manager = MySQLConnectionManager(DB_CONFIG)
    calls: list[Any] = []

    def build(**kwargs: Any) -> FakePool:
        calls.append(kwargs)
        raise mysql_errors.InterfaceError("Can't connect")

    with (
        patch(POOL, side_effect=build),
        patch(SLEEP),
        pytest.raises(MySQLConnectionError),
    ):
        manager.execute("SELECT 1")

    # Every attempt starts from scratch instead of reusing a half-built pool.
    assert len(calls) == 2
