"""Config flow for the HA MySQL integration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
from typing import Any
from uuid import uuid4

import probatio

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    CONF_UNIT_OF_MEASUREMENT,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
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
    CONF_MAX_JSON_ROWS,
    CONF_MYSQL_DATABASE,
    CONF_MYSQL_HOST,
    CONF_MYSQL_PASSWORD,
    CONF_MYSQL_PORT,
    CONF_MYSQL_USERNAME,
    CONF_QUERY,
    CONF_SENSORS,
    CONF_STATE_CLASS,
    CONF_SUGGESTED_DISPLAY_PRECISION,
    CONF_UNIQUE_ID,
    CONF_USE_TLS,
    CONF_VALUE_COLUMN,
    CONF_VALUE_TEMPLATE,
    CONFIG_ENTRY_VERSION,
    DEFAULT_MAX_JSON_ROWS,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DEFAULT_USE_TLS,
    DOMAIN,
)
from .coordinator import (
    MySQLConnectionError,
    MySQLConnectionManager,
    MySQLError,
    MySQLQueryError,
    TLSUnavailableError,
    classify_connect_error,
)
from .sql import validate_read_only_query

_LOGGER = logging.getLogger(__name__)

# Database errors are shown on the form itself, so they are cut off before they
# push the rest of the dialog out of view.
_MAX_ERROR_LENGTH = 255

# Errors that say something about the connection instead of the query, and
# therefore belong under the form as a whole.
_BASE_ERRORS = ("cannot_connect", "unknown")

# A masked input. The stored password is never its default or its suggested
# value, so it is not sent to the browser: either would put it in the form
# definition, readable in the page source and in the network traffic.
PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _connection_schema(*, editing: bool = False) -> probatio.Schema:
    """Return the form for the connection settings.

    Editing makes the password optional: the stored one is not shown, so a
    field left empty keeps it. The current values are added by the caller as
    suggestions, without the password.
    """
    password = (
        probatio.Optional(CONF_MYSQL_PASSWORD)
        if editing
        else probatio.Required(CONF_MYSQL_PASSWORD)
    )
    return probatio.Schema(
        {
            probatio.Required(CONF_MYSQL_HOST): TextSelector(),
            probatio.Required(CONF_MYSQL_PORT, default=DEFAULT_PORT): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=65535, step=1, mode=NumberSelectorMode.BOX
                )
            ),
            probatio.Required(CONF_MYSQL_USERNAME): TextSelector(),
            password: PASSWORD_SELECTOR,
            probatio.Required(CONF_MYSQL_DATABASE): TextSelector(),
            probatio.Required(CONF_USE_TLS, default=DEFAULT_USE_TLS): BooleanSelector(),
        }
    )


CONNECTION_SCHEMA = _connection_schema()
RECONFIGURE_SCHEMA = _connection_schema(editing=True)
REAUTH_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_MYSQL_USERNAME): TextSelector(),
        probatio.Required(CONF_MYSQL_PASSWORD): PASSWORD_SELECTOR,
    }
)


def _connection_from_input(
    user_input: Mapping[str, Any], stored: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Return the connection settings that a submitted form describes.

    A password left empty on the reconfigure form keeps the stored one: the
    form does not show it, so an untouched field means "keep it" and not "set
    it to nothing".
    """
    password = user_input.get(CONF_MYSQL_PASSWORD) or (stored or {}).get(
        CONF_MYSQL_PASSWORD, ""
    )
    return {
        CONF_MYSQL_HOST: user_input[CONF_MYSQL_HOST].strip(),
        CONF_MYSQL_PORT: int(user_input[CONF_MYSQL_PORT]),
        CONF_MYSQL_USERNAME: user_input[CONF_MYSQL_USERNAME],
        CONF_MYSQL_PASSWORD: password,
        CONF_MYSQL_DATABASE: user_input[CONF_MYSQL_DATABASE].strip(),
        CONF_USE_TLS: user_input.get(CONF_USE_TLS, DEFAULT_USE_TLS),
    }


def _without_password(settings: Mapping[str, Any]) -> dict[str, Any]:
    """Return the settings to suggest on a form, so never the password."""
    return {key: value for key, value in settings.items() if key != CONF_MYSQL_PASSWORD}


def _sensor_schema(defaults: dict[str, Any] | None = None) -> probatio.Schema:
    """Build the form for a single sensor, prefilled with the current values."""
    current = defaults or {}

    def suggest(key: str, fallback: Any = None) -> dict[str, Any]:
        value = current.get(key, fallback)
        return {} if value is None else {"suggested_value": value}

    return probatio.Schema(
        {
            probatio.Required(
                CONF_NAME, description=suggest(CONF_NAME)
            ): TextSelector(),
            probatio.Required(
                CONF_QUERY, description=suggest(CONF_QUERY)
            ): TextSelector(TextSelectorConfig(multiline=True)),
            probatio.Required(
                CONF_SCAN_INTERVAL,
                description=suggest(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=86400, step=1, mode=NumberSelectorMode.BOX
                )
            ),
            probatio.Optional(
                CONF_VALUE_COLUMN, description=suggest(CONF_VALUE_COLUMN)
            ): TextSelector(),
            probatio.Optional(
                CONF_VALUE_TEMPLATE, description=suggest(CONF_VALUE_TEMPLATE)
            ): TemplateSelector(),
            probatio.Optional(
                CONF_UNIT_OF_MEASUREMENT, description=suggest(CONF_UNIT_OF_MEASUREMENT)
            ): TextSelector(),
            probatio.Optional(
                CONF_DEVICE_CLASS, description=suggest(CONF_DEVICE_CLASS)
            ): SelectSelector(
                SelectSelectorConfig(
                    options=sorted(item.value for item in SensorDeviceClass),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            probatio.Optional(
                CONF_STATE_CLASS, description=suggest(CONF_STATE_CLASS)
            ): SelectSelector(
                SelectSelectorConfig(
                    options=sorted(item.value for item in SensorStateClass),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            probatio.Optional(
                CONF_SUGGESTED_DISPLAY_PRECISION,
                description=suggest(CONF_SUGGESTED_DISPLAY_PRECISION),
            ): NumberSelector(
                NumberSelectorConfig(min=0, max=6, step=1, mode=NumberSelectorMode.BOX)
            ),
            probatio.Required(
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
    """Return the message of an error in a form that fits on the dialog."""
    # What the driver said is what the user needs to see, without the context
    # the connection manager puts in front of it.
    message = (err.detail if isinstance(err, MySQLError) else str(err)).strip()
    # Some exceptions carry no message at all; the type is better than nothing.
    if not message:
        return type(err).__name__
    if len(message) > _MAX_ERROR_LENGTH:
        message = f"{message[:_MAX_ERROR_LENGTH]}..."
    return message


async def _async_validate_connection(
    connection: dict[str, Any],
) -> tuple[str | None, str]:
    """Try the connection.

    Returns the error key to show on the form, and the message that goes
    with it. Causes that speak for themselves come without a message. Shared
    by the setup, sign-in and reconfigure forms, so each checks a connection
    the same way.

    The form is where the failure is reported, so it is only logged at debug
    level. Only an unexpected exception is logged as an error, with its
    traceback.
    """
    manager = MySQLConnectionManager(connection)
    try:
        await manager.test_connection()
    except TLSUnavailableError as err:
        # The settings themselves are fine; the server just did not encrypt.
        _LOGGER.debug("TLS requested but not established: %s", err)
        return "tls_unavailable", ""
    except MySQLError as err:
        # The same classification setup uses, so the form and the connection
        # never give different advice for the same answer from the server.
        failure = classify_connect_error(err)
        if failure in ("invalid_auth", "unknown_database"):
            return failure, ""
        # A refused connection, a timeout, a host the account may not connect
        # from and a failed TLS handshake all end up here, and each needs a
        # different fix, so the reason from the driver is shown as well.
        _LOGGER.debug("Connection check failed: %s", err)
        return failure, _error_detail(err)
    except Exception as err:
        _LOGGER.exception("Unexpected error while validating the connection")
        return "unknown", _error_detail(err)
    return None, ""


class HAMySQLConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the setup of a MySQL connection."""

    VERSION = CONFIG_ENTRY_VERSION

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HAMySQLOptionsFlow:
        """Return the options flow that manages the sensors."""
        return HAMySQLOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the database connection settings."""
        errors: dict[str, str] = {}
        detail = ""

        if user_input is not None:
            connection = _connection_from_input(user_input)

            await self.async_set_unique_id(_connection_id(connection))
            self._abort_if_unique_id_configured()

            error, detail = await _async_validate_connection(connection)
            if error is None:
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
            description_placeholders={"error": detail},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change where and how the connection is made.

        The new settings are tried before they are saved. The identity of the
        entry follows a changed host, port or database, but never onto
        another connection that already exists.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        detail = ""

        if user_input is not None:
            connection = _connection_from_input(user_input, stored=entry.data)

            error, detail = await _async_validate_connection(connection)
            if error is None:
                changes: dict[str, Any] = {}
                new_unique_id = _connection_id(connection)
                if new_unique_id != entry.unique_id:
                    other = self.hass.config_entries.async_entry_for_domain_unique_id(
                        DOMAIN, new_unique_id
                    )
                    if other is not None and other.entry_id != entry.entry_id:
                        return self.async_abort(reason="already_configured")
                    changes["unique_id"] = new_unique_id
                # A title the user picked stays; the generated one follows.
                if entry.title == _entry_title(entry.data):
                    changes["title"] = _entry_title(connection)
                return self.async_update_reload_and_abort(
                    entry, data=connection, **changes
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                RECONFIGURE_SCHEMA, _without_password(user_input or entry.data)
            ),
            errors=errors,
            description_placeholders={"error": detail},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start the flow that asks for new credentials.

        Home Assistant starts it when setup, or a sensor's query, reports
        that the server refused the stored username or password.
        """
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the username and password, and update the entry with them."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        detail = ""

        if user_input is not None:
            connection = {
                **entry.data,
                CONF_MYSQL_USERNAME: user_input[CONF_MYSQL_USERNAME],
                CONF_MYSQL_PASSWORD: user_input[CONF_MYSQL_PASSWORD],
            }
            error, detail = await _async_validate_connection(connection)
            if error is None:
                return self.async_update_reload_and_abort(entry, data=connection)
            errors["base"] = error

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                REAUTH_SCHEMA,
                {CONF_MYSQL_USERNAME: (user_input or entry.data)[CONF_MYSQL_USERNAME]},
            ),
            errors=errors,
            description_placeholders={"connection": entry.title, "error": detail},
        )


class HAMySQLOptionsFlow(OptionsFlowWithReload):
    """Manage the sensors of a MySQL connection.

    Reloads the entry when the sensor list changed, which is what builds the
    sensors again from it. The connection itself is changed through
    Reconfigure instead.
    """

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
            rows = await manager.execute(query)
        except MySQLQueryError as err:
            _LOGGER.debug("Test run of %s failed: %s", query, err)
            return _QueryCheck(error="query_failed", message=_error_detail(err))
        except MySQLConnectionError as err:
            return _QueryCheck(error="cannot_connect", message=_error_detail(err))
        except Exception as err:
            _LOGGER.exception("Unexpected error while testing the query")
            return _QueryCheck(error="unknown", message=_error_detail(err))
        finally:
            if not borrowed:
                await manager.close()

        return _QueryCheck(row_count=len(rows))

    async def _async_check_query(self, query: str) -> tuple[dict[str, str], str]:
        """Validate a submitted query.

        Returns the errors to show on the form, and the database message that
        goes with them.
        """
        if not query:
            return {CONF_QUERY: "query_empty"}, ""

        # Checked before the query ever reaches the database: ha_mysql only
        # reads, and testing a write here would mean running it for real.
        if (error := validate_read_only_query(query)) is not None:
            return {CONF_QUERY: error}, ""

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
        """Show what can be done with the sensors of this connection."""
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
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_UNIQUE_ID): SelectSelector(
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
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_SENSORS): SelectSelector(
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
