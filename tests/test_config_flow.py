"""Tests for the HA MySQL config and options flow."""

from __future__ import annotations

import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData

from .conftest import CONNECTION, UNIQUE_ID, make_entry, make_sensor, setup_entry
from custom_components.ha_mysql.const import DOMAIN
from custom_components.ha_mysql.coordinator import MySQLConnectionError, MySQLQueryError

USER_INPUT = {
    "host": "db.local",
    "port": 3306,
    "username": "user",
    "password": "secret",
    "database": "testdb",
}

NEW_SENSOR = {
    "name": "Departments",
    "query": "SELECT * FROM dept",
    "scan_interval": 60,
    "max_json_rows": 0,
}


async def test_user_flow(hass: HomeAssistant, mock_execute) -> None:
    """A working connection results in a config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "testdb @ db.local"
    assert result["data"] == CONNECTION
    assert result["options"] == {"sensors": []}


async def test_user_flow_cannot_connect(hass: HomeAssistant, mock_execute) -> None:
    """An unreachable server is reported on the form."""
    mock_execute.side_effect = MySQLConnectionError("no route")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_invalid_auth(hass: HomeAssistant, mock_execute) -> None:
    """Wrong credentials are reported as such."""
    mock_execute.side_effect = MySQLQueryError("Access denied", 1045)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_unknown_database(hass: HomeAssistant, mock_execute) -> None:
    """A missing database is reported as such."""
    mock_execute.side_effect = MySQLQueryError("Unknown database", 1049)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown_database"}


async def test_user_flow_recovers_after_error(
    hass: HomeAssistant, mock_execute
) -> None:
    """The form can be submitted again once the problem is solved."""
    mock_execute.side_effect = MySQLConnectionError("no route")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["errors"] == {"base": "cannot_connect"}

    mock_execute.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_duplicate(hass: HomeAssistant, mock_execute) -> None:
    """The same database cannot be added twice."""
    entry = make_entry()
    entry.add_to_hass(hass)
    assert entry.unique_id == UNIQUE_ID

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_add_sensor(hass: HomeAssistant, mock_execute) -> None:
    """A sensor added through the options flow becomes an entity."""
    entry = make_entry([])
    await setup_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_sensor"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], NEW_SENSOR
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    sensors = entry.options["sensors"]
    assert len(sensors) == 1
    assert sensors[0]["name"] == "Departments"
    assert sensors[0]["scan_interval"] == 60
    assert sensors[0]["unique_id"]
    assert hass.states.get("sensor.departments") is not None


async def test_options_duplicate_name(hass: HomeAssistant, mock_execute) -> None:
    """Two sensors cannot share a name."""
    entry = make_entry()
    await setup_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_sensor"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**NEW_SENSOR, "name": "Employees"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"name": "name_exists"}


async def test_options_invalid_template(hass: HomeAssistant, mock_execute) -> None:
    """A broken value template is refused by the form itself."""
    entry = make_entry([])
    await setup_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_sensor"}
    )

    with pytest.raises(InvalidData) as err:
        await hass.config_entries.options.async_configure(
            result["flow_id"], {**NEW_SENSOR, "value_template": "{{ unclosed "}
        )

    assert "value_template" in str(err.value)
    assert entry.options["sensors"] == []


async def test_options_accepts_valid_template(
    hass: HomeAssistant, mock_execute
) -> None:
    """A correct value template is stored."""
    entry = make_entry([])
    await setup_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_sensor"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**NEW_SENSOR, "value_template": "{{ row.name }}"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["sensors"][0]["value_template"] == "{{ row.name }}"


async def test_options_edit_sensor(hass: HomeAssistant, mock_execute) -> None:
    """Editing a sensor keeps its unique ID."""
    entry = make_entry()
    await setup_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "select_sensor"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"unique_id": "ha_mysql_employees"}
    )
    assert result["step_id"] == "edit_sensor"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "name": "Employees",
            "query": "SELECT * FROM emp WHERE active = 1",
            "scan_interval": 120,
            "max_json_rows": 0,
        },
    )
    await hass.async_block_till_done()

    sensor = entry.options["sensors"][0]
    assert sensor["query"] == "SELECT * FROM emp WHERE active = 1"
    assert sensor["scan_interval"] == 120
    assert sensor["unique_id"] == "ha_mysql_employees"


async def test_options_remove_sensor(hass: HomeAssistant, mock_execute) -> None:
    """A removed sensor disappears from the options and from the states."""
    entry = make_entry(
        [make_sensor(), make_sensor(name="Departments", unique_id="dept")]
    )
    await setup_entry(hass, entry)
    assert hass.states.get("sensor.departments") is not None

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "remove_sensor"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"sensors": ["dept"]}
    )
    await hass.async_block_till_done()

    assert [sensor["name"] for sensor in entry.options["sensors"]] == ["Employees"]
    assert hass.states.get("sensor.departments") is None


async def test_options_menu_without_sensors(hass: HomeAssistant, mock_execute) -> None:
    """Editing and removing are hidden while there are no sensors."""
    entry = make_entry([])
    await setup_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["menu_options"] == ["add_sensor"]
