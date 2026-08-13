"""Tests for setting up and importing the HA MySQL integration."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.ha_mysql.const import DOMAIN
from custom_components.ha_mysql.coordinator import (
    MySQLConnectionError,
    MySQLQueryError,
)

from .conftest import (
    CONFIG,
    CONNECTION,
    ENTITY_ID,
    UNIQUE_ID,
    make_entry,
    make_sensor,
    setup_entry,
)


async def _import_yaml(hass: HomeAssistant, config: dict | None = None) -> None:
    """Set up the integration from configuration.yaml."""
    assert await async_setup_component(hass, DOMAIN, config or CONFIG)
    await hass.async_block_till_done()


async def test_yaml_creates_config_entry(
    hass: HomeAssistant, mock_execute
) -> None:
    """configuration.yaml is imported into a config entry."""
    await _import_yaml(hass)

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.unique_id == UNIQUE_ID
    assert entry.state is ConfigEntryState.LOADED
    # The port that was left out of the YAML falls back to the default.
    assert entry.data["port"] == 3306

    sensors = entry.options["sensors"]
    assert len(sensors) == 1
    assert sensors[0]["name"] == "Employees"
    assert sensors[0]["query"] == "SELECT * FROM emp"
    assert sensors[0]["source"] == "yaml"
    # Existing installations keep their entity ID and history.
    assert sensors[0]["unique_id"] == "ha_mysql_employees"


async def test_yaml_creates_entities(hass: HomeAssistant, mock_execute) -> None:
    """The imported sensors show up as entities."""
    await _import_yaml(hass)

    assert hass.states.get(ENTITY_ID).state == "2"


async def test_yaml_options_are_read(hass: HomeAssistant, mock_execute) -> None:
    """Optional YAML settings end up in the config entry."""
    config = {
        **CONFIG,
        "sensor": [
            {
                "platform": "ha_mysql",
                "name": "Salary",
                "query": "SELECT * FROM emp",
                "scan_interval": 300,
                "value_column": "salary",
                "unit_of_measurement": "EUR",
                "state_class": "measurement",
                "suggested_display_precision": 2,
                "max_json_rows": 50,
            }
        ],
    }
    await _import_yaml(hass, config)

    sensor = hass.config_entries.async_entries(DOMAIN)[0].options["sensors"][0]
    assert sensor["scan_interval"] == 300
    assert sensor["value_column"] == "salary"
    assert sensor["unit_of_measurement"] == "EUR"
    assert sensor["state_class"] == "measurement"
    assert sensor["suggested_display_precision"] == 2
    assert sensor["max_json_rows"] == 50


async def test_yaml_skips_invalid_sensor(hass: HomeAssistant, mock_execute) -> None:
    """A sensor without a query is skipped instead of breaking the setup."""
    config = {
        **CONFIG,
        "sensor": [
            {"platform": "ha_mysql", "name": "Broken"},
            {"platform": "ha_mysql", "name": "Employees", "query": "SELECT 1"},
        ],
    }
    await _import_yaml(hass, config)

    sensors = hass.config_entries.async_entries(DOMAIN)[0].options["sensors"]
    assert [sensor["name"] for sensor in sensors] == ["Employees"]


async def test_yaml_ignores_other_platforms(
    hass: HomeAssistant, mock_execute
) -> None:
    """Sensors of other integrations are left alone."""
    config = {
        **CONFIG,
        "sensor": [
            {"platform": "template", "name": "Something else"},
            {"platform": "ha_mysql", "name": "Employees", "query": "SELECT 1"},
        ],
    }
    await _import_yaml(hass, config)

    sensors = hass.config_entries.async_entries(DOMAIN)[0].options["sensors"]
    assert [sensor["name"] for sensor in sensors] == ["Employees"]


async def _run_import(hass: HomeAssistant, sensors: list[dict]) -> None:
    """Run the import step the way a restart with YAML would."""
    await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_IMPORT},
        data={"connection": CONNECTION, "sensors": sensors},
    )
    await hass.async_block_till_done()


async def test_yaml_refreshes_existing_entry(
    hass: HomeAssistant, mock_execute
) -> None:
    """A changed query in configuration.yaml reaches the existing entry."""
    entry = make_entry([make_sensor(source="yaml")])
    await setup_entry(hass, entry)

    await _run_import(
        hass,
        [make_sensor(query="SELECT * FROM emp WHERE active = 1", source="yaml")],
    )

    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    sensors = entry.options["sensors"]
    assert len(sensors) == 1
    assert sensors[0]["query"] == "SELECT * FROM emp WHERE active = 1"


async def test_yaml_import_keeps_ui_sensors(
    hass: HomeAssistant, mock_execute
) -> None:
    """Sensors added through the interface survive a YAML import."""
    # No source marker, so this one was added through the user interface.
    entry = make_entry([make_sensor(name="From UI", unique_id="abc123")])
    await setup_entry(hass, entry)

    await _run_import(hass, [make_sensor(source="yaml")])

    names = {sensor["name"] for sensor in entry.options["sensors"]}
    assert names == {"Employees", "From UI"}


async def test_yaml_import_drops_removed_yaml_sensors(
    hass: HomeAssistant, mock_execute
) -> None:
    """A sensor taken out of configuration.yaml disappears on the next start."""
    entry = make_entry(
        [make_sensor(source="yaml"), make_sensor(name="Gone", unique_id="x", source="yaml")]
    )
    await setup_entry(hass, entry)

    await _run_import(hass, [make_sensor(source="yaml")])

    assert [sensor["name"] for sensor in entry.options["sensors"]] == ["Employees"]


async def test_entry_not_ready_when_unreachable(
    hass: HomeAssistant, mock_execute
) -> None:
    """An unreachable database postpones the setup so it can retry."""
    mock_execute.side_effect = MySQLConnectionError("server gone")
    entry = make_entry()
    await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert hass.states.get(ENTITY_ID) is None


async def test_entry_error_on_bad_credentials(
    hass: HomeAssistant, mock_execute
) -> None:
    """Wrong credentials fail the entry instead of retrying forever."""
    mock_execute.side_effect = MySQLQueryError("Access denied", 1045)
    entry = make_entry()
    await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_unload_entry(hass: HomeAssistant, mock_execute) -> None:
    """Unloading removes the entities and closes the connections."""
    entry = make_entry()
    await setup_entry(hass, entry)
    assert hass.states.get(ENTITY_ID) is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    # Home Assistant keeps a restored placeholder for the registered entity.
    state = hass.states.get(ENTITY_ID)
    assert state.state == "unavailable"
    assert state.attributes["restored"] is True


async def test_options_update_reloads(hass: HomeAssistant, mock_execute) -> None:
    """Changing the options rebuilds the sensors."""
    entry = make_entry()
    await setup_entry(hass, entry)

    hass.config_entries.async_update_entry(
        entry, options={"sensors": [make_sensor(name="Departments", unique_id="dept")]}
    )
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID) is None
    assert hass.states.get("sensor.departments") is not None


async def test_sensor_platform_without_component_config(
    hass: HomeAssistant, mock_execute
) -> None:
    """A sensor platform without an ha_mysql: section is reported, not crashed on."""
    assert await async_setup_component(hass, "sensor", {"sensor": CONFIG["sensor"]})
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID) is None
