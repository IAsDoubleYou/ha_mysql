"""Connection handling and data coordination for the HA MySQL integration."""

from __future__ import annotations

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

from mysql.connector import errors as mysql_errors
from mysql.connector.pooling import MySQLConnectionPool

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_MYSQL_DATABASE,
    CONF_MYSQL_HOST,
    CONF_MYSQL_PASSWORD,
    CONF_MYSQL_PORT,
    CONF_MYSQL_USERNAME,
    CONNECT_TIMEOUT,
    DOMAIN,
    LARGE_RESULT_WARNING_THRESHOLD,
    MAX_QUERY_ATTEMPTS,
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


def _convert_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert driver specific types into values Home Assistant can store.

    Decimal values are converted to strings, which is the behaviour this
    integration has always had. SQL NULL is returned as None.
    """
    return {
        key: str(value) if isinstance(value, decimal.Decimal) else value
        for key, value in row.items()
    }


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that renders Decimal values as strings."""

    def default(self, o: Any) -> Any:
        """Encode values the standard encoder does not support."""
        if isinstance(o, decimal.Decimal):
            return str(o)
        return super().default(o)


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

    def _acquire(self) -> Any:
        """Return a healthy pooled connection, creating the pool if needed."""
        with self._lock:
            if self._pool is None:
                self._pool = MySQLConnectionPool(
                    pool_name=f"{DOMAIN}_{next(_POOL_COUNTER)}",
                    pool_size=POOL_SIZE,
                    pool_reset_session=True,
                    **self._db_config,
                )
            pool = self._pool

        connection = pool.get_connection()
        try:
            # A pooled connection may have been closed by the server after
            # wait_timeout, so verify it before handing it out.
            connection.ping(reconnect=True, attempts=2, delay=1)
        except Exception:
            with contextlib.suppress(Exception):
                connection.close()
            raise
        return connection

    def _invalidate_pool(self) -> None:
        """Drop the pool so the next query builds fresh connections."""
        with self._lock:
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
            connection = None
            try:
                connection = self._acquire()
                cursor = connection.cursor(buffered=True, dictionary=True)
                try:
                    cursor.execute(query)
                    rows = cursor.fetchall() or []
                finally:
                    cursor.close()
            except mysql_errors.PoolError as err:
                # All connections are in use; back off and try once more.
                last_error = err
                _LOGGER.debug("Connection pool exhausted (attempt %s)", attempt)
            except _CONNECTION_ERRORS as err:
                last_error = err
                _LOGGER.debug(
                    "Connection to %s failed (attempt %s): %s",
                    self.target,
                    attempt,
                    err,
                )
                self._invalidate_pool()
            except mysql_errors.Error as err:
                # Syntax errors, missing tables, denied privileges: retrying
                # would only repeat the same failure.
                raise MySQLQueryError(
                    f"Query failed: {err}", getattr(err, "errno", None)
                ) from err
            else:
                return [_convert_row(row) for row in rows]
            finally:
                if connection is not None:
                    with contextlib.suppress(Exception):
                        connection.close()

            if attempt < MAX_QUERY_ATTEMPTS:
                time.sleep(RETRY_DELAY)

        raise MySQLConnectionError(
            f"Could not reach MySQL at {self.target}: {last_error}",
            getattr(last_error, "errno", None),
        )

    def test_connection(self) -> None:
        """Verify the settings by running a trivial query.

        Blocking, run in an executor. Raises MySQLConnectionError when the
        server cannot be reached and MySQLQueryError when it refuses us.
        """
        try:
            self.execute("SELECT 1")
        finally:
            self.close()

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
        name: str,
        query: str,
        scan_interval: timedelta,
        max_json_rows: int,
    ) -> None:
        """Initialise the coordinator for one query."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} {name}",
            update_interval=scan_interval,
        )
        self._manager = manager
        self._max_json_rows = max_json_rows
        self._warned_large_result = False
        self.default_query = query
        self.query = query

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
                default=str,
                cls=DecimalEncoder,
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
