"""Connection handling and data coordination for the HA MySQL integration."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
import decimal
import json
import logging
from typing import Any, Final, Literal

import aiomysql
from pymysql import err as pymysql_err

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.ssl import client_context_no_verify

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

# PyMySQL error codes that say nothing about the settings, only that the
# server cannot be used right now: it could not be reached or the connection
# was lost, or it is full or shutting down. Waiting fixes these. Codes outside
# this set, including a rejected username or an unknown database, raise the
# very same OperationalError as a dropped socket does, so the code is what
# tells the two apart, not the exception class.
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
        1203,  # This user has more connections than max_user_connections
    }
)

# MySQL error codes that setup and the forms tell apart from the rest.
ERRNO_DATABASE_ACCESS_DENIED: Final = 1044
ERRNO_ACCESS_DENIED: Final = 1045
ERRNO_UNKNOWN_DATABASE: Final = 1049

# Run by the driver right after every login. Both MySQL and MariaDB refuse a
# write, a temporary table included, with error 1792 in a read only
# transaction, and with autocommit on every statement is one.
READ_ONLY_COMMAND: Final = "SET SESSION TRANSACTION READ ONLY"

type ConnectFailure = Literal[
    "invalid_auth", "unknown_database", "cannot_connect", "connection_refused"
]


class MySQLError(HomeAssistantError):
    """Base error for the HA MySQL integration."""

    def __init__(
        self, message: str, errno: int | None = None, detail: str | None = None
    ) -> None:
        """Keep the driver error code and message so callers can tell causes apart.

        ``detail`` is what the driver itself said, without the context this
        integration puts in front of it. The forms and the translated messages
        show that part.
        """
        super().__init__(message)
        self.errno = errno
        self.detail = message if detail is None else detail


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
                f"Could not reach MySQL at {target}: {message}", errno, message
            )
        return MySQLQueryError(f"Query failed: {message}", errno, message)
    # OSError, and anything else that is not a MySQL protocol error, is always
    # a connectivity problem: the driver never got far enough to send a query.
    return MySQLConnectionError(
        f"Could not reach MySQL at {target}: {err}", detail=str(err)
    )


def classify_connect_error(err: MySQLError) -> ConnectFailure:
    """Return what a failed attempt to connect means for whoever has to fix it.

    Setup and the forms both decide by this, so the same answer from the
    server always gets the same advice:

    - invalid_auth: the username or password was refused; new ones fix it.
    - unknown_database: the database does not exist or this user may not use
      it; a new password does not fix that.
    - cannot_connect: the server cannot be used right now; waiting fixes it.
    - connection_refused: any other refusal by the server, such as a host the
      account may not connect from, a locked account or an expired password;
      waiting does not fix it.
    """
    if isinstance(err, MySQLConnectionError):
        return "cannot_connect"
    if err.errno == ERRNO_ACCESS_DENIED:
        return "invalid_auth"
    if err.errno in (ERRNO_DATABASE_ACCESS_DENIED, ERRNO_UNKNOWN_DATABASE):
        return "unknown_database"
    return "connection_refused"


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
            # This integration only reads, so every session it opens is set to
            # read only before the first query. The driver runs this on every
            # login, including the one ping(reconnect=True) makes after the
            # server dropped an idle connection, and the test connection
            # shares these settings. It is a second line behind the check on
            # the query text, and no replacement for the privileges of the
            # database user: a statement that got past that check, such as a
            # SET, could lift it again.
            "init_command": READ_ONLY_COMMAND,
        }
        # Left out entirely when TLS is off, which is what keeps aiomysql from
        # offering it: it only runs the handshake when a context is present.
        #
        # The server certificate is deliberately not checked. A database on a
        # home network nearly always carries a self signed one, and demanding
        # a verifiable certificate would make the option unusable for most
        # setups. This encrypts the traffic, which keeps it from being read off
        # the network; it does not prove the server is the one it claims to be.
        #
        # The context comes from Home Assistant, which builds it once and
        # caches it. Building one here would load the CA bundle from disk on
        # the event loop. The cached context is shared, so it is not modified.
        if self._use_tls:
            self._connect_kwargs["ssl"] = client_context_no_verify()

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

        Only a query that completed leaves its connection fit for reuse. One
        that failed, timed out or was cancelled may still have a query in
        flight on the wire, and reusing it would let the next query read its
        leftovers, so that connection is closed. Either way it goes back
        through release(): only that frees its slot in the pool, and aiomysql
        does not pool a closed connection again. A connection that skipped
        release() would stay counted as in use for good: after POOL_MAX_SIZE
        failures the pool would hand out nothing at all, and close() would
        wait for it forever.
        """
        pool: aiomysql.Pool | None = None
        connection: aiomysql.Connection | None = None
        reusable = False
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
            reusable = True
        except TimeoutError as err:
            raise MySQLConnectionError(
                f"Timed out reaching MySQL at {self.target}",
                detail=f"Timed out after {QUERY_TIMEOUT} seconds",
            ) from err
        except (pymysql_err.Error, OSError) as err:
            raise _wrap_error(err, self.target) from err
        finally:
            if pool is not None and connection is not None:
                if not reusable:
                    connection.close()
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
                f"Timed out reaching MySQL at {self.target}",
                detail=f"Timed out after {QUERY_TIMEOUT} seconds",
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
                f"Timed out reaching MySQL at {self.target}",
                detail=f"Timed out after {QUERY_TIMEOUT} seconds",
            ) from err
        except (pymysql_err.Error, OSError) as err:
            raise _wrap_error(err, self.target) from err
        finally:
            # This connection is not pooled, so this really does close it.
            await connection.ensure_closed()

    async def credentials_refused(self) -> bool:
        """Return whether the server refuses the stored credentials on a new login.

        Error 1045 on a query does not only mean that: MySQL and MariaDB also
        answer a statement that needs the FILE privilege, such as SELECT ...
        INTO OUTFILE, with it. Only a fresh login tells the two apart.
        Anything else that goes wrong with that login says nothing about the
        credentials.
        """
        try:
            await self.test_connection()
        except MySQLQueryError as err:
            return err.errno == ERRNO_ACCESS_DENIED
        except MySQLConnectionError, TLSUnavailableError:
            return False
        return False

    async def close(self) -> None:
        """Close every pooled connection, including the ones still in use.

        terminate() rather than close(): after close(), wait_closed() waits
        until every borrowed connection is back, and a query on a database
        that stopped answering only gives its connection back after
        QUERY_TIMEOUT. When that connection comes back closed, release() does
        not wake wait_closed() at all, so unloading the entry would hang for
        good. Whoever calls this is done with the pool, an entry being
        unloaded or a finished test run in the options flow, so nothing is
        waiting for those queries any more.
        """
        async with self._pool_lock:
            pool, self._pool = self._pool, None
        if pool is None:
            return
        pool.terminate()
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
        self._connection_title = config_entry.title
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
        except MySQLQueryError as err:
            if (
                err.errno == ERRNO_ACCESS_DENIED
                and await self._manager.credentials_refused()
            ):
                # Raised from here, Home Assistant starts the sign-in flow of
                # the entry by itself, once however many sensors run into it,
                # and stops polling until the entry is loaded again.
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN,
                    translation_key="invalid_auth",
                    translation_placeholders={"connection": self._connection_title},
                ) from err
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="query_failed",
                translation_placeholders={"error": err.detail},
            ) from err
        except MySQLConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="unreachable",
                translation_placeholders={
                    "target": self._manager.target,
                    "error": err.detail,
                },
            ) from err

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
