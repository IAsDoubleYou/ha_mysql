"""Sensor platform for the HA MySQL integration."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from enum import Enum
import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_NAME,
    CONF_UNIT_OF_MEASUREMENT,
    MAX_LENGTH_STATE_STATE,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import (
    config_validation as cv,
    entity_platform,
    entity_registry as er,
)
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.template import Template
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType, StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_EXECUTED_QUERY,
    ATTR_JSON_RESULT,
    ATTR_JSON_TRUNCATED,
    ATTR_QUERY_DATE,
    ATTR_QUERY_TIME,
    ATTR_ROW_COUNT,
    ATTR_SELECTED_ROW,
    CONF_MYSQL_DATABASE,
    CONF_MYSQL_HOST,
    CONF_QUERY,
    CONF_ROWNUMBER,
    CONF_SENSORS,
    CONF_STATE_CLASS,
    CONF_SUGGESTED_DISPLAY_PRECISION,
    CONF_UNIQUE_ID,
    CONF_VALUE_COLUMN,
    CONF_VALUE_TEMPLATE,
    DATA_YAML_IMPORTED,
    DOMAIN,
    SERVICE_SELECT_RECORD,
    SERVICE_SET_QUERY,
    VALUE_PREFIX,
)
from .coordinator import MySQLQueryCoordinator, QueryResult
from .helpers import rename_keys

if TYPE_CHECKING:
    from . import HAMySQLConfigEntry

_LOGGER = logging.getLogger(__name__)

SET_QUERY_SCHEMA = {vol.Optional(CONF_QUERY): cv.string}
SELECT_RECORD_SCHEMA = {
    vol.Required(CONF_ROWNUMBER): vol.All(vol.Coerce(int), vol.Range(min=0))
}


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Handle a sensor entry in configuration.yaml.

    The entries are read and imported by async_setup, so nothing is created
    here. Without the ha_mysql: section there is nothing to import.
    """
    if hass.data.get(DOMAIN, {}).get(DATA_YAML_IMPORTED):
        return

    _LOGGER.error(
        "No ha_mysql: section found in configuration.yaml; add the database "
        "settings there or set the integration up through the user interface"
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HAMySQLConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors of a MySQL connection."""
    sensors: list[dict[str, Any]] = entry.options.get(CONF_SENSORS, [])
    _async_cleanup_registry(hass, entry, sensors)

    entities: list[HAMySQLSensor] = []
    for sensor_config in sensors:
        coordinator = MySQLQueryCoordinator(
            hass, entry, entry.runtime_data, sensor_config
        )
        entities.append(HAMySQLSensor(coordinator, entry, sensor_config))

    # A single failing query should not keep the other sensors from loading,
    # so failures are left to the coordinator instead of raising here.
    if entities:
        await asyncio.gather(
            *(entity.coordinator.async_refresh() for entity in entities)
        )

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SET_QUERY, SET_QUERY_SCHEMA, "async_set_query"
    )
    platform.async_register_entity_service(
        SERVICE_SELECT_RECORD, SELECT_RECORD_SCHEMA, "async_select_record"
    )

    async_add_entities(entities)


@callback
def _async_cleanup_registry(
    hass: HomeAssistant, entry: HAMySQLConfigEntry, sensors: list[dict[str, Any]]
) -> None:
    """Drop registry entries of sensors that are no longer configured."""
    registry = er.async_get(hass)
    configured = {sensor[CONF_UNIQUE_ID] for sensor in sensors}

    for registry_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if registry_entry.unique_id not in configured:
            _LOGGER.debug("Removing stale entity %s", registry_entry.entity_id)
            registry.async_remove(registry_entry.entity_id)


class HAMySQLSensor(CoordinatorEntity[MySQLQueryCoordinator], SensorEntity):
    """Sensor holding the result of a MySQL query.

    By default the state is the number of rows returned by the query. With
    value_column or value_template the state becomes a value from the result
    set instead. The columns of the selected row are always exposed as
    attributes prefixed with `valueof_`.
    """

    _attr_icon = "mdi:database-search"

    def __init__(
        self,
        coordinator: MySQLQueryCoordinator,
        entry: HAMySQLConfigEntry,
        config: dict[str, Any],
    ) -> None:
        """Initialise the sensor from its stored configuration."""
        super().__init__(coordinator)
        self._selected_row = 0
        self._warned_missing_column = False
        self._warned_bad_value = False

        self._attr_name = config[CONF_NAME]
        self._attr_unique_id = config[CONF_UNIQUE_ID]
        self._value_column: str | None = config.get(CONF_VALUE_COLUMN)
        self._attr_native_unit_of_measurement = config.get(CONF_UNIT_OF_MEASUREMENT)
        self._attr_suggested_display_precision = config.get(
            CONF_SUGGESTED_DISPLAY_PRECISION
        )
        self._attr_device_class = _enum_or_none(
            SensorDeviceClass, config.get(CONF_DEVICE_CLASS), self._attr_name
        )
        self._attr_state_class = _enum_or_none(
            SensorStateClass, config.get(CONF_STATE_CLASS), self._attr_name
        )

        self._template: Template | None = None
        if (template := config.get(CONF_VALUE_TEMPLATE)) is not None:
            self._template = Template(template, coordinator.hass)

        # A value that Home Assistant charts or converts has to be a number.
        self._numeric = (
            self._attr_state_class is not None
            or self._attr_native_unit_of_measurement is not None
            or self._attr_suggested_display_precision is not None
        )

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"{entry.data[CONF_MYSQL_DATABASE]} @ {entry.data[CONF_MYSQL_HOST]}",
            model="MySQL database",
            entry_type=DeviceEntryType.SERVICE,
        )

    def _resolve_index(self, data: QueryResult) -> int | None:
        """Return the index of the row to expose, or None when there is none."""
        if not data.rows:
            return None
        if self._selected_row < len(data.rows):
            return self._selected_row

        _LOGGER.warning(
            "Selected row %s is out of range for %s (%s rows); "
            "falling back to the first row",
            self._selected_row,
            self.entity_id,
            len(data.rows),
        )
        return 0

    def _raw_value(self, data: QueryResult) -> Any:
        """Return the value for the state, before any conversion."""
        if self._template is not None:
            index = self._resolve_index(data)
            try:
                return self._template.async_render(
                    variables={
                        "rows": data.rows,
                        "row": data.rows[index] if index is not None else None,
                        "row_count": data.row_count,
                    },
                    parse_result=True,
                )
            except TemplateError as err:
                if not self._warned_bad_value:
                    self._warned_bad_value = True
                    _LOGGER.error(
                        "Value template of %s failed: %s", self.entity_id, err
                    )
                return None

        if self._value_column is not None:
            index = self._resolve_index(data)
            if index is None:
                return None
            row = data.rows[index]
            if self._value_column not in row:
                if not self._warned_missing_column:
                    self._warned_missing_column = True
                    _LOGGER.error(
                        "Column %s is not part of the result of %s; available "
                        "columns are %s",
                        self._value_column,
                        self.entity_id,
                        ", ".join(row) or "none",
                    )
                return None
            return row[self._value_column]

        return data.row_count

    def _convert(self, value: Any) -> StateType | date | datetime:
        """Convert the raw value into something the sensor can report."""
        if value is None:
            return None

        if self._attr_device_class is SensorDeviceClass.TIMESTAMP:
            if isinstance(value, datetime):
                return value
            return dt_util.parse_datetime(str(value))

        if self._attr_device_class is SensorDeviceClass.DATE:
            if isinstance(value, datetime):
                return value.date()
            if isinstance(value, date):
                return value
            return dt_util.parse_date(str(value))

        if self._numeric:
            try:
                return float(value)
            except (TypeError, ValueError):
                if not self._warned_bad_value:
                    self._warned_bad_value = True
                    _LOGGER.error(
                        "Value %r of %s is not numeric, while a unit, device "
                        "class or state class is configured",
                        value,
                        self.entity_id,
                    )
                return None

        if isinstance(value, str) and len(value) > MAX_LENGTH_STATE_STATE:
            if not self._warned_bad_value:
                self._warned_bad_value = True
                _LOGGER.warning(
                    "Value of %s is longer than %s characters and was cut off",
                    self.entity_id,
                    MAX_LENGTH_STATE_STATE,
                )
            return value[:MAX_LENGTH_STATE_STATE]

        return value

    @property
    def native_value(self) -> StateType | date | datetime:
        """Return the row count, or the configured column or template value."""
        if (data := self.coordinator.data) is None:
            return None
        return self._convert(self._raw_value(data))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the query metadata and the columns of the selected row."""
        if (data := self.coordinator.data) is None:
            return {}

        attributes: dict[str, Any] = {}

        if (index := self._resolve_index(data)) is not None:
            attributes.update(rename_keys(data.rows[index], VALUE_PREFIX))
            attributes[ATTR_SELECTED_ROW] = index
        else:
            attributes[ATTR_SELECTED_ROW] = -1

        attributes[ATTR_ROW_COUNT] = data.row_count
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


def _enum_or_none[EnumT: Enum](
    enum: type[EnumT], value: str | None, name: str
) -> EnumT | None:
    """Return the enum member for the value, or None when it does not exist."""
    if not value:
        return None
    try:
        return enum(value)
    except ValueError:
        _LOGGER.error("Unknown %s %r configured for %s", enum.__name__, value, name)
        return None
