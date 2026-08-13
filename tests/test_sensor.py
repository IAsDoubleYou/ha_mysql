"""Tests for the HA MySQL sensor platform."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.ha_mysql.const import (
    DOMAIN,
    SERVICE_SELECT_RECORD,
    SERVICE_SET_QUERY,
)
from custom_components.ha_mysql.coordinator import MySQLConnectionError

from .conftest import ENTITY_ID, make_entry, make_sensor, setup_entry


async def _advance(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Move past the scan interval so the coordinator polls again.

    The coordinator schedules its refresh as a background task, so the wait
    has to include those.
    """
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_state_is_row_count(hass: HomeAssistant, mock_execute) -> None:
    """Without a value column the state holds the number of rows."""
    await setup_entry(hass, make_entry())

    assert hass.states.get(ENTITY_ID).state == "2"


async def test_attributes_of_first_row(hass: HomeAssistant, mock_execute) -> None:
    """The columns of the first row are exposed with the valueof_ prefix."""
    await setup_entry(hass, make_entry())

    attributes = hass.states.get(ENTITY_ID).attributes
    assert attributes["valueof_name"] == "Alice"
    assert attributes["valueof_salary"] == "1000.50"
    assert attributes["selected_row"] == 0
    assert attributes["row_count"] == 2
    assert attributes["executed_sql_query"] == "SELECT * FROM emp"
    assert "query_date" in attributes
    assert "query_time" in attributes


async def test_unique_id_is_stable(hass: HomeAssistant, mock_execute) -> None:
    """The unique ID keeps the format used by earlier releases."""
    await setup_entry(hass, make_entry())

    entry = er.async_get(hass).async_get(ENTITY_ID)
    assert entry is not None
    assert entry.unique_id == "ha_mysql_employees"


async def test_sensor_is_linked_to_a_device(
    hass: HomeAssistant, mock_execute
) -> None:
    """Every sensor of a connection sits under one database device."""
    config_entry = make_entry()
    await setup_entry(hass, config_entry)

    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, config_entry.entry_id)}
    )
    assert device is not None
    assert device.name == "testdb @ db.local"

    entity = er.async_get(hass).async_get(ENTITY_ID)
    assert entity.device_id == device.id


async def test_friendly_name_is_unchanged(
    hass: HomeAssistant, mock_execute
) -> None:
    """The device does not creep into the name of existing sensors."""
    await setup_entry(hass, make_entry())

    assert hass.states.get(ENTITY_ID).attributes["friendly_name"] == "Employees"


async def test_value_column(hass: HomeAssistant, mock_execute) -> None:
    """A value column puts a value from the result set in the state."""
    await setup_entry(hass, make_entry([make_sensor(value_column="name")]))

    assert hass.states.get(ENTITY_ID).state == "Alice"


async def test_value_column_numeric(hass: HomeAssistant, mock_execute) -> None:
    """A value column with a unit is reported as a number."""
    await setup_entry(
        hass,
        make_entry(
            [
                make_sensor(
                    value_column="salary",
                    unit_of_measurement="EUR",
                    state_class="measurement",
                    suggested_display_precision=2,
                )
            ]
        ),
    )

    state = hass.states.get(ENTITY_ID)
    assert state.state == "1000.5"
    assert state.attributes["unit_of_measurement"] == "EUR"
    assert state.attributes["state_class"] == "measurement"


async def test_value_column_missing(hass: HomeAssistant, mock_execute) -> None:
    """An unknown column gives an unknown state instead of an error."""
    await setup_entry(hass, make_entry([make_sensor(value_column="nope")]))

    assert hass.states.get(ENTITY_ID).state == "unknown"


async def test_value_template(hass: HomeAssistant, mock_execute) -> None:
    """A value template can build the state from the result set."""
    await setup_entry(
        hass,
        make_entry([make_sensor(value_template="{{ row.name }} of {{ row_count }}")]),
    )

    assert hass.states.get(ENTITY_ID).state == "Alice of 2"


async def test_value_template_beats_column(
    hass: HomeAssistant, mock_execute
) -> None:
    """The template wins when both a column and a template are configured."""
    await setup_entry(
        hass,
        make_entry(
            [make_sensor(value_column="name", value_template="{{ rows | length }}")]
        ),
    )

    assert hass.states.get(ENTITY_ID).state == "2"


async def test_value_template_follows_selected_row(
    hass: HomeAssistant, mock_execute
) -> None:
    """The template sees the row that select_record picked."""
    await setup_entry(hass, make_entry([make_sensor(value_template="{{ row.name }}")]))

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SELECT_RECORD,
        {"entity_id": ENTITY_ID, "rownumber": 1},
        blocking=True,
    )

    assert hass.states.get(ENTITY_ID).state == "Bob"


async def test_unknown_device_class_is_ignored(
    hass: HomeAssistant, mock_execute
) -> None:
    """A typo in the device class does not break the sensor."""
    await setup_entry(hass, make_entry([make_sensor(device_class="not_a_class")]))

    state = hass.states.get(ENTITY_ID)
    assert state.state == "2"
    assert "device_class" not in state.attributes


async def test_select_record_switches_row(
    hass: HomeAssistant, mock_execute
) -> None:
    """select_record exposes the columns of the requested row."""
    await setup_entry(hass, make_entry())

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SELECT_RECORD,
        {"entity_id": ENTITY_ID, "rownumber": 1},
        blocking=True,
    )

    attributes = hass.states.get(ENTITY_ID).attributes
    assert attributes["valueof_name"] == "Bob"
    assert attributes["selected_row"] == 1


async def test_select_record_out_of_range(
    hass: HomeAssistant, mock_execute
) -> None:
    """A row beyond the result set falls back to the first row."""
    await setup_entry(hass, make_entry())

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
    await setup_entry(hass, make_entry())
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
    await setup_entry(hass, make_entry())

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


async def test_becomes_unavailable_on_database_error(
    hass: HomeAssistant, mock_execute, freezer: FrozenDateTimeFactory
) -> None:
    """A failing poll marks the sensor unavailable instead of keeping stale data."""
    await setup_entry(hass, make_entry())
    assert hass.states.get(ENTITY_ID).state == "2"

    mock_execute.side_effect = MySQLConnectionError("server gone")
    await _advance(hass, freezer)

    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE


async def test_recovers_after_database_error(
    hass: HomeAssistant, mock_execute, freezer: FrozenDateTimeFactory
) -> None:
    """The sensor recovers on its own once the database answers again."""
    await setup_entry(hass, make_entry())

    mock_execute.side_effect = MySQLConnectionError("server gone")
    await _advance(hass, freezer)
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE

    mock_execute.side_effect = None
    await _advance(hass, freezer)
    assert hass.states.get(ENTITY_ID).state == "2"


async def test_one_broken_query_keeps_others_running(
    hass: HomeAssistant, mock_execute
) -> None:
    """A sensor with a failing query does not take the rest down."""
    rows = list(mock_execute.return_value)

    def _execute(self, query):
        if "broken" in query:
            raise MySQLConnectionError("nope")
        return list(rows)

    mock_execute.side_effect = _execute

    await setup_entry(
        hass,
        make_entry(
            [
                make_sensor(),
                make_sensor(
                    name="Broken", query="SELECT broken", unique_id="ha_mysql_broken"
                ),
            ]
        ),
    )

    assert hass.states.get(ENTITY_ID).state == "2"
    assert hass.states.get("sensor.broken").state == STATE_UNAVAILABLE


async def test_empty_result_set(hass: HomeAssistant, mock_execute) -> None:
    """An empty result set gives state 0 and no selected row."""
    mock_execute.return_value = []
    await setup_entry(hass, make_entry())

    state = hass.states.get(ENTITY_ID)
    assert state.state == "0"
    assert state.attributes["selected_row"] == -1
    assert state.attributes["json_result"] == "{}"


async def test_max_json_rows_truncates(hass: HomeAssistant, mock_execute) -> None:
    """A row limit keeps the json_result attribute small."""
    await setup_entry(hass, make_entry([make_sensor(max_json_rows=1)]))

    state = hass.states.get(ENTITY_ID)
    assert state.state == "2"
    assert state.attributes["json_result_truncated"] is True
    assert "Bob" not in state.attributes["json_result"]


async def test_removed_sensor_is_cleaned_up(
    hass: HomeAssistant, mock_execute
) -> None:
    """An entity of a deleted sensor does not linger in the registry."""
    config_entry = make_entry()
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "ha_mysql_old",
        suggested_object_id="old",
        config_entry=config_entry,
    )

    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get("sensor.old") is None
    assert registry.async_get(ENTITY_ID) is not None
