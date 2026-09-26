"""Connection handling and data coordination for the HA MySQL integration."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
import decimal
import json
import logging
import ssl
from typing import Any

import aiomysql
from pymysql import err as pymysql_err

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
    CONF_USE_TLS,
    CONNECT_TIMEOUT,
    DEFAULT_MAX_JSON_ROWS,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DEFAULT_USE_TLS,
    DOMAIN,
    LARGE_RESULT_WARNING_THRESHOLD,
    POOL_MAX_SIZE,
    POOL_MIN_SIZE,
    POOL_RECYCLE_SECONDS,
    QUERY_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

# A server-side driver error carries (errno, message) in .args; a client-side
# one, such as a refused connection, carries only the message.
_ERRNO_MESSAGE_ARGS = 2

# PyMySQL error codes that mean the server could not be reached at all. Codes
# outside this set, including a rejected username or an unknown database,
# raise the very same OperationalError as a dropped socket does, so the code
# is what tells the two apart, not the exception class.
_CONNECTIVITY_ERRNOS = frozenset(
    {
        2002,  # Can't connect through the socket file
        2003,  # Can't connect to the server
        2005,  # Unknown host
        2006,  # MySQL server has gone away
        2013,  # Lost connection during query
        1040,  # Too many connections
        1053,  # Server shutdown in progress
        1152,  # Aborted connection
    }
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


class TLSUnavailableError(HomeAssistantError):
    """Raised when TLS was asked for but the connection ended up in plain text."""


def _error_details(err: BaseException) -> tuple[int | None, str]:
    """Split a driver error into its MySQL error number and message."""
    args = getattr(err, "args", ())
    if len(args) >= _ERRNO_MESSAGE_ARGS and isinstance(args[0], int):
        return args[0], str(args[1])
    if len(args) == 1:
        return None, str(args[0])
    return None, str(err)


def _wrap_error(err: BaseException, target: str) -> MySQLError:
    """Turn a driver error into a MySQLConnectionError or MySQLQueryError."""
    if isinstance(err, pymysql_err.Error):
        errno, message = _error_details(err)
        if errno is None or errno in _CONNECTIVITY_ERRNOS:
            return MySQLConnectionError(
                f"Could not reach MySQL at {target}: {message}", errno
            )
        return MySQLQueryError(f"Query failed: {message}", errno)
    # OSError, and anything else that is not a MySQL protocol error, is always
    # a connectivity problem: the driver never got far enough to send a query.
    return MySQLConnectionError(f"Could not reach MySQL at {target}: {err}")


def _tls_context() -> ssl.SSLContext:
    """Return the TLS context used for an encrypted connection.

    The server certificate is deliberately not checked. A database on a home
    network nearly always carries a self signed one, and demanding a
    verifiable certificate would make the option unusable for most setups.
    This encrypts the traffic, which keeps it from being read off the
    network; it does not prove the server is the one it claims to be.
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


async def _async_verify_tls(connection: aiomysql.Connection) -> None:
    """Raise when a connection that asked for TLS is not actually encrypted.

    aiomysql only runs the handshake when the server advertises TLS, and
    carries on in plain text when it does not, without reporting anything. So
    asking for TLS is not the same as getting it, and the session status is
    the only thing that says which of the two happened.
    """
    async with connection.cursor() as cursor:
        await cursor.execute("SHOW STATUS LIKE 'Ssl_cipher'")
        row = await cursor.fetchone()

    if not (row and row[1]):
        raise TLSUnavailableError(
            "The server accepted the connection but did not encrypt it. Check "
            "that the database is configured for TLS, or turn the option off."
        )


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
    """Own a connection pool shared by every sensor of one config entry."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Store the database configuration."""
        self._use_tls: bool = bool(config.get(CONF_USE_TLS, DEFAULT_USE_TLS))
        self._connect_kwargs: dict[str, Any] = {
            "host": config[CONF_MYSQL_HOST],
            "port": int(config[CONF_MYSQL_PORT]),
            "user": config[CONF_MYSQL_USERNAME],
            "password": config[CONF_MYSQL_PASSWORD],
            "db": config[CONF_MYSQL_DATABASE],
            "connect_timeout": CONNECT_TIMEOUT,
            # Without autocommit a pooled connection keeps an open transaction,
            # which makes InnoDB return the same snapshot on every poll.
            "autocommit": True,
        }
        if self._use_tls:
            self._connect_kwargs["ssl"] = _tls_context()

        self._target: str = (
            f"{self._connect_kwargs['host']}:{self._connect_kwargs['port']}"
            f"/{self._connect_kwargs['db']}"
        )
        self._pool: aiomysql.Pool | None = None
        self._pool_lock = asyncio.Lock()

    @property
    def target(self) -> str:
        """Return a printable description of the configured database."""
        return self._target

    async def _get_pool(self) -> aiomysql.Pool:
        """Return the shared pool, creating it on first use."""
        async with self._pool_lock:
            if self._pool is None:
                self._pool = await aiomysql.create_pool(
                    minsize=POOL_MIN_SIZE,
                    maxsize=POOL_MAX_SIZE,
                    pool_recycle=POOL_RECYCLE_SECONDS,
                    **self._connect_kwargs,
                )
            return self._pool

    async def execute(
        self, query: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        """Run a query and return its rows.

        ``params`` binds values to %s placeholders in ``query``: the driver
        quotes and escapes each one according to its type, so data passed
        this way can never change what the statement does. Left as None, the
        query is sent exactly as written, which also keeps a literal percent
        sign, such as in LIKE '%text%', from being misread as a placeholder.
        """
        connection: aiomysql.Connection | None = None
        try:
            async with asyncio.timeout(QUERY_TIMEOUT):
                pool = await self._get_pool()
                connection = await pool.acquire()
                # The server can have dropped this connection while it sat
                # idle in the pool (wait_timeout); reconnect instead of
                # failing the query outright.
                await connection.ping(reconnect=True)
                async with connection.cursor(aiomysql.DictCursor) as cursor:
                    await cursor.execute(query, params)
                    rows = await cursor.fetchall()
        except TimeoutError as err:
            if connection is not None:
                # It may still have a query in flight on the wire; handing it
                # back to the pool would let the next query read its
                # leftovers, so it is dropped instead of released.
                connection.close()
            raise MySQLConnectionError(
                f"Timed out reaching MySQL at {self.target}: {err}"
            ) from err
        except (pymysql_err.Error, OSError) as err:
            if connection is not None:
                connection.close()
            raise _wrap_error(err, self.target) from err
        else:
            pool.release(connection)
            return [_convert_row(row) for row in rows]

    async def test_connection(self) -> None:
        """Verify the settings on a connection of their own.

        Raises MySQLConnectionError when the server cannot be reached,
        MySQLQueryError when it refuses us, and TLSUnavailableError when TLS
        was requested but the server did not actually encrypt the session.

        This deliberately stays away from the pool. The check runs on every
        setup and on every submitted config flow, while building a pool opens
        POOL_MAX_SIZE connections at once; doing that for one SELECT 1 is what
        pushed a busy server over its connection limit.
        """
        try:
            async with asyncio.timeout(QUERY_TIMEOUT):
                connection = await aiomysql.connect(**self._connect_kwargs)
        except TimeoutError as err:
            raise MySQLConnectionError(
                f"Timed out reaching MySQL at {self.target}: {err}"
            ) from err
        except (pymysql_err.Error, OSError) as err:
            raise _wrap_error(err, self.target) from err

        try:
            async with asyncio.timeout(QUERY_TIMEOUT):
                if self._use_tls:
                    await _async_verify_tls(connection)
                async with connection.cursor() as cursor:
                    await cursor.execute("SELECT 1")
                    await cursor.fetchall()
        except TimeoutError as err:
            raise MySQLConnectionError(
                f"Timed out reaching MySQL at {self.target}: {err}"
            ) from err
        except (pymysql_err.Error, OSError) as err:
            raise _wrap_error(err, self.target) from err
        finally:
            # This connection is not pooled, so this really does close it.
            await connection.ensure_closed()

    async def close(self) -> None:
        """Release every pooled connection."""
        async with self._pool_lock:
            pool, self._pool = self._pool, None
        if pool is None:
            return
        pool.close()
        await pool.wait_closed()


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
        # Values bound to the %s placeholders of self.query, set by set_query.
        # None outside of that, and reset together with self.query whenever
        # set_query restores the default.
        self.query_values: tuple[Any, ...] | None = None

    async def _async_update_data(self) -> QueryResult:
        """Fetch the current result set."""
        query = self.query
        values = self.query_values
        now = dt_util.now()

        try:
            rows = await self._manager.execute(query, values)
        except MySQLError as err:
            raise UpdateFailed(str(err)) from err

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
