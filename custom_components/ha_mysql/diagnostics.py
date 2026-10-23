"""Diagnostics of the HA MySQL integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import HAMySQLConfigEntry
from .const import (
    ATTR_EXECUTED_QUERY,
    ATTR_JSON_TRUNCATED,
    ATTR_QUERY_DATE,
    ATTR_QUERY_TIME,
    ATTR_ROW_COUNT,
    CONF_MYSQL_DATABASE,
    CONF_MYSQL_HOST,
    CONF_MYSQL_PASSWORD,
    CONF_MYSQL_PORT,
    CONF_MYSQL_USERNAME,
    CONF_SENSORS,
    CONF_UNIQUE_ID,
    DOMAIN,
)

# Everything that says where the database is or how to log in. The title and
# the unique ID of the entry are built from the host, the port and the database
# name, so they are masked as well.
TO_REDACT_CONNECTION = {
    CONF_MYSQL_HOST,
    CONF_MYSQL_PORT,
    CONF_MYSQL_USERNAME,
    CONF_MYSQL_PASSWORD,
    CONF_MYSQL_DATABASE,
}

# The attributes of a sensor that describe the last run without carrying data
# from the database. The state, json_result and the valueof_ attributes do, so
# they are left out.
SENSOR_ATTRIBUTES = (
    ATTR_EXECUTED_QUERY,
    ATTR_QUERY_DATE,
    ATTR_QUERY_TIME,
    ATTR_ROW_COUNT,
    ATTR_JSON_TRUNCATED,
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HAMySQLConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    The connection details and the credentials are masked, and no row of the
    database is included. The entity IDs are left out too: they are built from
    the database name and the host. The queries are: they are part of the configuration
    and usually what needs looking at, so read them before sharing the file.
    """
    registry = er.async_get(hass)
    sensors: list[dict[str, Any]] = []
    for config in entry.options.get(CONF_SENSORS, []):
        entity_id = registry.async_get_entity_id(
            SENSOR_DOMAIN, DOMAIN, config[CONF_UNIQUE_ID]
        )
        state = hass.states.get(entity_id) if entity_id else None
        sensors.append(
            {
                "config": dict(config),
                "available": state is not None and state.state != STATE_UNAVAILABLE,
                "last_run": (
                    {
                        key: state.attributes[key]
                        for key in SENSOR_ATTRIBUTES
                        if key in state.attributes
                    }
                    if state is not None
                    else {}
                ),
            }
        )

    return {
        "entry": {
            "version": entry.version,
            "title": "**REDACTED**",
            "unique_id": "**REDACTED**",
            "data": async_redact_data(dict(entry.data), TO_REDACT_CONNECTION),
        },
        "pool": entry.runtime_data.pool_status
        if entry.state is ConfigEntryState.LOADED
        else None,
        "sensors": sensors,
    }
