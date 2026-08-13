"""Fixtures for the HA MySQL tests."""

from __future__ import annotations

from collections.abc import Generator
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
    """Replace the blocking database call with a mock."""
    with patch(
        "custom_components.ha_mysql.coordinator.MySQLConnectionManager.execute",
        autospec=True,
        return_value=list(ROWS),
    ) as mock:
        yield mock
