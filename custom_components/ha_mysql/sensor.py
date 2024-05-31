from __future__ import annotations  # noqa: D100

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


class HAMySQLSensor(SensorEntity):
    """Representation of a Sensor."""

    _attr_name = "HA MySQL Sensor"
    _attr_native_value = 0
    _active_query = None
    _result_item = {}
    _json_result = ""

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

    def __init__(self, hass, config, name, mysql_db, default_query):  # noqa: D107
        self._attr_name = name

        # _unique_id = uuid.uuid4()
        # _unique_id = str(_unique_id).replace("-", "")
        # self._unique_id = f"ha_mysql_{_unique_id}"

        self._unique_id = self.string_to_id("ha_mysql." + name)

        self._mysql_db = mysql_db
        self._default_query = default_query
        self._active_query = default_query
        self._active_record = -1
        self._hass = hass

        self._custom_attributes = {
            "Name": name,
            "Database": mysql_db,
            "Query": self._active_query,
            "UniqueId": self._unique_id,
            "ActiveRecord": self._active_record,
            "QueryResult": self._result_item,
            "JSONResult": self._json_result,
        }

        self._hass.services.register(DOMAIN, "execute", self.execute_service)

    @property
    def extra_state_attributes(self):
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

    def update(self):  # noqa: D102
        self._custom_attributes = {
            "Name": self.name,
            "Database": self._mysql_db,
            "Query": self._active_query,
            "UniqueId": self._unique_id,
            "ActiveRecord": self._active_record,
            "QueryResult": self._result_item,
            "JSONResult": self._json_result,
        }

    def execute_service(self, call):  # noqa: D102
        entityid_to_modify = call.data.get("entity_id")

        # Zoek de juiste entiteit op basis van de unieke identifier
        entity_to_modify = None
        for entity in entities:
            if entity.entity_id == entityid_to_modify:
                entity_to_modify = entity
                break

        if entity_to_modify:
            #            current_state = self._hass.states.get(entityid_to_modify)
            #            new_attributes = dict(current_state.attributes)

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
            entity_to_modify._result_item = {}

            try:
                connection = mysql.connector.connect(
                    host=_host,
                    port=_port,
                    username=_username,
                    password=_password,
                    database=_database,
                )
                with connection.cursor(buffered=True) as cursor:
                    cursor.execute(_query)
                    _result = cursor.rowcount
                    _LOGGER.info(
                        "Query executed successfully. Rows affected: %s", _result
                    )
                    entity_to_modify._attr_native_value = _result

                    if cursor.rowcount > 0:
                        # Retrieve all rows and convert to list of dictionaries
                        _columns = [col[0] for col in cursor.description]
                        _rows = cursor.fetchall()
                        _result = [dict(zip(_columns, _row)) for _row in _rows]

                        # Convert to JSON string
                        #                        json_result = json.dumps(result, ensure_ascii=False, indent=4)
                        _json_result = json.dumps(_result, ensure_ascii=False, indent=2)

                        # Retrieve the field values of the requested row, so these can be returned as attributes
                        _fieldvalues = _rows[0]

                        #                        fieldvalues = cursor.fetchone()

                        # Retrieve the column description objects and construct the result structure
                        # consisting of column name and field values
                        _result_item = {}

                        for _colcounter, columnnobject in enumerate(cursor.description):
                            _result_item[columnnobject[0]] = _fieldvalues[_colcounter]

                        entity_to_modify._json_result = _json_result
                        entity_to_modify._result_item = _result_item
                        entity_to_modify._active_record = 0
            except Exception as e:
                _LOGGER.error("Error executing query: %s", str(e))
                entity_to_modify._attr_native_value = -1
            finally:
                self.update()
                entity_to_modify.schedule_update_ha_state(force_refresh=True)
                if connection:
                    connection.close()


#       self.write_ha_state()
