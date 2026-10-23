"""Tests for the connection manager and the update coordinator."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import decimal
import json
import ssl
from typing import Any
from unittest.mock import AsyncMock, patch

from pymysql import err as pymysql_err
import pytest

from homeassistant.util.ssl import client_context_no_verify

from .conftest import ROWS, FakeConnection, FakeCursor, FakePool
from custom_components.ha_mysql.const import (
    BINARY_PREVIEW_BYTES,
    CONNECT_TIMEOUT,
    POOL_MAX_SIZE,
    POOL_MIN_SIZE,
    POOL_RECYCLE_SECONDS,
)
from custom_components.ha_mysql.coordinator import (
    _CONNECTIVITY_ERRNOS,
    READ_ONLY_COMMAND,
    MySQLConnectionError,
    MySQLConnectionManager,
    MySQLQueryError,
    QueryResult,
    QueryResultEncoder,
    TLSUnavailableError,
    _async_verify_tls,
    _convert_row,
    _wrap_error,
    classify_connect_error,
)

DB_CONFIG = {
    "host": "db.local",
    "port": 3306,
    "username": "user",
    "password": "secret",
    "database": "testdb",
}

CREATE_POOL = "aiomysql.create_pool"
CONNECT = "aiomysql.connect"


def _manager(**overrides: Any) -> MySQLConnectionManager:
    """Return a connection manager for the test database."""
    return MySQLConnectionManager({**DB_CONFIG, **overrides})


def _cursor(**kwargs: Any) -> FakeCursor:
    """Return a cursor reporting the standard two-row result set."""
    kwargs.setdefault("rows", [dict(row) for row in ROWS[:1]])
    return FakeCursor(**kwargs)


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


async def test_execute_returns_rows() -> None:
    """A successful query returns the converted rows."""
    manager = _manager()
    pool = FakePool(FakeConnection(_cursor(rows=[{"a": decimal.Decimal("1.5")}])))

    with patch(CREATE_POOL, AsyncMock(return_value=pool)):
        assert await manager.execute("SELECT 1") == [{"a": "1.5"}]

    assert pool.connection.cursor_obj.executed == ["SELECT 1"]
    assert pool.acquired == 1
    assert pool.released == 1


async def test_execute_binds_params() -> None:
    """Values passed as params are handed to the driver, not the query text.

    This is what keeps a bound value from being able to change what the
    statement does: the driver quotes and escapes it instead of it becoming
    part of the SQL.
    """
    manager = _manager()
    pool = FakePool(FakeConnection(_cursor(rows=[])))

    with patch(CREATE_POOL, AsyncMock(return_value=pool)):
        await manager.execute("SELECT * FROM emp WHERE id = %s", ("1; DROP TABLE emp",))

    assert pool.connection.cursor_obj.executed_args == [("1; DROP TABLE emp",)]


async def test_execute_pings_before_use() -> None:
    """A pooled connection is verified before the query runs."""
    manager = _manager()
    connection = FakeConnection(_cursor(rows=[]))
    pool = FakePool(connection)

    with patch(CREATE_POOL, AsyncMock(return_value=pool)):
        await manager.execute("SELECT 1")

    assert connection.pings == 1


async def test_execute_builds_the_pool_once() -> None:
    """Repeated calls reuse the pool instead of opening a new one each time."""
    manager = _manager()
    pool = FakePool(FakeConnection(_cursor(rows=[])))
    create_pool = AsyncMock(return_value=pool)

    with patch(CREATE_POOL, create_pool):
        for _ in range(3):
            await manager.execute("SELECT 1")

    create_pool.assert_called_once()
    assert pool.acquired == 3
    assert pool.released == 3


async def test_pool_is_created_with_the_configured_settings() -> None:
    """The pool is bounded, recycling, and built from the stored settings."""
    manager = _manager()
    create_pool = AsyncMock(return_value=FakePool(FakeConnection(_cursor(rows=[]))))

    with patch(CREATE_POOL, create_pool):
        await manager.execute("SELECT 1")

    kwargs = create_pool.call_args.kwargs
    assert kwargs["host"] == "db.local"
    assert kwargs["port"] == 3306
    assert kwargs["user"] == "user"
    assert kwargs["password"] == "secret"
    assert kwargs["db"] == "testdb"
    assert kwargs["connect_timeout"] == CONNECT_TIMEOUT
    assert kwargs["autocommit"] is True
    assert kwargs["minsize"] == POOL_MIN_SIZE
    assert kwargs["maxsize"] == POOL_MAX_SIZE
    assert kwargs["pool_recycle"] == POOL_RECYCLE_SECONDS
    assert "ssl" not in kwargs


def test_the_session_is_set_to_read_only() -> None:
    """Every connection is made read only, whatever builds it.

    The driver runs init_command after each login, also the one it makes when
    the server dropped an idle connection, so the pool never hands out a
    session that can write. Measured against MariaDB 10.11: with it, an
    INSERT, an UPDATE, a DELETE and a temporary table all fail with error
    1792; the SET that would lift it is refused by the query check.
    """
    assert READ_ONLY_COMMAND == "SET SESSION TRANSACTION READ ONLY"
    assert _manager()._connect_kwargs["init_command"] == READ_ONLY_COMMAND
    assert _manager(use_tls=True)._connect_kwargs["init_command"] == READ_ONLY_COMMAND


async def test_the_pool_and_the_test_connection_both_ask_for_read_only() -> None:
    """The pool and the one-off test connection share the read only login."""
    manager = _manager()
    create_pool = AsyncMock(return_value=FakePool(FakeConnection(_cursor(rows=[]))))
    connection = FakeConnection(_cursor(rows=[]))

    with (
        patch(CREATE_POOL, create_pool),
        patch(CONNECT, AsyncMock(return_value=connection)) as connect,
    ):
        await manager.execute("SELECT 1")
        await manager.test_connection()

    assert create_pool.call_args.kwargs["init_command"] == READ_ONLY_COMMAND
    assert connect.call_args.kwargs["init_command"] == READ_ONLY_COMMAND

    await manager.close()


@pytest.mark.parametrize(
    "errno",
    sorted(_CONNECTIVITY_ERRNOS),
)
async def test_execute_classifies_connectivity_errors(errno: int) -> None:
    """A dropped socket, a timeout or too many connections is a connection error.

    The connection is closed rather than reused: it may still have a query in
    flight on the wire, and a corrupted pooled connection would let the next
    query read another statement's leftovers. It still goes back through
    release(), which is what frees its slot in the pool.
    """
    manager = _manager()
    connection = FakeConnection(
        _cursor(error=pymysql_err.OperationalError(errno, "connection trouble"))
    )
    pool = FakePool(connection)

    with (
        patch(CREATE_POOL, AsyncMock(return_value=pool)),
        pytest.raises(MySQLConnectionError) as caught,
    ):
        await manager.execute("SELECT 1")

    assert caught.value.errno == errno
    assert connection.closed is True
    assert pool.released == 1


@pytest.mark.parametrize("errno", [1045, 1044, 1049, 1064, 1146])
async def test_execute_classifies_other_errors_as_query_errors(errno: int) -> None:
    """A rejected login, a missing database or bad SQL is a query error.

    PyMySQL raises the very same OperationalError for these as it does for a
    dropped socket, so the error code is what decides, not the exception type.
    """
    manager = _manager()
    connection = FakeConnection(
        _cursor(error=pymysql_err.OperationalError(errno, "refused"))
    )
    pool = FakePool(connection)

    with (
        patch(CREATE_POOL, AsyncMock(return_value=pool)),
        pytest.raises(MySQLQueryError) as caught,
    ):
        await manager.execute("SELECT 1")

    assert caught.value.errno == errno
    assert connection.closed is True
    assert pool.released == 1


async def test_execute_treats_a_bare_oserror_as_connectivity() -> None:
    """A raw socket error, without a MySQL error code, is a connection error."""
    manager = _manager()
    connection = FakeConnection(_cursor(error=OSError("network unreachable")))
    pool = FakePool(connection)

    with (
        patch(CREATE_POOL, AsyncMock(return_value=pool)),
        pytest.raises(MySQLConnectionError),
    ):
        await manager.execute("SELECT 1")

    assert connection.closed is True


async def test_execute_reports_a_client_side_error_without_errno() -> None:
    """An error with only a message, no code, is treated as connectivity."""
    manager = _manager()
    connection = FakeConnection(_cursor(error=pymysql_err.InterfaceError("(0, '')")))
    pool = FakePool(connection)

    with (
        patch(CREATE_POOL, AsyncMock(return_value=pool)),
        pytest.raises(MySQLConnectionError),
    ):
        await manager.execute("SELECT 1")


async def test_execute_times_out_and_drops_the_connection() -> None:
    """A query that never answers gives up instead of holding the pool hostage.

    This is what a single asyncio.timeout() around the whole call replaces the
    old read/write timeouts with: nothing here can block forever, and the
    connection is closed rather than reused in an unknown state. It is still
    released, so its slot in the pool is freed.
    """
    manager = _manager()
    connection = FakeConnection()
    pool = FakePool(connection)

    async def hang(*args: Any, **kwargs: Any) -> None:
        await asyncio.sleep(3600)

    connection.cursor_obj.execute = hang  # type: ignore[method-assign]

    with (
        patch(CREATE_POOL, AsyncMock(return_value=pool)),
        patch("custom_components.ha_mysql.coordinator.QUERY_TIMEOUT", 0),
        pytest.raises(MySQLConnectionError, match="Timed out"),
    ):
        await manager.execute("SELECT 1")

    assert connection.closed is True
    assert pool.released == 1


async def test_execute_returns_every_connection_across_repeated_failures() -> None:
    """Failing queries do not drain the pool one connection at a time.

    A failed query closes its connection, but only release() frees its slot
    in the pool. Without it the pool is exhausted after POOL_MAX_SIZE
    failures, and every later query times out waiting for a connection.
    """
    manager = _manager()
    pool = FakePool(
        FakeConnection(
            _cursor(error=pymysql_err.OperationalError(1054, "Unknown column"))
        )
    )

    with patch(CREATE_POOL, AsyncMock(return_value=pool)):
        for _ in range(POOL_MAX_SIZE + 1):
            with pytest.raises(MySQLQueryError):
                await manager.execute("SELECT nope FROM emp")

    assert pool.acquired == POOL_MAX_SIZE + 1
    assert pool.released == pool.acquired


async def test_execute_returns_a_connection_that_failed_its_ping() -> None:
    """A connection that cannot reconnect is closed and released as well."""
    manager = _manager()
    connection = FakeConnection(
        ping_error=pymysql_err.OperationalError(2003, "Can't connect")
    )
    pool = FakePool(connection)

    with (
        patch(CREATE_POOL, AsyncMock(return_value=pool)),
        pytest.raises(MySQLConnectionError),
    ):
        await manager.execute("SELECT 1")

    assert connection.closed is True
    assert pool.released == 1


async def test_execute_drops_and_returns_a_cancelled_query() -> None:
    """A query that is cancelled midway gives its connection back, closed.

    A cancellation is not an error the query handlers catch, so it has to be
    the finally block that closes the connection, with its query still in
    flight, and frees its slot.
    """
    manager = _manager()
    connection = FakeConnection()
    pool = FakePool(connection)
    started = asyncio.Event()

    async def hang(*args: Any, **kwargs: Any) -> None:
        started.set()
        await asyncio.sleep(3600)

    connection.cursor_obj.execute = hang  # type: ignore[method-assign]

    with patch(CREATE_POOL, AsyncMock(return_value=pool)):
        task = asyncio.create_task(manager.execute("SELECT 1"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert connection.closed is True
    assert pool.released == 1


async def test_execute_survives_a_pool_that_cannot_be_built() -> None:
    """A pool that fails to open is reported instead of leaving a broken one."""
    manager = _manager()

    with (
        patch(
            CREATE_POOL,
            AsyncMock(side_effect=pymysql_err.OperationalError(2003, "Can't connect")),
        ),
        pytest.raises(MySQLConnectionError),
    ):
        await manager.execute("SELECT 1")


async def test_test_connection_uses_a_single_connection() -> None:
    """Checking the settings opens one connection and closes it again.

    Going through the pool would open POOL_MAX_SIZE connections for one
    SELECT 1, on every setup and on every submitted form.
    """
    manager = _manager()
    connection = FakeConnection(_cursor(rows=[]))
    create_pool = AsyncMock()

    with (
        patch(CONNECT, AsyncMock(return_value=connection)) as connect,
        patch(CREATE_POOL, create_pool),
    ):
        await manager.test_connection()

    assert connect.call_args.kwargs["db"] == "testdb"
    assert connection.cursor_obj.executed == ["SELECT 1"]
    assert connection.ensure_closed_called is True
    create_pool.assert_not_called()


async def test_test_connection_closes_after_a_refused_query() -> None:
    """A server that refuses the query still gets its connection closed."""
    manager = _manager()
    connection = FakeConnection(
        _cursor(error=pymysql_err.ProgrammingError(1064, "Syntax error"))
    )

    with (
        patch(CONNECT, AsyncMock(return_value=connection)),
        pytest.raises(MySQLQueryError) as caught,
    ):
        await manager.test_connection()

    assert caught.value.errno == 1064
    assert connection.ensure_closed_called is True


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (pymysql_err.OperationalError(2003, "Can't connect"), MySQLConnectionError),
        (
            pymysql_err.OperationalError(1040, "Too many connections"),
            MySQLConnectionError,
        ),
        (pymysql_err.OperationalError(1045, "Access denied"), MySQLQueryError),
        (pymysql_err.OperationalError(1049, "Unknown database"), MySQLQueryError),
        (pymysql_err.ProgrammingError(1064, "Syntax error"), MySQLQueryError),
    ],
)
async def test_test_connection_reports_why_it_failed(
    failure: Exception, expected: type[Exception]
) -> None:
    """An unreachable server and a refused login are told apart."""
    manager = _manager()

    with patch(CONNECT, AsyncMock(side_effect=failure)), pytest.raises(expected):
        await manager.test_connection()


async def test_test_connection_times_out() -> None:
    """A server that never answers the handshake is reported, not hung on."""
    manager = _manager()

    async def hang(**kwargs: Any) -> None:
        await asyncio.sleep(3600)

    with (
        patch(CONNECT, hang),
        patch("custom_components.ha_mysql.coordinator.QUERY_TIMEOUT", 0),
        pytest.raises(MySQLConnectionError, match="Timed out"),
    ):
        await manager.test_connection()


async def test_close_drops_the_current_pool() -> None:
    """Unloading the entry closes the pooled connections, busy ones included.

    terminate() closes the connections a running query still holds as well,
    so unloading never waits for a query on a database that stopped
    answering.
    """
    manager = _manager()
    pool = FakePool(FakeConnection(_cursor(rows=[])))

    with patch(CREATE_POOL, AsyncMock(return_value=pool)):
        await manager.execute("SELECT 1")
        await manager.close()

    assert pool.terminated is True
    assert pool.wait_closed_called is True


async def test_close_without_a_pool_is_a_noop() -> None:
    """Closing a manager that never built a pool does nothing."""
    manager = _manager()
    await manager.close()


def test_tls_off_by_default() -> None:
    """Without the option no context is built, so the driver never offers TLS."""
    manager = _manager()
    assert "ssl" not in manager._connect_kwargs


def test_tls_builds_an_unverified_context() -> None:
    """Turning the option on hands the driver a context that does not verify.

    A database on a home network nearly always has a self signed certificate,
    so verification would make the option unusable. The context is the one
    Home Assistant caches, so the CA bundle is not loaded on the event loop.
    """
    manager = _manager(use_tls=True)
    context = manager._connect_kwargs["ssl"]

    assert context is client_context_no_verify()
    assert context.check_hostname is False
    assert context.verify_mode is ssl.CERT_NONE


@pytest.mark.parametrize(
    ("error", "failure"),
    [
        (pymysql_err.OperationalError(2003, "Can't connect"), "cannot_connect"),
        (pymysql_err.OperationalError(1203, "max_user_connections"), "cannot_connect"),
        (OSError("network unreachable"), "cannot_connect"),
        (pymysql_err.OperationalError(1045, "Access denied"), "invalid_auth"),
        (pymysql_err.OperationalError(1044, "Access denied to db"), "unknown_database"),
        (pymysql_err.OperationalError(1049, "Unknown database"), "unknown_database"),
        (pymysql_err.OperationalError(1130, "Host not allowed"), "connection_refused"),
        (pymysql_err.OperationalError(3118, "Account is locked"), "connection_refused"),
    ],
)
def test_classify_connect_error(error: Exception, failure: str) -> None:
    """Setup and the forms give the same advice for the same answer."""
    assert classify_connect_error(_wrap_error(error, "db.local:3306/testdb")) == (
        failure
    )


def test_wrapped_errors_keep_the_driver_message_apart() -> None:
    """The driver message is kept without the context put in front of it."""
    error = _wrap_error(
        pymysql_err.OperationalError(1146, "Table 'testdb.x' doesn't exist"),
        "db.local:3306/testdb",
    )

    assert str(error) == "Query failed: Table 'testdb.x' doesn't exist"
    assert error.detail == "Table 'testdb.x' doesn't exist"
    assert error.errno == 1146


@pytest.mark.parametrize(
    ("failure", "refused"),
    [
        (pymysql_err.OperationalError(1045, "Access denied"), True),
        (pymysql_err.OperationalError(2003, "Can't connect"), False),
        (pymysql_err.OperationalError(1049, "Unknown database"), False),
        (None, False),
    ],
)
async def test_credentials_refused_needs_a_refused_fresh_login(
    failure: Exception | None, refused: bool
) -> None:
    """Only a new login that is refused counts as refused credentials.

    Error 1045 on a query can also mean a missing FILE privilege, and a login
    that fails for another reason says nothing about the credentials.
    """
    manager = _manager()
    connection = FakeConnection(_cursor(rows=[]))
    connect = (
        AsyncMock(return_value=connection)
        if failure is None
        else AsyncMock(side_effect=failure)
    )

    with patch(CONNECT, connect):
        assert await manager.credentials_refused() is refused


async def test_verify_tls_accepts_an_encrypted_session() -> None:
    """A session reporting a cipher passes the check."""
    connection = FakeConnection(
        FakeCursor(status_row=("Ssl_cipher", "TLS_AES_256_GCM_SHA384"))
    )

    await _async_verify_tls(connection)

    assert connection.cursor_obj.executed == ["SHOW STATUS LIKE 'Ssl_cipher'"]


@pytest.mark.parametrize("status_row", [("Ssl_cipher", ""), None])
async def test_verify_tls_rejects_a_plain_text_session(
    status_row: tuple | None,
) -> None:
    """An empty cipher means the server never encrypted the connection.

    This is the case aiomysql does not report on its own: it skips the
    handshake when the server does not advertise TLS and carries on in plain
    text instead of raising anything.
    """
    connection = FakeConnection(FakeCursor(status_row=status_row))

    with pytest.raises(TLSUnavailableError):
        await _async_verify_tls(connection)


async def test_test_connection_checks_tls_when_asked() -> None:
    """The config flow path verifies the session it just opened."""
    manager = _manager(use_tls=True)
    connection = FakeConnection(FakeCursor(status_row=("Ssl_cipher", "")))

    with (
        patch(CONNECT, AsyncMock(return_value=connection)),
        pytest.raises(TLSUnavailableError),
    ):
        await manager.test_connection()

    assert connection.ensure_closed_called is True


async def test_test_connection_skips_the_tls_check_when_off() -> None:
    """Without the option the extra round trip is not made at all."""
    manager = _manager()
    connection = FakeConnection(FakeCursor(rows=[], status_row=("Ssl_cipher", "")))

    with patch(CONNECT, AsyncMock(return_value=connection)):
        await manager.test_connection()

    assert connection.cursor_obj.executed == ["SELECT 1"]
