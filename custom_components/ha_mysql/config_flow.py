"""Config flow for the HA MySQL integration."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any
from uuid import uuid4

import voluptuous as vol

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    CONF_UNIT_OF_MEASUREMENT,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

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
    DEFAULT_MAX_JSON_ROWS,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    SOURCE_YAML,
)
from .coordinator import MySQLConnectionError, MySQLConnectionManager, MySQLQueryError

_LOGGER = logging.getLogger(__name__)

# MySQL error codes worth translating into a friendly message.
_ERRNO_ACCESS_DENIED = 1045
_ERRNO_DATABASE_ACCESS_DENIED = 1044
_ERRNO_UNKNOWN_DATABASE = 1049

# Database errors are shown on the form itself, so they are cut off before they
# push the rest of the dialog out of view.
_MAX_ERROR_LENGTH = 255

# Errors that say something about the connection instead of the query, and
# therefore belong under the form as a whole.
_BASE_ERRORS = ("cannot_connect", "unknown")

CONNECTION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MYSQL_HOST): TextSelector(),
        vol.Required(CONF_MYSQL_PORT, default=DEFAULT_PORT): NumberSelector(
            NumberSelectorConfig(min=1, max=65535, step=1, mode=NumberSelectorMode.BOX)
        ),
        vol.Required(CONF_MYSQL_USERNAME): TextSelector(),
        vol.Required(CONF_MYSQL_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_MYSQL_DATABASE): TextSelector(),
    }
)


def _sensor_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build the form for a single sensor, prefilled with the current values."""
    current = defaults or {}

    def suggest(key: str, fallback: Any = None) -> dict[str, Any]:
        value = current.get(key, fallback)
        return {} if value is None else {"suggested_value": value}

    return vol.Schema(
        {
            vol.Required(CONF_NAME, description=suggest(CONF_NAME)): TextSelector(),
            vol.Required(CONF_QUERY, description=suggest(CONF_QUERY)): TextSelector(
                TextSelectorConfig(multiline=True)
            ),
            vol.Required(
                CONF_SCAN_INTERVAL,
                description=suggest(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=86400, step=1, mode=NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_VALUE_COLUMN, description=suggest(CONF_VALUE_COLUMN)
            ): TextSelector(),
            vol.Optional(
                CONF_VALUE_TEMPLATE, description=suggest(CONF_VALUE_TEMPLATE)
            ): TemplateSelector(),
            vol.Optional(
                CONF_UNIT_OF_MEASUREMENT, description=suggest(CONF_UNIT_OF_MEASUREMENT)
            ): TextSelector(),
            vol.Optional(
                CONF_DEVICE_CLASS, description=suggest(CONF_DEVICE_CLASS)
            ): SelectSelector(
                SelectSelectorConfig(
                    options=sorted(item.value for item in SensorDeviceClass),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_STATE_CLASS, description=suggest(CONF_STATE_CLASS)
            ): SelectSelector(
                SelectSelectorConfig(
                    options=sorted(item.value for item in SensorStateClass),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_SUGGESTED_DISPLAY_PRECISION,
                description=suggest(CONF_SUGGESTED_DISPLAY_PRECISION),
            ): NumberSelector(
                NumberSelectorConfig(min=0, max=6, step=1, mode=NumberSelectorMode.BOX)
            ),
            vol.Required(
                CONF_MAX_JSON_ROWS,
                description=suggest(CONF_MAX_JSON_ROWS, DEFAULT_MAX_JSON_ROWS),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=10000, step=1, mode=NumberSelectorMode.BOX
                )
            ),
        }
    )


def _clean_sensor_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Normalise a submitted sensor form into what is stored in the options."""
    cleaned: dict[str, Any] = {
        CONF_NAME: user_input[CONF_NAME].strip(),
        CONF_QUERY: user_input[CONF_QUERY].strip(),
        CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
        CONF_MAX_JSON_ROWS: int(user_input.get(CONF_MAX_JSON_ROWS, 0)),
    }

    for key in (
        CONF_VALUE_COLUMN,
        CONF_VALUE_TEMPLATE,
        CONF_UNIT_OF_MEASUREMENT,
        CONF_DEVICE_CLASS,
        CONF_STATE_CLASS,
    ):
        value = user_input.get(key)
        if isinstance(value, str):
            value = value.strip()
        cleaned[key] = value or None

    precision = user_input.get(CONF_SUGGESTED_DISPLAY_PRECISION)
    cleaned[CONF_SUGGESTED_DISPLAY_PRECISION] = (
        None if precision is None else int(precision)
    )
    return cleaned


@dataclass(frozen=True)
class _QueryCheck:
    """Outcome of a test run of a sensor query."""

    error: str | None = None
    message: str = ""
    row_count: int = 0


def _error_detail(err: Exception) -> str:
    """Return the database message in a form that fits on the dialog."""
    # The coordinator prefixes its own errors; the driver message is what the
    # user needs to see.
    message = str(err).removeprefix("Query failed: ").strip()
    if len(message) > _MAX_ERROR_LENGTH:
        message = f"{message[:_MAX_ERROR_LENGTH]}..."
    return message


class HAMySQLConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the setup of a MySQL connection."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HAMySQLOptionsFlow:
        """Return the options flow that manages the sensors."""
        return HAMySQLOptionsFlow()

    async def _async_validate(self, connection: dict[str, Any]) -> str | None:
        """Try the connection and return an error key when it fails."""
        manager = MySQLConnectionManager(connection)
        try:
            await self.hass.async_add_executor_job(manager.test_connection)
        except MySQLQueryError as err:
            if err.errno == _ERRNO_ACCESS_DENIED:
                return "invalid_auth"
            if err.errno in (_ERRNO_DATABASE_ACCESS_DENIED, _ERRNO_UNKNOWN_DATABASE):
                return "unknown_database"
            _LOGGER.debug("Unexpected database error while validating: %s", err)
            return "unknown"
        except MySQLConnectionError:
            return "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error while validating the connection")
            return "unknown"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the database connection settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            connection = {
                CONF_MYSQL_HOST: user_input[CONF_MYSQL_HOST].strip(),
                CONF_MYSQL_PORT: int(user_input[CONF_MYSQL_PORT]),
                CONF_MYSQL_USERNAME: user_input[CONF_MYSQL_USERNAME],
                CONF_MYSQL_PASSWORD: user_input[CONF_MYSQL_PASSWORD],
                CONF_MYSQL_DATABASE: user_input[CONF_MYSQL_DATABASE].strip(),
            }

            await self.async_set_unique_id(_connection_id(connection))
            self._abort_if_unique_id_configured()

            if (error := await self._async_validate(connection)) is None:
                return self.async_create_entry(
                    title=_entry_title(connection),
                    data=connection,
                    options={CONF_SENSORS: []},
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                CONNECTION_SCHEMA, user_input or {}
            ),
            errors=errors,
        )

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Import the settings from configuration.yaml.

        This runs on every restart, so the entry keeps following the YAML file.
        Sensors that were added through the user interface are left alone.
        """
        connection = import_data[CONF_CONNECTION]
        yaml_sensors = import_data[CONF_SENSORS]

        await self.async_set_unique_id(
            _connection_id(connection), raise_on_progress=False
        )

        for entry in self._async_current_entries():
            if entry.unique_id != self.unique_id:
                continue
            kept = [
                sensor
                for sensor in entry.options.get(CONF_SENSORS, [])
                if sensor.get(CONF_SOURCE) != SOURCE_YAML
            ]
            self.hass.config_entries.async_update_entry(
                entry,
                data=connection,
                options={CONF_SENSORS: [*yaml_sensors, *kept]},
            )
            return self.async_abort(reason="already_configured")

        return self.async_create_entry(
            title=_entry_title(connection),
            data=connection,
            options={CONF_SENSORS: yaml_sensors},
        )


class HAMySQLOptionsFlow(OptionsFlow):
    """Manage the sensors of a MySQL connection."""

    def __init__(self) -> None:
        """Start without a selected sensor."""
        self._selected: str | None = None
        # Query that the user already saw the "no rows" warning for.
        self._empty_ack: str | None = None

    @property
    def _sensors(self) -> list[dict[str, Any]]:
        """Return a copy of the configured sensors."""
        return [
            dict(sensor) for sensor in self.config_entry.options.get(CONF_SENSORS, [])
        ]

    def _save(self, sensors: list[dict[str, Any]]) -> ConfigFlowResult:
        """Store the new sensor list."""
        return self.async_create_entry(data={CONF_SENSORS: sensors})

    async def _async_run_query(self, query: str) -> _QueryCheck:
        """Run the query once against the configured database.

        The manager of a loaded entry is reused, so testing a query does not
        open connections beyond the pool the sensors already share.
        """
        manager = getattr(self.config_entry, "runtime_data", None)
        borrowed = isinstance(manager, MySQLConnectionManager)
        if not borrowed:
            manager = MySQLConnectionManager(dict(self.config_entry.data))

        try:
            rows = await self.hass.async_add_executor_job(manager.execute, query)
        except MySQLQueryError as err:
            _LOGGER.debug("Test run of %s failed: %s", query, err)
            return _QueryCheck(error="query_failed", message=_error_detail(err))
        except MySQLConnectionError as err:
            return _QueryCheck(error="cannot_connect", message=_error_detail(err))
        except Exception:
            _LOGGER.exception("Unexpected error while testing the query")
            return _QueryCheck(error="unknown")
        finally:
            if not borrowed:
                await self.hass.async_add_executor_job(manager.close)

        return _QueryCheck(row_count=len(rows))

    async def _async_check_query(self, query: str) -> tuple[dict[str, str], str]:
        """Validate a submitted query.

        Returns the errors to show on the form, and the database message that
        goes with them.
        """
        if not query:
            return {CONF_QUERY: "query_empty"}, ""

        check = await self._async_run_query(query)
        if check.error is not None:
            field = "base" if check.error in _BASE_ERRORS else CONF_QUERY
            return {field: check.error}, check.message

        if check.row_count == 0 and self._empty_ack != query:
            # A query that returns nothing today can return rows tomorrow, so
            # this only warns: submitting the same query again saves it.
            self._empty_ack = query
            return {CONF_QUERY: "query_no_results"}, ""

        return {}, ""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show what can be done with this connection."""
        menu = ["add_sensor"]
        if self._sensors:
            menu += ["select_sensor", "remove_sensor"]
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_add_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a new sensor to this connection."""
        errors: dict[str, str] = {}
        detail = ""

        if user_input is not None:
            sensors = self._sensors
            cleaned = _clean_sensor_input(user_input)
            if any(sensor[CONF_NAME] == cleaned[CONF_NAME] for sensor in sensors):
                errors[CONF_NAME] = "name_exists"
            else:
                errors, detail = await self._async_check_query(cleaned[CONF_QUERY])
                if not errors:
                    cleaned[CONF_UNIQUE_ID] = uuid4().hex
                    sensors.append(cleaned)
                    return self._save(sensors)

        return self.async_show_form(
            step_id="add_sensor",
            data_schema=_sensor_schema(user_input),
            errors=errors,
            description_placeholders={"error": detail},
        )

    async def async_step_select_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the sensor that should be edited."""
        if user_input is not None:
            self._selected = user_input[CONF_UNIQUE_ID]
            return await self.async_step_edit_sensor()

        return self.async_show_form(
            step_id="select_sensor",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_UNIQUE_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {
                                    "value": sensor[CONF_UNIQUE_ID],
                                    "label": sensor[CONF_NAME],
                                }
                                for sensor in self._sensors
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_edit_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the settings of the selected sensor."""
        sensors = self._sensors
        index = next(
            (
                position
                for position, sensor in enumerate(sensors)
                if sensor[CONF_UNIQUE_ID] == self._selected
            ),
            None,
        )
        if index is None:
            return self.async_abort(reason="sensor_not_found")

        errors: dict[str, str] = {}
        detail = ""

        if user_input is None:
            # The stored query was accepted before, so an edit that leaves it
            # alone is not held up by the "no rows" warning.
            self._empty_ack = sensors[index][CONF_QUERY]
        else:
            cleaned = _clean_sensor_input(user_input)
            if any(
                sensor[CONF_NAME] == cleaned[CONF_NAME]
                for position, sensor in enumerate(sensors)
                if position != index
            ):
                errors[CONF_NAME] = "name_exists"
            else:
                errors, detail = await self._async_check_query(cleaned[CONF_QUERY])
                if not errors:
                    # The unique ID stays untouched so the entity keeps its
                    # history.
                    cleaned[CONF_UNIQUE_ID] = sensors[index][CONF_UNIQUE_ID]
                    if (source := sensors[index].get(CONF_SOURCE)) is not None:
                        cleaned[CONF_SOURCE] = source
                    sensors[index] = cleaned
                    return self._save(sensors)

        return self.async_show_form(
            step_id="edit_sensor",
            data_schema=_sensor_schema(user_input or sensors[index]),
            errors=errors,
            description_placeholders={
                "name": sensors[index][CONF_NAME],
                "error": detail,
            },
        )

    async def async_step_remove_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove one or more sensors from this connection."""
        if user_input is not None:
            removed = set(user_input[CONF_SENSORS])
            return self._save(
                [
                    sensor
                    for sensor in self._sensors
                    if sensor[CONF_UNIQUE_ID] not in removed
                ]
            )

        return self.async_show_form(
            step_id="remove_sensor",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SENSORS): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {
                                    "value": sensor[CONF_UNIQUE_ID],
                                    "label": sensor[CONF_NAME],
                                }
                                for sensor in self._sensors
                            ],
                            mode=SelectSelectorMode.LIST,
                            multiple=True,
                        )
                    )
                }
            ),
        )


def _connection_id(connection: dict[str, Any]) -> str:
    """Return the unique ID that identifies one database connection."""
    return (
        f"{connection[CONF_MYSQL_HOST]}:{connection[CONF_MYSQL_PORT]}"
        f"/{connection[CONF_MYSQL_DATABASE]}"
    )


def _entry_title(connection: dict[str, Any]) -> str:
    """Return the title shown on the integration card."""
    return f"{connection[CONF_MYSQL_DATABASE]} @ {connection[CONF_MYSQL_HOST]}"
