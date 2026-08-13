"""The HA MySQL integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_NAME,
    CONF_PLATFORM,
    CONF_SCAN_INTERVAL,
    CONF_UNIT_OF_MEASUREMENT,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_CONNECTION,
    CONF_MAX_JSON_ROWS,
    CONF_MYSQL_DATABASE,
    CONF_MYSQL_HOST,
    CONF_MYSQL_PASSWORD,
    CONF_MYSQL_PORT,
    CONF_MYSQL_USERNAME,
    CONF_QUERY,
    CONF_SENSORS,
    CONF_SOURCE,
    CONF_STATE_CLASS,
    CONF_SUGGESTED_DISPLAY_PRECISION,
    CONF_UNIQUE_ID,
    CONF_VALUE_COLUMN,
    CONF_VALUE_TEMPLATE,
    DATA_YAML_IMPORTED,
    DEFAULT_MAX_JSON_ROWS,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    PLATFORMS,
    SOURCE_YAML,
)
from .coordinator import (
    MySQLConnectionError,
    MySQLConnectionManager,
    MySQLQueryError,
)
from .helpers import generate_unique_id

_LOGGER = logging.getLogger(__name__)

type HAMySQLConfigEntry = ConfigEntry[MySQLConnectionManager]

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_MYSQL_HOST): cv.string,
                vol.Optional(CONF_MYSQL_PORT, default=DEFAULT_PORT): cv.port,
                vol.Required(CONF_MYSQL_USERNAME): cv.string,
                vol.Required(CONF_MYSQL_PASSWORD): cv.string,
                vol.Required(CONF_MYSQL_DATABASE): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

# Sensors in configuration.yaml are read straight from the sensor domain, so
# they can be imported together with the connection settings.
YAML_SENSOR_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PLATFORM): cv.string,
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_QUERY): cv.string,
        vol.Optional(CONF_SCAN_INTERVAL): cv.time_period,
        vol.Optional(CONF_MAX_JSON_ROWS, default=DEFAULT_MAX_JSON_ROWS): cv.positive_int,
        vol.Optional(CONF_VALUE_COLUMN): cv.string,
        vol.Optional(CONF_VALUE_TEMPLATE): cv.template,
        vol.Optional(CONF_UNIT_OF_MEASUREMENT): cv.string,
        vol.Optional(CONF_DEVICE_CLASS): cv.string,
        vol.Optional(CONF_STATE_CLASS): cv.string,
        vol.Optional(CONF_SUGGESTED_DISPLAY_PRECISION): cv.positive_int,
    },
    extra=vol.ALLOW_EXTRA,
)


def _yaml_sensors(config: ConfigType) -> list[dict[str, Any]]:
    """Collect the ha_mysql sensor platform entries from configuration.yaml."""
    sensors: list[dict[str, Any]] = []

    for platform_config in cv.ensure_list(config.get(Platform.SENSOR, [])):
        if (
            not isinstance(platform_config, dict)
            or platform_config.get(CONF_PLATFORM) != DOMAIN
        ):
            continue

        try:
            validated = YAML_SENSOR_SCHEMA(dict(platform_config))
        except vol.Invalid as err:
            _LOGGER.error(
                "Skipping invalid ha_mysql sensor in configuration.yaml: %s", err
            )
            continue

        scan_interval = validated.get(CONF_SCAN_INTERVAL)
        sensors.append(
            {
                CONF_NAME: validated[CONF_NAME],
                CONF_QUERY: validated[CONF_QUERY],
                CONF_SCAN_INTERVAL: (
                    int(scan_interval.total_seconds())
                    if scan_interval is not None
                    else DEFAULT_SCAN_INTERVAL_SECONDS
                ),
                CONF_MAX_JSON_ROWS: validated[CONF_MAX_JSON_ROWS],
                CONF_VALUE_COLUMN: validated.get(CONF_VALUE_COLUMN),
                # Templates are stored as plain text in the config entry.
                CONF_VALUE_TEMPLATE: (
                    template.template
                    if (template := validated.get(CONF_VALUE_TEMPLATE)) is not None
                    else None
                ),
                CONF_UNIT_OF_MEASUREMENT: validated.get(CONF_UNIT_OF_MEASUREMENT),
                CONF_DEVICE_CLASS: validated.get(CONF_DEVICE_CLASS),
                CONF_STATE_CLASS: validated.get(CONF_STATE_CLASS),
                CONF_SUGGESTED_DISPLAY_PRECISION: validated.get(
                    CONF_SUGGESTED_DISPLAY_PRECISION
                ),
                # Keeps the entity ID and the history of existing installations.
                CONF_UNIQUE_ID: generate_unique_id(validated[CONF_NAME]),
                CONF_SOURCE: SOURCE_YAML,
            }
        )

    return sensors


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Import the settings from configuration.yaml, if there are any."""
    if (conf := config.get(DOMAIN)) is None:
        return True

    hass.data.setdefault(DOMAIN, {})[DATA_YAML_IMPORTED] = True

    sensors = _yaml_sensors(config)
    if not sensors:
        _LOGGER.warning(
            "ha_mysql is configured in configuration.yaml but no sensor platform "
            "entries were found; add them under sensor: or through the user interface"
        )

    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data={CONF_CONNECTION: dict(conf), CONF_SENSORS: sensors},
        )
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HAMySQLConfigEntry) -> bool:
    """Set up a MySQL connection and its sensors."""
    manager = MySQLConnectionManager(dict(entry.data))

    try:
        await hass.async_add_executor_job(manager.test_connection)
    except MySQLQueryError as err:
        # Wrong credentials or a missing database will not fix themselves.
        raise ConfigEntryError(str(err)) from err
    except MySQLConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = manager
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HAMySQLConfigEntry) -> bool:
    """Unload a MySQL connection and close its pooled connections."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await hass.async_add_executor_job(entry.runtime_data.close)
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: HAMySQLConfigEntry) -> None:
    """Reload the entry after its options changed."""
    await hass.config_entries.async_reload(entry.entry_id)
