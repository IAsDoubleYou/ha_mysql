"""Tests for setting up and importing the HA MySQL integration."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from .conftest import (
    CONNECTION,
    ENTITY_ID,
    UNIQUE_ID,
    driver_error,
    make_entry,
    make_sensor,
    setup_entry,
)
from custom_components.ha_mysql import async_migrate_entry
from custom_components.ha_mysql.const import (
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    SERVICE_SELECT_RECORD,
    SERVICE_SET_QUERY,
)
from custom_components.ha_mysql.coordinator import (
    MySQLConnectionError,
    TLSUnavailableError,
)


async def test_actions_are_registered_without_a_connection(
    hass: HomeAssistant,
) -> None:
    """The actions belong to the integration, not to a loaded connection."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    assert hass.services.has_service(DOMAIN, SERVICE_SET_QUERY)
    assert hass.services.has_service(DOMAIN, SERVICE_SELECT_RECORD)


async def test_a_yaml_section_creates_nothing(
    hass: HomeAssistant, mock_execute
) -> None:
    """configuration.yaml is no longer read: Home Assistant reports the section.

    Release 1.7.0 still imported it and raised a Repairs issue. A connection
    that was imported then lives on in its config entry, and a section that is
    still in the file does not create another one.
    """
    config = {
        DOMAIN: {
            "host": "db.local",
            "username": "user",
            "password": "secret",
            "database": "testdb",
        },
        "sensor": [{"platform": DOMAIN, "name": "Employees", "query": "SELECT 1"}],
    }

    await async_setup_component(hass, DOMAIN, config)
    await hass.async_block_till_done()

    assert hass.config_entries.async_entries(DOMAIN) == []
    assert hass.states.async_entity_ids("sensor") == []


async def test_the_deprecation_issue_of_release_1_7_0_is_removed(
    hass: HomeAssistant,
) -> None:
    """Repairs stops showing the issue that 1.7.0 raised for the YAML section."""
    registry = ir.async_get(hass)
    ir.async_create_issue(
        hass,
        DOMAIN,
        "deprecated_yaml",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="deprecated_yaml",
    )
    assert registry.async_get_issue(DOMAIN, "deprecated_yaml") is not None

    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    assert registry.async_get_issue(DOMAIN, "deprecated_yaml") is None


async def test_migration_drops_the_yaml_source_marker(
    hass: HomeAssistant, mock_execute
) -> None:
    """Sensors imported from YAML by an earlier release lose their marker.

    Everything else about them stays, the unique ID above all: it is what
    keeps the entity ID and the history.
    """
    imported = make_sensor(source="yaml")
    added_in_the_ui = make_sensor(name="From UI", unique_id="abc123")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="testdb @ db.local",
        data=CONNECTION,
        options={"sensors": [imported, added_in_the_ui], "other": "kept"},
        unique_id=UNIQUE_ID,
        version=1,
    )

    await setup_entry(hass, entry)

    assert entry.version == CONFIG_ENTRY_VERSION
    assert entry.state is ConfigEntryState.LOADED
    assert entry.options["other"] == "kept"
    assert entry.options["sensors"] == [
        make_sensor(),
        make_sensor(name="From UI", unique_id="abc123"),
    ]
    assert hass.states.get(ENTITY_ID) is not None


async def test_migration_handles_an_entry_without_sensors(
    hass: HomeAssistant, mock_execute
) -> None:
    """An entry whose sensors were all removed migrates as well."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="testdb @ db.local",
        data=CONNECTION,
        options={},
        unique_id=UNIQUE_ID,
        version=1,
    )

    await setup_entry(hass, entry)

    assert entry.version == CONFIG_ENTRY_VERSION
    assert entry.options == {"sensors": []}
    assert entry.state is ConfigEntryState.LOADED


async def test_an_entry_from_a_newer_release_is_not_loaded(
    hass: HomeAssistant, mock_execute
) -> None:
    """A downgrade is refused instead of reading data it does not know."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="testdb @ db.local",
        data=CONNECTION,
        options={"sensors": [make_sensor()]},
        unique_id=UNIQUE_ID,
        version=CONFIG_ENTRY_VERSION + 1,
    )

    await setup_entry(hass, entry)

    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    assert entry.version == CONFIG_ENTRY_VERSION + 1


async def test_the_migration_itself_refuses_a_newer_entry(hass: HomeAssistant) -> None:
    """Whatever Home Assistant does first, the migration never touches the data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=CONNECTION,
        options={"sensors": [make_sensor(source="yaml")]},
        version=CONFIG_ENTRY_VERSION + 1,
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is False
    assert entry.options["sensors"] == [make_sensor(source="yaml")]


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


async def test_a_yaml_sensor_platform_creates_nothing(
    hass: HomeAssistant, mock_execute
) -> None:
    """A sensor with platform: ha_mysql is reported by Home Assistant, not set up."""
    config = {
        "sensor": [{"platform": DOMAIN, "name": "Employees", "query": "SELECT 1"}]
    }

    assert await async_setup_component(hass, "sensor", config)
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID) is None
