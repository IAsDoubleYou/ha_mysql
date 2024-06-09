from __future__ import annotations  # noqa: D100

from datetime import datetime
import decimal
import json
import logging

import mysql.connector
import voluptuous as vol

# from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import Entity

_LOGGER = logging.getLogger(__name__)

CONF_SENSORS = "sensors"

DOMAIN = "ha_mysql"
CONF_NAME = "name"
CONF_MYSQL_HOST = "host"
CONF_MYSQL_PORT = "port"
CONF_MYSQL_USERNAME = "username"
CONF_MYSQL_PASSWORD = "password"
CONF_MYSQL_DATABASE = "database"
CONF_QUERY = "query"
CONF_ROWNUMBER = "rownumber"
SERVICE_SET_QUERY = "set_query"
SERVICE_SELECT_RECORD = "select_record"


# Definieer de schema's
SENSOR_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_QUERY): cv.string,
    }
)

# PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
#     {
#         vol.Required(CONF_HOST): cv.string,
#         vol.Required(CONF_USERNAME): cv.string,
#         vol.Required(CONF_PASSWORD): cv.string,
#         vol.Required(CONF_DATABASE): cv.string,
#         vol.Required(CONF_SENSORS): vol.All(cv.ensure_list, [SENSOR_SCHEMA]),
#     }
# )

entities = []


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Get the platform configuration and create the sensors."""
    component_config = hass.data[DOMAIN]
    host = component_config.get(CONF_MYSQL_HOST)
    port = component_config.get(CONF_MYSQL_PORT)
    username = component_config.get(CONF_MYSQL_USERNAME)
    password = component_config.get(CONF_MYSQL_PASSWORD)
    database = component_config.get(CONF_MYSQL_DATABASE)

    name = config[CONF_NAME]
    query = config[CONF_QUERY]

    db = mysql.connector.connect(
        host=host, port=port, user=username, password=password, database=database
    )

    entity = HAMySQLSensor(hass, config, name, query, db)
    entities.append(entity)
    add_entities([entity], True)

    hass.services.register(DOMAIN, SERVICE_SET_QUERY, handle_set_query_service)
    hass.services.register(DOMAIN, SERVICE_SELECT_RECORD, handle_select_record)


def handle_set_query_service(call):  # noqa: D103
    _LOGGER.debug("Executing set_query service")

    entityid_to_modify = call.data.get("entity_id")

    # Locate the entity that needs to be addressed
    entity_to_modify = None
    for entity in entities:
        if entity.entity_id == entityid_to_modify:
            entity_to_modify = entity
            break

    query = None
    query = call.data.get(CONF_QUERY)
    if query is None or query == "":
        query = entity_to_modify.default_query
    else:
        entity_to_modify.query = query

    entity_to_modify.selected_row = 0


def handle_select_record(call):  # noqa: D103
    _LOGGER.debug("Executing select_record service")

    entityid_to_modify = call.data.get("entity_id")

    # Zoek de juiste entiteit op basis van de unieke identifier
    entity_to_modify = None
    for entity in entities:
        if entity.entity_id == entityid_to_modify:
            entity_to_modify = entity
            break

    rownumber = call.data.get(CONF_ROWNUMBER)
    if rownumber is not None:
        entity_to_modify.selected_row = rownumber


def generate_unique_id(name):
    """Generate a unique ID for the sensor."""
    return f"{DOMAIN}_{name.lower().replace(' ', '_')}"


class DecimalEncoder(json.JSONEncoder):  # noqa: D101
    def default(self, o):  # noqa: D102
        if isinstance(o, decimal.Decimal):
            return str(o)
        return super().default(o)


# Custom sensor klasse
class HAMySQLSensor(Entity):
    """HAMySQLSensor clas."""

    def __init__(self, hass: HomeAssistant, config, name, query, db) -> None:  # noqa: D107
        self._name = name
        self._query = query
        self._db = db
        self._unique_id = generate_unique_id(name)
        self._hass = hass
        self._state = None
        self._selected_row = 0

        self._query_date = None
        self._query_time = None

        self._attributes = {}

    @property
    def __str__(self):  # noqa: D105
        return str(getattr(self, self._name))

    @property
    def name(self):  # noqa: D102
        return self._name

    @property
    def state(self):  # noqa: D102
        return self._state

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._unique_id

    @property
    def extra_state_attributes(self):  # noqa: D102
        return self._attributes

    @property
    def query(self):  # noqa: D102
        return self._query

    @query.setter
    def query(self, value):  # noqa: D102
        self._query = value

    @property
    def selected_row(self):  # noqa: D102
        return self._selected_row

    @selected_row.setter
    def selected_row(self, value):  # noqa: D102
        self._selected_row = value

    def convert_decimals(self, data):
        """Convert decimals in data structure to strings."""
        for key, value in data.items():
            if isinstance(value, decimal.Decimal):
                data[key] = str(value)

    def rename_keys(self, old_dict, prefix):  # noqa: D102
        """Rename the fields of a dict."""
        new_dict = {}
        for key, value in old_dict.items():
            new_key = prefix + key
            new_dict[new_key] = value
        return new_dict

    def execute_query(self):  # noqa: D102
        current_time = datetime.now()
        self._query_date = current_time.strftime("%Y-%m-%d")
        self._query_time = current_time.strftime("%H:%M:%S")

        cursor = self._db.cursor(buffered=True, dictionary=True)

        cursor.execute(self._query)
        result = cursor.fetchall()

        for record in result:
            self.convert_decimals(record)

        cursor.close()
        return result

    def update(self):  # noqa: D102
        result = self.execute_query()
        if result:
            self._attributes = {}

            self._attributes.update(result[0])
            self._attributes = self.rename_keys(self._attributes, "valueof_")

            self._attributes["selected_row"] = self._selected_row

            # Convert to JSON string
            json_result = json.dumps(
                result,
                ensure_ascii=False,
                indent=4,
                default=str,
                cls=DecimalEncoder,
            )

            self._attributes["json_result"] = json_result

            self._attributes["query_date"] = self._query_date
            self._attributes["query_time"] = self._query_time

            # for idx, row in enumerate(results):
            #     self._attributes[f"result_{idx}"] = row
            self._state = len(result)
        else:
            self._state = 0
            self._attributes = {}
