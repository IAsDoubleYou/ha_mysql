"""Fixtures for the HA MySQL tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Generator, Sequence
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from custom_components.ha_mysql.const import DOMAIN

pytest_plugins = "pytest_homeassistant_custom_component"

# What a user puts in configuration.yaml.
CONFIG = {
    "ha_mysql": {
        "host": "db.local",
        "username": "user",
        "password": "secret",
        "database": "testdb",
    },
    "sensor": [
        {
            "platform": "ha_mysql",
            "name": "Employees",
            "query": "SELECT * FROM emp",
        }
    ],
}

CONNECTION = {
    "host": "db.local",
    "port": 3306,
    "username": "user",
    "password": "secret",
    "database": "testdb",
    "use_tls": False,
}

UNIQUE_ID = "db.local:3306/testdb"
# Every sensor sits under a device named after the connection, so a newly
# registered entity ID combines the two: "testdb @ db.local" + "Employees".
# An entity that already existed under the old, bare ID keeps it; this is
# only what a fresh registration gets.
ENTITY_ID = "sensor.testdb_db_local_employees"

SENSOR: dict[str, Any] = {
    "name": "Employees",
    "query": "SELECT * FROM emp",
    "scan_interval": 30,
    "max_json_rows": 0,
    "value_column": None,
    "value_template": None,
    "unit_of_measurement": None,
    "device_class": None,
    "state_class": None,
    "suggested_display_precision": None,
    "unique_id": "ha_mysql_employees",
}

ROWS = [
    {"id": 1, "name": "Alice", "salary": "1000.50"},
    {"id": 2, "name": "Bob", "salary": "2000.00"},
]


class FakeCursor:
    """Stand-in for an aiomysql DictCursor.

    Only the surface the integration touches is implemented: it is an async
    context manager that executes a statement and hands back prepared rows.
    """

    def __init__(
        self,
        *,
        rows: Sequence[dict[str, Any]] | None = None,
        status_row: tuple | None = None,
        error: Exception | None = None,
        on_execute: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """Configure the result the cursor will report."""
        self.rows = list(rows or [])
        # Answered by SHOW STATUS LIKE 'Ssl_cipher', which reads a plain
        # tuple cursor rather than a dict one.
        self.status_row = status_row
        self.error = error
        self.on_execute = on_execute
        self.executed: list[str] = []
        self.executed_args: list[Any] = []
        self.closed = False

    async def execute(self, query: str, args: Any = None) -> None:
        """Record the statement and raise the configured error, if any."""
        self.executed.append(query)
        self.executed_args.append(args)
        if self.on_execute is not None:
            await self.on_execute(query)
        if self.error is not None:
            raise self.error

    async def fetchall(self) -> list[dict[str, Any]]:
        """Return the prepared rows."""
        return self.rows

    async def fetchone(self) -> tuple | None:
        """Return the prepared status row, for the TLS check."""
        return self.status_row

    async def close(self) -> None:
        """Mark the cursor as closed."""
        self.closed = True

    async def __aenter__(self) -> FakeCursor:
        """Enter the cursor context."""
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        """Close the cursor on leaving the context."""
        await self.close()
        return False


class FakeConnection:
    """Stand-in for an aiomysql connection, pooled or standalone."""

    def __init__(
        self, cursor: FakeCursor | None = None, *, ping_error: Exception | None = None
    ) -> None:
        """Create a connection handing out ``cursor`` for every cursor call."""
        self.cursor_obj = cursor if cursor is not None else FakeCursor()
        self.ping_error = ping_error
        self.pings = 0
        self.closed = False
        self.ensure_closed_called = False

    def cursor(self, *cursor_classes: type) -> FakeCursor:
        """Return the prepared cursor; usable as an async context manager."""
        return self.cursor_obj

    async def ping(self, reconnect: bool = True) -> None:
        """Count the liveness checks the integration performs."""
        self.pings += 1
        if self.ping_error is not None:
            raise self.ping_error

    def close(self) -> None:
        """Mark the connection as closed."""
        self.closed = True

    async def ensure_closed(self) -> None:
        """Mark a standalone connection as closed."""
        self.ensure_closed_called = True
        self.closed = True


class FakePool:
    """Stand-in for an aiomysql connection pool."""

    def __init__(self, connection: FakeConnection | None = None) -> None:
        """Create a pool that always hands out the same connection."""
        self.connection = connection if connection is not None else FakeConnection()
        self.acquired = 0
        self.released = 0
        self.closed = False
        self.wait_closed_called = False

    async def acquire(self) -> FakeConnection:
        """Hand out the pooled connection."""
        self.acquired += 1
        return self.connection

    def release(self, conn: FakeConnection) -> None:
        """Take the connection back into the pool."""
        self.released += 1

    def close(self) -> None:
        """Start closing the pool."""
        self.closed = True

    async def wait_closed(self) -> None:
        """Wait until the pool finished closing."""
        self.wait_closed_called = True


def make_sensor(**overrides: Any) -> dict[str, Any]:
    """Return a sensor configuration with the given fields replaced."""
    return {**SENSOR, **overrides}


def make_entry(sensors: list[dict[str, Any]] | None = None) -> MockConfigEntry:
    """Return a config entry for the test database."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="testdb @ db.local",
        data=CONNECTION,
        options={"sensors": sensors if sensors is not None else [SENSOR]},
        unique_id=UNIQUE_ID,
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add the entry to Home Assistant and set it up."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading of the custom integration in every test."""


@pytest.fixture
def mock_execute() -> Generator[Any]:
    """Replace the database calls with a mock.

    The connection check opens a connection of its own instead of borrowing
    one from the pool, so it is routed to the same mock: a test that makes the
    query fail expects the check to fail in the same way. Its call is not
    recorded, so tests can keep counting the queries they trigger themselves.
    """

    def check_connection(manager: Any) -> None:
        error = mock.side_effect
        if isinstance(error, type) and issubclass(error, BaseException):
            raise error
        if isinstance(error, BaseException):
            raise error

    with (
        patch(
            "custom_components.ha_mysql.coordinator.MySQLConnectionManager.execute",
            autospec=True,
            return_value=list(ROWS),
        ) as mock,
        patch(
            "custom_components.ha_mysql.coordinator.MySQLConnectionManager"
            ".test_connection",
            autospec=True,
            side_effect=check_connection,
        ),
    ):
        yield mock
