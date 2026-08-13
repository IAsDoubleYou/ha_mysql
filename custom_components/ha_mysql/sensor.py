"""Sensor platform for the HA MySQL integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import (
    PLATFORM_SCHEMA as SENSOR_PLATFORM_SCHEMA,
    SensorEntity,
)
from homeassistant.const import CONF_NAME, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_EXECUTED_QUERY,
    ATTR_JSON_RESULT,
    ATTR_JSON_TRUNCATED,
    ATTR_QUERY_DATE,
    ATTR_QUERY_TIME,
    ATTR_SELECTED_ROW,
    CONF_MAX_JSON_ROWS,
    CONF_QUERY,
    CONF_ROWNUMBER,
    DATA_MANAGER,
    DEFAULT_MAX_JSON_ROWS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SERVICE_SELECT_RECORD,
    SERVICE_SET_QUERY,
    VALUE_PREFIX,
)
from .coordinator import MySQLQueryCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = SENSOR_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_QUERY): cv.string,
        vol.Optional(CONF_MAX_JSON_ROWS, default=DEFAULT_MAX_JSON_ROWS): vol.All(
            vol.Coerce(int), vol.Range(min=0)
        ),
    }
)

SET_QUERY_SCHEMA = {vol.Optional(CONF_QUERY): cv.string}
SELECT_RECORD_SCHEMA = {
    vol.Required(CONF_ROWNUMBER): vol.All(vol.Coerce(int), vol.Range(min=0))
}


def generate_unique_id(name: str) -> str:
    """Generate a unique ID for the sensor."""
    return f"{DOMAIN}_{name.lower().replace(' ', '_')}"


def rename_keys(old_dict: dict[str, Any], prefix: str) -> dict[str, Any]:
    """Return a copy of the dict with every key prefixed."""
    return {f"{prefix}{key}": value for key, value in old_dict.items()}


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up a HA MySQL sensor from configuration.yaml."""
    data = hass.data.get(DOMAIN)
    if not data:
        _LOGGER.error(
            "No ha_mysql: section found in configuration.yaml; add the database "
            "settings before configuring a ha_mysql sensor"
        )
        return

    name: str = config[CONF_NAME]
    query: str = config[CONF_QUERY]
    scan_interval = config.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    coordinator = MySQLQueryCoordinator(
        hass,
        data[DATA_MANAGER],
        name,
        query,
        scan_interval,
        config[CONF_MAX_JSON_ROWS],
    )

    await coordinator.async_refresh()
    if not coordinator.last_update_success:
        # Home Assistant retries the platform setup with a growing backoff.
        raise PlatformNotReady(
            f"Initial query for {name} failed: {coordinator.last_exception}"
        )

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SET_QUERY, SET_QUERY_SCHEMA, "async_set_query"
    )
    platform.async_register_entity_service(
        SERVICE_SELECT_RECORD, SELECT_RECORD_SCHEMA, "async_select_record"
    )

    async_add_entities([HAMySQLSensor(coordinator, name)])


class HAMySQLSensor(CoordinatorEntity[MySQLQueryCoordinator], SensorEntity):
    """Sensor holding the result of a MySQL query.

    The state is the number of rows returned by the query. The columns of the
    selected row are exposed as attributes prefixed with `valueof_`.
    """

    _attr_icon = "mdi:database-search"

    def __init__(self, coordinator: MySQLQueryCoordinator, name: str) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = generate_unique_id(name)
        self._selected_row = 0

    @property
    def native_value(self) -> int | None:
        """Return the number of rows of the last successful query."""
        if (data := self.coordinator.data) is None:
            return None
        return data.row_count

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the query metadata and the columns of the selected row."""
        if (data := self.coordinator.data) is None:
            return {}

        attributes: dict[str, Any] = {}

        if data.rows:
            index = self._selected_row
            if index >= len(data.rows):
                _LOGGER.warning(
                    "Selected row %s is out of range for %s (%s rows); "
                    "falling back to the first row",
                    index,
                    self.entity_id,
                    len(data.rows),
                )
                index = 0
            attributes.update(rename_keys(data.rows[index], VALUE_PREFIX))
            attributes[ATTR_SELECTED_ROW] = index
        else:
            attributes[ATTR_SELECTED_ROW] = -1

        attributes[ATTR_JSON_RESULT] = data.json_result
        if data.json_truncated:
            attributes[ATTR_JSON_TRUNCATED] = True
        attributes[ATTR_EXECUTED_QUERY] = data.query
        attributes[ATTR_QUERY_DATE] = data.query_date
        attributes[ATTR_QUERY_TIME] = data.query_time
        return attributes

    async def async_set_query(self, query: str | None = None) -> None:
        """Replace the query of this sensor and refresh it immediately.

        Passing no query, or an empty one, restores the configured query.
        """
        self.coordinator.query = query or self.coordinator.default_query
        self._selected_row = 0
        _LOGGER.debug("New query for %s: %s", self.entity_id, self.coordinator.query)
        await self.coordinator.async_refresh()

    async def async_select_record(self, rownumber: int) -> None:
        """Expose the columns of the given row as attributes."""
        self._selected_row = rownumber
        _LOGGER.debug("Selected row %s for %s", rownumber, self.entity_id)
        self.async_write_ha_state()
