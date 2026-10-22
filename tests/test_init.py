"""Tests for setting up and importing the HA MySQL integration."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from .conftest import (
    CONFIG,
    CONNECTION,
    ENTITY_ID,
    UNIQUE_ID,
    driver_error,
    make_entry,
    make_sensor,
    setup_entry,
)
from custom_components.ha_mysql.const import (
    DOMAIN,
    SERVICE_SELECT_RECORD,
    SERVICE_SET_QUERY,
)
from custom_components.ha_mysql.coordinator import (
    MySQLConnectionError,
    TLSUnavailableError,
)


async def _import_yaml(hass: HomeAssistant, config: dict | None = None) -> None:
    """Set up the integration from configuration.yaml."""
    assert await async_setup_component(hass, DOMAIN, config or CONFIG)
    await hass.async_block_till_done()


async def test_yaml_creates_config_entry(hass: HomeAssistant, mock_execute) -> None:
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


async def test_yaml_skips_a_mutating_sensor(hass: HomeAssistant, mock_execute) -> None:
    """A sensor with a write in its query is skipped, not polled forever."""
    config = {
        **CONFIG,
        "sensor": [
            {"platform": "ha_mysql", "name": "Broken", "query": "DELETE FROM emp"},
            {"platform": "ha_mysql", "name": "Employees", "query": "SELECT 1"},
        ],
    }
    await _import_yaml(hass, config)

    sensors = hass.config_entries.async_entries(DOMAIN)[0].options["sensors"]
    assert [sensor["name"] for sensor in sensors] == ["Employees"]


async def test_yaml_ignores_other_platforms(hass: HomeAssistant, mock_execute) -> None:
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


async def test_yaml_refreshes_existing_entry(hass: HomeAssistant, mock_execute) -> None:
    """A changed query in configuration.yaml reaches the existing entry.

    The entry is loaded again by the import itself, so the sensor runs the
    new query right away instead of after the next restart.
    """
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
    assert entry.state is ConfigEntryState.LOADED
    assert (
        hass.states.get(ENTITY_ID).attributes["executed_sql_query"]
        == "SELECT * FROM emp WHERE active = 1"
    )


async def test_yaml_import_without_changes_does_not_reload(
    hass: HomeAssistant, mock_execute
) -> None:
    """An unchanged sensor list leaves the loaded entry alone."""
    entry = make_entry([make_sensor(source="yaml")])
    await setup_entry(hass, entry)
    mock_execute.reset_mock()

    await _run_import(hass, [make_sensor(source="yaml")])

    assert entry.state is ConfigEntryState.LOADED
    assert mock_execute.call_count == 0


async def test_yaml_raises_a_repair_issue(hass: HomeAssistant, mock_execute) -> None:
    """configuration.yaml still works, and Repairs says it is deprecated."""
    await _import_yaml(hass)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, "deprecated_yaml")
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_key == "deprecated_yaml"


async def test_no_repair_issue_without_yaml(hass: HomeAssistant, mock_execute) -> None:
    """A connection set up through the user interface raises no issue."""
    await setup_entry(hass, make_entry())

    assert ir.async_get(hass).async_get_issue(DOMAIN, "deprecated_yaml") is None


async def test_actions_are_registered_without_a_connection(
    hass: HomeAssistant,
) -> None:
    """The actions belong to the integration, not to a loaded connection."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    assert hass.services.has_service(DOMAIN, SERVICE_SET_QUERY)
    assert hass.services.has_service(DOMAIN, SERVICE_SELECT_RECORD)


async def test_yaml_import_does_not_revert_a_connection_fixed_through_the_ui(
    hass: HomeAssistant, mock_execute
) -> None:
    """A stale configuration.yaml never reverts a connection fixed via Configure.

    Regression test: an entry's connection used to be re-imported from YAML
    on every restart, so correcting a wrong password through Change the
    database connection was silently undone the next time Home Assistant
    started while the old value was still sitting in configuration.yaml.
    """
    entry = MockConfigEntry(
        domain="ha_mysql",
        title="testdb @ db.local",
        data={**CONNECTION, "password": "corrected-through-the-ui"},
        options={"sensors": [make_sensor()]},
        unique_id=UNIQUE_ID,
    )
    await setup_entry(hass, entry)

    # CONFIG's password ("secret") is now stale compared to the entry's.
    await _import_yaml(hass)

    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert entry.data["password"] == "corrected-through-the-ui"


async def test_yaml_import_keeps_ui_sensors(hass: HomeAssistant, mock_execute) -> None:
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
        [
            make_sensor(source="yaml"),
            make_sensor(name="Gone", unique_id="x", source="yaml"),
        ]
    )
    await setup_entry(hass, entry)

    await _run_import(hass, [make_sensor(source="yaml")])

    assert [sensor["name"] for sensor in entry.options["sensors"]] == ["Employees"]


@pytest.mark.parametrize(
    ("error", "state", "translation_key"),
    [
        (
            driver_error(2003, "Can't connect to MySQL server"),
            ConfigEntryState.SETUP_RETRY,
            "cannot_connect",
        ),
        (
            driver_error(1203, "User has exceeded max_user_connections"),
            ConfigEntryState.SETUP_RETRY,
            "cannot_connect",
        ),
        (
            driver_error(1049, "Unknown database 'testdb'"),
            ConfigEntryState.SETUP_ERROR,
            "unknown_database",
        ),
        (
            driver_error(1130, "Host 'ha.local' is not allowed to connect"),
            ConfigEntryState.SETUP_ERROR,
            "connection_refused",
        ),
        (
            TLSUnavailableError("not encrypted"),
            ConfigEntryState.SETUP_ERROR,
            "tls_unavailable",
        ),
    ],
)
async def test_setup_failure_decides_what_happens_next(
    hass: HomeAssistant,
    mock_execute,
    error: Exception,
    state: ConfigEntryState,
    translation_key: str,
) -> None:
    """A server that cannot be reached is retried; any other refusal is not.

    Retrying a refusal that waiting does not fix would go on forever, so
    those fail the entry, with a message that says what to change.
    """
    mock_execute.side_effect = error
    entry = make_entry()
    await setup_entry(hass, entry)

    assert entry.state is state
    assert entry.error_reason_translation_key == translation_key
    assert hass.states.get(ENTITY_ID) is None
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_refused_credentials_start_the_sign_in_flow(
    hass: HomeAssistant, mock_execute
) -> None:
    """Wrong credentials fail the entry and ask for new ones."""
    mock_execute.side_effect = driver_error(1045, "Access denied for user 'user'")
    entry = make_entry()
    await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.error_reason_translation_key == "invalid_auth"
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_an_unreachable_server_is_retried(
    hass: HomeAssistant, mock_execute
) -> None:
    """An error without a MySQL code is a server that cannot be reached."""
    mock_execute.side_effect = MySQLConnectionError("server gone")
    entry = make_entry()
    await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_RETRY


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


async def test_sensor_platform_without_component_config(
    hass: HomeAssistant, mock_execute
) -> None:
    """A sensor platform without an ha_mysql: section is reported, not crashed on."""
    assert await async_setup_component(hass, "sensor", {"sensor": CONFIG["sensor"]})
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID) is None
