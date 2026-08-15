"""Connection handling and data coordination for the HA MySQL integration."""

from __future__ import annotations

from collections.abc import Iterator
import contextlib
from dataclasses import dataclass, field
from datetime import timedelta
import decimal
import itertools
import json
import logging
import threading
import time
from typing import Any

import mysql.connector
from mysql.connector import errors as mysql_errors
from mysql.connector.pooling import MySQLConnectionPool, PooledMySQLConnection

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    BINARY_PREVIEW_BYTES,
    CONF_MAX_JSON_ROWS,
    CONF_MYSQL_DATABASE,
    CONF_MYSQL_HOST,
    CONF_MYSQL_PASSWORD,
    CONF_MYSQL_PORT,
    CONF_MYSQL_USERNAME,
    CONF_QUERY,
    CONNECT_TIMEOUT,
    DEFAULT_MAX_JSON_ROWS,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    LARGE_RESULT_WARNING_THRESHOLD,
    MAX_QUERY_ATTEMPTS,
    POOL_ACQUIRE_INTERVAL,
    POOL_ACQUIRE_TIMEOUT,
    POOL_SIZE,
    RETRY_DELAY,
)

_LOGGER = logging.getLogger(__name__)

# Pool names must be unique within the process and are restricted to a small
# character set, so they are generated instead of derived from user input.
_POOL_COUNTER = itertools.count(1)

# Errors that indicate a broken or exhausted connection rather than a bad
# query. Only these are worth retrying.
_CONNECTION_ERRORS = (
    mysql_errors.InterfaceError,
    mysql_errors.OperationalError,
)


class MySQLError(HomeAssistantError):
    """Base error for the HA MySQL integration."""

    def __init__(self, message: str, errno: int | None = None) -> None:
        """Keep the driver error code so callers can tell causes apart."""
        super().__init__(message)
        self.errno = errno


class MySQLConnectionError(MySQLError):
    """Raised when the database cannot be reached."""


class MySQLQueryError(MySQLError):
    """Raised when the database rejects the query itself."""


def _decode_binary(value: bytes | bytearray) -> str:
    """Return a readable representation of a BINARY, VARBINARY or BLOB value.

    Text that happens to be stored in a binary column is returned as text.
    Anything that is not valid UTF-8, such as an image or an encrypted value,
    becomes a short hexadecimal preview instead, so a state or an attribute
    never ends up holding raw bytes.
    """
    try:
        return bytes(value).decode()
    except UnicodeDecodeError:
        preview = bytes(value[:BINARY_PREVIEW_BYTES]).hex()
        suffix = "..." if len(value) > BINARY_PREVIEW_BYTES else ""
        return f"0x{preview}{suffix}"


def _convert_value(value: Any) -> Any:
    """Convert a single column value into something Home Assistant can store.

    SQL NULL stays None, and dates and timestamps are left as they are so the
    date and timestamp device classes keep working. Everything the state
    machine and the JSON encoder cannot handle is turned into text.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, decimal.Decimal):
        # Kept as a string, which is the behaviour of earlier releases.
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return _decode_binary(value)
    if isinstance(value, timedelta):
        # A TIME column comes back as a timedelta; "1:30:00" reads better.
        return str(value)
    if isinstance(value, set):
        # A SET column comes back as a set, which is not JSON serialisable.
        return sorted(value)
    return value


def _convert_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert every column of a result row. See _convert_value."""
    return {key: _convert_value(value) for key, value in row.items()}


class QueryResultEncoder(json.JSONEncoder):
    """JSON encoder for the driver types the standard encoder rejects."""

    def default(self, o: Any) -> str:
        """Render an unsupported value as a string instead of raising.

        Rows are converted by _convert_row before they get here, so this only
        catches types that survive that, such as dates and timestamps.
        """
        if isinstance(o, decimal.Decimal):
            return str(o)
        if isinstance(o, (bytes, bytearray)):
            return _decode_binary(o)
        return str(o)


@dataclass(frozen=True)
class QueryResult:
    """Result of a single query execution."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    query: str = ""
    json_result: str = "{}"
    json_truncated: bool = False
    query_date: str = ""
    query_time: str = ""

    @property
    def row_count(self) -> int:
        """Return the number of rows in the result set."""
        return len(self.rows)


class MySQLConnectionManager:
    """Own a lazily created connection pool shared by every sensor."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Store the database configuration."""
        self._db_config: dict[str, Any] = {
            "host": config[CONF_MYSQL_HOST],
            "port": int(config[CONF_MYSQL_PORT]),
            "user": config[CONF_MYSQL_USERNAME],
            "password": config[CONF_MYSQL_PASSWORD],
            "database": config[CONF_MYSQL_DATABASE],
            "connection_timeout": CONNECT_TIMEOUT,
            # Without autocommit a pooled connection keeps an open transaction,
            # which makes InnoDB return the same snapshot on every poll.
            "autocommit": True,
        }
        self._pool: MySQLConnectionPool | None = None
        self._lock = threading.Lock()

    @property
    def target(self) -> str:
        """Return a printable description of the configured database."""
        return (
            f"{self._db_config['host']}:{self._db_config['port']}"
            f"/{self._db_config['database']}"
        )

    def _get_pool(self) -> MySQLConnectionPool:
        """Return the shared pool, creating it on first use."""
        with self._lock:
            if self._pool is None:
                self._pool = MySQLConnectionPool(
                    pool_name=f"{DOMAIN}_{next(_POOL_COUNTER)}",
                    pool_size=POOL_SIZE,
                    pool_reset_session=True,
                    **self._db_config,
                )
            return self._pool

    def _checkout(self, pool: MySQLConnectionPool) -> PooledMySQLConnection:
        """Take a connection out of the pool, waiting for one to come free.

        The pool of the driver never blocks: it reports "pool exhausted" as
        soon as every connection is handed out. Sensors that poll at the same
        moment would fail on that even though a connection comes free a
        fraction of a second later, so the wait is polled here.
        """
        deadline = time.monotonic() + POOL_ACQUIRE_TIMEOUT
        while True:
            try:
                return pool.get_connection()
            except mysql_errors.PoolError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(POOL_ACQUIRE_INTERVAL)

    @contextlib.contextmanager
    def _connection(self, pool: MySQLConnectionPool) -> Iterator[PooledMySQLConnection]:
        """Yield a pooled connection and always hand it back afterwards.

        Closing a pooled connection does not close the socket, it returns the
        connection to the pool. That has to happen on every path: after a
        successful query, after a failed one, and after an unexpected
        exception. A connection that is not handed back stays checked out for
        good, and once that has happened POOL_SIZE times every later query
        runs into "pool exhausted" instead of reaching the database.
        """
        connection = self._checkout(pool)
        try:
            # A pooled connection may have been closed by the server after
            # wait_timeout, so verify it before the query runs.
            connection.ping(reconnect=True, attempts=2, delay=1)
            yield connection
        finally:
            with contextlib.suppress(Exception):
                connection.close()

    def _invalidate_pool(self, stale: MySQLConnectionPool | None = None) -> None:
        """Drop the pool so the next query builds fresh connections.

        When a pool is given it is only dropped while it is still the current
        one. Sensors share the pool and fail together, so without that check
        the thread that notices the outage second would tear down the pool the
        first one just rebuilt, and the two would keep replacing each other's
        connections until the database runs out of them.
        """
        with self._lock:
            if stale is not None and stale is not self._pool:
                return
            pool, self._pool = self._pool, None
        if pool is None:
            return
        # There is no public API to dispose of a pool, so failures here are
        # ignored; dropping the reference is enough to stop using it.
        with contextlib.suppress(Exception):
            pool._remove_connections()  # noqa: SLF001

    def execute(self, query: str) -> list[dict[str, Any]]:
        """Run a query and return its rows. Blocking, run in an executor."""
        last_error: Exception | None = None

        for attempt in range(1, MAX_QUERY_ATTEMPTS + 1):
            pool: MySQLConnectionPool | None = None
            try:
                pool = self._get_pool()
                with (
                    self._connection(pool) as connection,
                    contextlib.closing(
                        connection.cursor(buffered=True, dictionary=True)
                    ) as cursor,
                ):
                    cursor.execute(query)
                    rows = cursor.fetchall() or []
            except mysql_errors.PoolError as err:
                # Every connection is in use and none came free in time. The
                # pool itself is healthy, so it is kept.
                last_error = err
                _LOGGER.debug(
                    "No free connection in the pool of %s (attempt %s)",
                    self.target,
                    attempt,
                )
            except _CONNECTION_ERRORS as err:
                last_error = err
                _LOGGER.debug(
                    "Connection to %s failed (attempt %s): %s",
                    self.target,
                    attempt,
                    err,
                )
                # The connection was already handed back by _connection, so
                # the pool can be thrown away without losing one.
                self._invalidate_pool(pool)
            except mysql_errors.Error as err:
                # Syntax errors, missing tables, denied privileges: retrying
                # would only repeat the same failure.
                raise MySQLQueryError(
                    f"Query failed: {err}", getattr(err, "errno", None)
                ) from err
            else:
                return [_convert_row(row) for row in rows]

            if attempt < MAX_QUERY_ATTEMPTS:
                time.sleep(RETRY_DELAY)

        if isinstance(last_error, mysql_errors.PoolError):
            raise MySQLConnectionError(
                f"All {POOL_SIZE} connections to {self.target} are in use: "
                f"{last_error}",
                getattr(last_error, "errno", None),
            )

        raise MySQLConnectionError(
            f"Could not reach MySQL at {self.target}: {last_error}",
            getattr(last_error, "errno", None),
        )

    def test_connection(self) -> None:
        """Verify the settings on a single connection of its own.

        Blocking, run in an executor. Raises MySQLConnectionError when the
        server cannot be reached and MySQLQueryError when it refuses us.

        This deliberately stays away from the pool. The check runs on every
        setup and on every submitted config flow, while building a pool opens
        POOL_SIZE connections at once; doing that for one SELECT 1 is what
        pushed a busy server over its connection limit.
        """
        try:
            connection = mysql.connector.connect(**self._db_config)
        except _CONNECTION_ERRORS as err:
            raise MySQLConnectionError(
                f"Could not reach MySQL at {self.target}: {err}",
                getattr(err, "errno", None),
            ) from err
        except mysql_errors.Error as err:
            raise MySQLQueryError(
                f"Query failed: {err}", getattr(err, "errno", None)
            ) from err

        try:
            with contextlib.closing(connection.cursor()) as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchall()
        except mysql_errors.Error as err:
            raise MySQLQueryError(
                f"Query failed: {err}", getattr(err, "errno", None)
            ) from err
        finally:
            # This connection is not pooled, so this really does close it.
            with contextlib.suppress(Exception):
                connection.close()

    def close(self) -> None:
        """Release every pooled connection. Blocking, run in an executor."""
        self._invalidate_pool()


class MySQLQueryCoordinator(DataUpdateCoordinator[QueryResult]):
    """Poll a single query and share the result with its sensor."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        manager: MySQLConnectionManager,
        config: dict[str, Any],
    ) -> None:
        """Initialise the coordinator from the stored sensor configuration."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} {config[CONF_NAME]}",
            update_interval=timedelta(
                seconds=config.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS)
            ),
        )
        self._manager = manager
        self._max_json_rows: int = config.get(CONF_MAX_JSON_ROWS, DEFAULT_MAX_JSON_ROWS)
        self._warned_large_result = False
        self.default_query: str = config[CONF_QUERY]
        self.query: str = self.default_query

    def _fetch(self, query: str) -> QueryResult:
        """Execute the query and build the result. Runs in an executor."""
        now = dt_util.now()
        rows = self._manager.execute(query)

        if not rows:
            return QueryResult(
                rows=[],
                query=query,
                json_result="{}",
                query_date=now.strftime("%Y-%m-%d"),
                query_time=now.strftime("%H:%M:%S"),
            )

        if 0 < self._max_json_rows < len(rows):
            json_rows = rows[: self._max_json_rows]
            truncated = True
        else:
            json_rows = rows
            truncated = False

        if (
            not truncated
            and not self._warned_large_result
            and len(rows) > LARGE_RESULT_WARNING_THRESHOLD
        ):
            self._warned_large_result = True
            _LOGGER.warning(
                "Query for %s returned %s rows; the whole result set is stored "
                "in the json_result attribute on every update. Consider setting "
                "max_json_rows or narrowing the query",
                self.name,
                len(rows),
            )

        return QueryResult(
            rows=rows,
            query=query,
            json_result=json.dumps(
                json_rows,
                ensure_ascii=False,
                indent=4,
                cls=QueryResultEncoder,
            ),
            json_truncated=truncated,
            query_date=now.strftime("%Y-%m-%d"),
            query_time=now.strftime("%H:%M:%S"),
        )

    async def _async_update_data(self) -> QueryResult:
        """Fetch the current result set."""
        query = self.query
        try:
            return await self.hass.async_add_executor_job(self._fetch, query)
        except MySQLError as err:
            raise UpdateFailed(str(err)) from err
