"""Tests for the HA MySQL sensor platform."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.ha_mysql.const import (
    DATA_CONFIG,
    DOMAIN,
    SERVICE_SELECT_RECORD,
    SERVICE_SET_QUERY,
)
from custom_components.ha_mysql.coordinator import MySQLConnectionError

from .conftest import CONFIG

ENTITY_ID = "sensor.employees"


async def _setup(hass: HomeAssistant) -> bool:
    """Set up the integration and its sensor platform."""
    assert await async_setup_component(hass, DOMAIN, CONFIG)
    result = await async_setup_component(hass, "sensor", CONFIG)
    await hass.async_block_till_done()
    return result


async def test_port_defaults_to_3306(hass: HomeAssistant, mock_execute) -> None:
    """An omitted port falls back to the MySQL default instead of failing."""
    assert await async_setup_component(hass, DOMAIN, CONFIG)
    assert hass.data[DOMAIN][DATA_CONFIG]["port"] == 3306


async def test_state_is_row_count(hass: HomeAssistant, mock_execute) -> None:
    """The state holds the number of rows returned by the query."""
    await _setup(hass)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == "2"


async def test_attributes_of_first_row(hass: HomeAssistant, mock_execute) -> None:
    """The columns of the first row are exposed with the valueof_ prefix."""
    await _setup(hass)

    attributes = hass.states.get(ENTITY_ID).attributes
    assert attributes["valueof_name"] == "Alice"
    assert attributes["valueof_salary"] == "1000.50"
    assert attributes["selected_row"] == 0
    assert attributes["executed_sql_query"] == "SELECT * FROM emp"
    assert "query_date" in attributes
    assert "query_time" in attributes


async def test_unique_id_is_stable(hass: HomeAssistant, mock_execute) -> None:
    """The unique ID keeps the format used by earlier releases."""
    from homeassistant.helpers import entity_registry as er

    await _setup(hass)

    entry = er.async_get(hass).async_get(ENTITY_ID)
    assert entry is not None
    assert entry.unique_id == "ha_mysql_employees"


async def test_select_record_switches_row(hass: HomeAssistant, mock_execute) -> None:
    """select_record exposes the columns of the requested row."""
    await _setup(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SELECT_RECORD,
        {"entity_id": ENTITY_ID, "rownumber": 1},
        blocking=True,
    )

    attributes = hass.states.get(ENTITY_ID).attributes
    assert attributes["valueof_name"] == "Bob"
    assert attributes["selected_row"] == 1


async def test_select_record_out_of_range(hass: HomeAssistant, mock_execute) -> None:
    """A row beyond the result set falls back to the first row."""
    await _setup(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SELECT_RECORD,
        {"entity_id": ENTITY_ID, "rownumber": 99},
        blocking=True,
    )

    attributes = hass.states.get(ENTITY_ID).attributes
    assert attributes["valueof_name"] == "Alice"
    assert attributes["selected_row"] == 0


async def test_set_query_refreshes_immediately(
    hass: HomeAssistant, mock_execute
) -> None:
    """set_query replaces the query and re-runs it right away."""
    await _setup(hass)
    mock_execute.reset_mock()

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_QUERY,
        {"entity_id": ENTITY_ID, "query": "SELECT 1 FROM DUAL"},
        blocking=True,
    )

    assert mock_execute.call_count == 1
    assert mock_execute.call_args[0][1] == "SELECT 1 FROM DUAL"
    assert (
        hass.states.get(ENTITY_ID).attributes["executed_sql_query"]
        == "SELECT 1 FROM DUAL"
    )


async def test_set_query_without_query_restores_default(
    hass: HomeAssistant, mock_execute
) -> None:
    """An empty query restores the query from the configuration."""
    await _setup(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_QUERY,
        {"entity_id": ENTITY_ID, "query": "SELECT 1 FROM DUAL"},
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_QUERY,
        {"entity_id": ENTITY_ID, "query": ""},
        blocking=True,
    )

    assert (
        hass.states.get(ENTITY_ID).attributes["executed_sql_query"]
        == "SELECT * FROM emp"
    )


async def _advance(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Move past the scan interval so the coordinator polls again.

    The coordinator schedules its refresh as a background task, so the wait
    has to include those.
    """
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_becomes_unavailable_on_database_error(
    hass: HomeAssistant, mock_execute, freezer: FrozenDateTimeFactory
) -> None:
    """A failing poll marks the sensor unavailable instead of keeping stale data."""
    await _setup(hass)
    assert hass.states.get(ENTITY_ID).state == "2"

    mock_execute.side_effect = MySQLConnectionError("server gone")
    await _advance(hass, freezer)

    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE


async def test_recovers_after_database_error(
    hass: HomeAssistant, mock_execute, freezer: FrozenDateTimeFactory
) -> None:
    """The sensor recovers on its own once the database answers again."""
    await _setup(hass)

    mock_execute.side_effect = MySQLConnectionError("server gone")
    await _advance(hass, freezer)
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE

    mock_execute.side_effect = None
    await _advance(hass, freezer)
    assert hass.states.get(ENTITY_ID).state == "2"


async def test_no_entity_when_first_query_fails(
    hass: HomeAssistant, mock_execute
) -> None:
    """A database that is down at startup postpones the platform setup."""
    mock_execute.side_effect = MySQLConnectionError("server gone")

    await _setup(hass)

    assert hass.states.get(ENTITY_ID) is None


async def test_missing_component_config(hass: HomeAssistant, mock_execute) -> None:
    """A sensor without an ha_mysql: section is reported, not crashed on."""
    assert await async_setup_component(hass, "sensor", {"sensor": CONFIG["sensor"]})
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID) is None


async def test_empty_result_set(hass: HomeAssistant, mock_execute) -> None:
    """An empty result set gives state 0 and no selected row."""
    mock_execute.return_value = []

    await _setup(hass)

    state = hass.states.get(ENTITY_ID)
    assert state.state == "0"
    assert state.attributes["selected_row"] == -1
    assert state.attributes["json_result"] == "{}"
