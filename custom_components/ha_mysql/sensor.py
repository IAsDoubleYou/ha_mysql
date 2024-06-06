from __future__ import annotations  # noqa: D100

import decimal
import json
import logging
import re

import mysql.connector

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

_LOGGER = logging.getLogger(__name__)

DOMAIN = "ha_mysql"
CONF_MYSQL_NAME = "name"
CONF_MYSQL_HOST = "mysql_host"
CONF_MYSQL_PORT = "mysql_port"
CONF_MYSQL_DB = "mysql_db"
CONF_MYSQL_USERNAME = "mysql_username"
CONF_MYSQL_PASSWORD = "mysql_password"
CONF_DEFAULT_QUERY = "default_query"
CONF_QUERY = "query"

entities = []


def setup_platform(  # noqa: D103
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    name = config[CONF_MYSQL_NAME]
    mysql_db = config[CONF_MYSQL_DB]
    default_query = config[CONF_DEFAULT_QUERY]

    my_entity = HAMySQLSensor(hass, config, name, mysql_db, default_query)
    entities.append(my_entity)
    add_entities([my_entity])


#    add_entities([HAMySQLSensor(hass, config, name, mysql_db, default_query)])


class DecimalEncoder(json.JSONEncoder):  # noqa: D101
    def default(self, o):  # noqa: D102
        if isinstance(o, decimal.Decimal):
            return str(o)
        return super().default(o)


class HAMySQLSensor(SensorEntity):
    """Representation of a Sensor."""

    _attr_name = "HA MySQL Sensor"
    _attr_native_value = 0
    _custom_attributes = {}
    _unique_id = None
    _mysql_db = None
    _default_query = None
    _hass = None

    def string_to_id(self, input_string):  # noqa: D102
        # Zet de string eerst om naar kleine letters
        input_string = input_string.lower()

        # Vervang alle niet-toegestane tekens door underscores
        clean_string = re.sub(r"[^a-z0-9_]", "_", input_string)

        # Verwijder eventuele opeenvolgende underscores
        clean_string = re.sub(r"__+", "_", clean_string)

        # Zorg ervoor dat de string niet begint met een underscore
        if clean_string.startswith("_"):
            clean_string = clean_string[1:]

        # Zorg ervoor dat de string niet eindigt met een underscore
        if clean_string.endswith("_"):
            clean_string = clean_string[:-1]

        return clean_string

    def rename_keys(self, old_dict, prefix):  # noqa: D102
        new_dict = {}
        for key, value in old_dict.items():
            new_key = prefix + key
            new_dict[new_key] = value
        return new_dict

    def convert_decimals(self, data):
        """Convert decimals in data structure to strings."""
        for key, value in data.items():
            if isinstance(value, decimal.Decimal):
                data[key] = str(value)
        return data

    def __init__(self, hass, config, name, mysql_db, default_query) -> None:  # noqa: D107
        self._attr_name = name

        # _unique_id = uuid.uuid4()
        # _unique_id = str(_unique_id).replace("-", "")
        # self._unique_id = f"ha_mysql_{_unique_id}"

        self._unique_id = self.string_to_id("ha_mysql." + name)

        self._hass = hass

        self._mysql_db = mysql_db
        self._default_query = default_query
        self._active_row = -1
        self._available_rows = 0
        self._active_query = ""
        self._json_result = ""
        self._cursor_description = {}
        self._rows = {}

        # self.update()

        self._hass.services.register(DOMAIN, "execute", self.execute_service)
        self._hass.services.register(DOMAIN, "goto", self.goto_service)

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the sensor."""
        return self._custom_attributes

    @property
    def state_attributes(self):
        """Return the state attributes of the sensor."""
        return self._custom_attributes

    @property
    def unique_id(self):
        """Return the state attributes of the sensor."""
        return self._unique_id

    @property
    def mysql_db(self):
        """Return the state attributes of the sensor."""
        return self._mysql_db

    @property
    def default_query(self):
        """Return the state attributes of the sensor."""
        return self._default_query

    def XXupdate(self):  # noqa: D102
        _LOGGER.debug("Updating state and attributes")
        self._state = self._available_rows
        self._custom_attributes = {
            "name": self.name,
            #            "entity_id": self._unique_id,
            "database": self.mysql_db,
            "default_query": self._default_query,
            "active_query": self._active_query,
            "available_rows": self._available_rows,
            "selected_row_number": self._active_row,
            "json_result": self._json_result,
        }
        _LOGGER.debug("Updated state and attributes")

    def select_row(self, entity_to_modify, rownumber):  # noqa: D102
        _LOGGER.debug("Selecting requested row")

        # Retrieve the field values of the requested row, so these can be returned as attributes
        #        self._custom_attributes.update(entity_to_modify._rows[rownumber])
        entity_to_modify._active_row = rownumber
        # self.update()

        self._custom_attributes.clear()
        entity_to_modify._attr_native_value = entity_to_modify._available_rows
        if entity_to_modify._available_rows > 0 and isinstance(
            entity_to_modify._rows[rownumber], dict
        ):
            self._custom_attributes.update(
                self.convert_decimals(entity_to_modify._rows[rownumber])
            )
            self._custom_attributes = self.rename_keys(
                self._custom_attributes, "valueof_"
            )

        self._custom_attributes["name"] = self.name
        #            "entity_id": self._unique_id,
        self._custom_attributes["database"] = self.mysql_db
        self._custom_attributes["default_query"] = self._default_query
        self._custom_attributes["active_query"] = self._active_query
        self._custom_attributes["available_rows"] = self._available_rows
        self._custom_attributes["selected_row_number"] = self._active_row
        self._custom_attributes["json_result"] = self._json_result

        # self.async_schedule_update_ha_state()
        _LOGGER.debug("Selected requested row")

    def goto_service(self, call):  # noqa: D102
        _LOGGER.debug("Executing goto_service")

        entityid_to_modify = call.data.get("entity_id")

        # Zoek de juiste entiteit op basis van de unieke identifier
        entity_to_modify = None
        for entity in entities:
            if entity.entity_id == entityid_to_modify:
                entity_to_modify = entity
                break

        if entity_to_modify:
            rownumber = call.data.get("rownumber")
            self.select_row(entity_to_modify, rownumber)
            _LOGGER.debug("Executed goto_service")
        else:
            _LOGGER.error(
                "Error executing service. The provided entity does not exists"
            )

    def execute_service(self, call):  # noqa: D102
        _LOGGER.debug("Executing execute_service")

        entityid_to_modify = call.data.get("entity_id")

        # Zoek de juiste entiteit op basis van de unieke identifier
        entity_to_modify = None
        for entity in entities:
            if entity.entity_id == entityid_to_modify:
                entity_to_modify = entity
                break

        if entity_to_modify:
            # Ophalen van de configuratie

            _platform_config = self.hass.data.get(DOMAIN).get("platform")
            _host = _platform_config.get(CONF_MYSQL_HOST)
            _port = _platform_config.get(CONF_MYSQL_PORT)
            _username = _platform_config.get(CONF_MYSQL_USERNAME)
            _password = _platform_config.get(CONF_MYSQL_PASSWORD)

            _database = entity_to_modify.mysql_db

            _query = None
            _query = call.data.get(CONF_QUERY)
            if _query is None or _query == "":
                _query = entity_to_modify.default_query

            entity_to_modify._active_query = _query

            try:
                connection = mysql.connector.connect(
                    host=_host,
                    port=_port,
                    username=_username,
                    password=_password,
                    database=_database,
                )
                with connection.cursor(buffered=True, dictionary=True) as cursor:
                    cursor.execute(_query)
                    _LOGGER.info(
                        "Query executed successfully. Rows affected: %s",
                        cursor.rowcount,
                    )

                    if cursor.rowcount > 0:
                        _rows = cursor.fetchall()

                        # Convert to JSON string
                        _json_result = json.dumps(
                            _rows,
                            ensure_ascii=False,
                            indent=4,
                            default=str,
                            cls=DecimalEncoder,
                        )

                        entity_to_modify._rows = _rows
                        entity_to_modify._cursor_description = cursor.description
                        entity_to_modify._json_result = _json_result
                        entity_to_modify._available_rows = cursor.rowcount

                        if entity_to_modify._available_rows > 0 and isinstance(
                            entity_to_modify._rows[0], dict
                        ):
                            entity_to_modify._active_row = 0
                        else:
                            entity_to_modify._active_row = -1

                        # This part should be moved to the update function
                        self._custom_attributes.clear()
                        entity_to_modify._attr_native_value = (
                            entity_to_modify._available_rows
                        )
                        if entity_to_modify._available_rows > 0 and isinstance(
                            entity_to_modify._rows[0], dict
                        ):
                            ###
                            entity_to_modify._custom_attributes.update(
                                self.convert_decimals(entity_to_modify._rows[0])
                            )
                            entity_to_modify._custom_attributes = self.rename_keys(
                                entity_to_modify._custom_attributes, "valueof_"
                            )

                            entity_to_modify._state = "Query executed successfully"
                        else:
                            entity_to_modify._state = "Query returned no valid results"

                        entity_to_modify._custom_attributes["name"] = self.name
                        #            "entity_id": self._unique_id,
                        entity_to_modify._custom_attributes["database"] = self.mysql_db
                        entity_to_modify._custom_attributes[
                            "default_query"
                        ] = self._default_query
                        entity_to_modify._custom_attributes[
                            "active_query"
                        ] = self._active_query
                        entity_to_modify._custom_attributes[
                            "available_rows"
                        ] = self._available_rows
                        entity_to_modify._custom_attributes[
                            "selected_row_number"
                        ] = self._active_row
                        entity_to_modify._custom_attributes[
                            "json_result"
                        ] = self._json_result

                        _LOGGER.debug("Executed execute_service")
            except Exception as e:
                _LOGGER.error("Error executing query: %s", str(e))
                entity_to_modify._attr_native_value = -1
                self._state = "Error executing query"
            finally:
                # self.update()
                entity_to_modify.schedule_update_ha_state(force_refresh=True)
                if connection:
                    connection.close()


#       self.write_ha_state()
