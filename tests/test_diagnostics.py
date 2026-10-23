"""Tests for the diagnostics of the HA MySQL integration."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from .conftest import CONNECTION, SENSOR, make_entry, setup_entry
from custom_components.ha_mysql.coordinator import MySQLConnectionManager
from custom_components.ha_mysql.diagnostics import async_get_config_entry_diagnostics

REDACTED = "**REDACTED**"


async def _diagnostics(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    """Set the entry up and return its diagnostics."""
    await setup_entry(hass, entry)
    return await async_get_config_entry_diagnostics(hass, entry)  # type: ignore[arg-type]


async def test_the_connection_is_masked(hass: HomeAssistant, mock_execute: Any) -> None:
    """Neither the credentials nor the place of the database are reported."""
    result = await _diagnostics(hass, make_entry())

    assert result["entry"]["data"] == {
        "host": REDACTED,
        "port": REDACTED,
        "username": REDACTED,
        "password": REDACTED,
        "database": REDACTED,
        "use_tls": False,
    }
    assert result["entry"]["title"] == REDACTED
    assert result["entry"]["unique_id"] == REDACTED


async def test_nothing_of_the_connection_leaks_anywhere(
    hass: HomeAssistant, mock_execute: Any
) -> None:
    """No value of the connection shows up in the output, wherever it is."""
    result = await _diagnostics(hass, make_entry())

    text = json.dumps(result, default=str)
    for key in ("host", "username", "password", "database"):
        assert f'"{CONNECTION[key]}"' not in text
    assert "db.local" not in text


async def test_a_sensor_reports_its_last_run_but_no_data(
    hass: HomeAssistant, mock_execute: Any
) -> None:
    """The query and the figures of the last run are in, the rows are not."""
    result = await _diagnostics(hass, make_entry())

    (sensor,) = result["sensors"]
    assert sensor["config"]["query"] == SENSOR["query"]
    assert sensor["config"]["unique_id"] == SENSOR["unique_id"]
    assert "entity_id" not in sensor
    assert sensor["available"] is True
    assert sensor["last_run"]["row_count"] == 2
    assert sensor["last_run"]["executed_sql_query"] == SENSOR["query"]
    assert "Alice" not in json.dumps(result, default=str)


async def test_the_pool_is_reported_once_it_is_open(
    hass: HomeAssistant, mock_execute: Any
) -> None:
    """The pool has no size to report while the queries are mocked away."""
    result = await _diagnostics(hass, make_entry())

    assert result["pool"] is None


async def test_a_sensor_that_is_not_loaded_is_reported_as_unavailable(
    hass: HomeAssistant, mock_execute: Any
) -> None:
    """A configured sensor without an entity is listed, not skipped."""
    entry = make_entry()
    entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)  # type: ignore[arg-type]

    (sensor,) = result["sensors"]
    assert sensor["available"] is False
    assert sensor["last_run"] == {}


async def test_pool_status_describes_an_open_pool() -> None:
    """The size of the pool is reported as plain numbers."""
    manager = MySQLConnectionManager(CONNECTION)
    assert manager.pool_status is None

    manager._pool = SimpleNamespace(minsize=1, maxsize=10, size=3, freesize=2)  # type: ignore[assignment]

    assert manager.pool_status == {"minimum": 1, "maximum": 10, "open": 3, "idle": 2}
