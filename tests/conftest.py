"""Fixtures for the HA MySQL tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

from mysql.connector import errors as mysql_errors
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from custom_components.ha_mysql.const import DOMAIN, POOL_SIZE

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
}

UNIQUE_ID = "db.local:3306/testdb"
ENTITY_ID = "sensor.employees"

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


class FakePool:
    """A stand-in for the driver pool that keeps count of its connections.

    Unlike a plain mock this refuses to hand out more connections than it
    has, so a connection that is never given back shows up as an exhausted
    pool instead of going unnoticed.
    """

    def __init__(
        self,
        rows: list[dict] | None = None,
        query_error: Exception | None = None,
        ping_error: Exception | None = None,
        size: int = POOL_SIZE,
    ) -> None:
        """Set up an idle pool of the given size."""
        self.size = size
        self.rows = rows if rows is not None else []
        self.query_error = query_error
        self.ping_error = ping_error
        self.in_use = 0
        self.peak_in_use = 0
        self.removed = False

    def get_connection(self) -> MagicMock:
        """Hand out a connection, or report the pool as exhausted."""
        if self.in_use >= self.size:
            raise mysql_errors.PoolError("Failed getting connection; pool exhausted")
        self.in_use += 1
        self.peak_in_use = max(self.peak_in_use, self.in_use)

        cursor = MagicMock()
        cursor.fetchall.return_value = self.rows
        if self.query_error is not None:
            cursor.execute.side_effect = self.query_error

        connection = MagicMock()
        connection.cursor.return_value = cursor
        connection.close.side_effect = self._release
        if self.ping_error is not None:
            connection.ping.side_effect = self.ping_error
        return connection

    def _release(self) -> None:
        """Take a connection back, the way close() does on a pooled one."""
        self.in_use -= 1

    def _remove_connections(self) -> int:
        """Record that the pool was thrown away."""
        self.removed = True
        return 0


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
    """Replace the blocking database calls with a mock.

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
