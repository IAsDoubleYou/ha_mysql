from datetime import datetime
import decimal
import json
import logging

import mysql.connector
import voluptuous as vol

# from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_USERNAME
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import Entity

_LOGGER = logging.getLogger(__name__)

# Definieer de ontbrekende constanten lokaal
CONF_DATABASE = "database"
CONF_SENSORS = "sensors"
CONF_QUERY = "query"
DOMAIN = "custom_mysql"
SERVICE_EXECUTE_QUERY = "execute_query"
CONF_MYSQL_HOST = "host"
CONF_MYSQL_PORT = "port"
CONF_MYSQL_DATABASE = "database"
CONF_MYSQL_USERNAME = "username"
CONF_MYSQL_PASSWORD = "password"

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

# Haal de lokale tijdzone van het systeem


# Setup platform functie
def setup_platform(hass, config, add_entities, discovery_info=None):
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

    # entities.append(CustomMySQLSensor(name, query, db))
    #    add_entities(entities, True)
    entity = CustomMySQLSensor(hass, config, name, query, db)
    entities.append(entity)
    add_entities([entity], True)

    hass.services.register(DOMAIN, "execute", handle_execute_service)


def handle_execute_service(call):
    _LOGGER.debug("Executing execute_service")

    entityid_to_modify = call.data.get("entity_id")

    # Zoek de juiste entiteit op basis van de unieke identifier
    entity_to_modify = None
    for entity in entities:
        if entity.entity_id == entityid_to_modify:
            entity_to_modify = entity
            break

    current_time = datetime.now()
    entity_to_modify._query_date = current_time.strftime("%Y-%m-%d")
    entity_to_modify._query_time = current_time.strftime("%H:%M:%S")


def generate_unique_id(name):
    """Generate a unique ID for the sensor."""
    return f"{DOMAIN}_{name.lower().replace(' ', '_')}"


class DecimalEncoder(json.JSONEncoder):  # noqa: D101
    def default(self, o):  # noqa: D102
        if isinstance(o, decimal.Decimal):
            return str(o)
        return super().default(o)


# Custom sensor klasse
class CustomMySQLSensor(Entity):
    def __init__(self, hass, config, name, query, db):
        self._name = name
        self._query = query
        self._db = db
        self._unique_id = generate_unique_id(name)
        self._hass = hass
        self._state = None

        self._query_date = None
        self._query_time = None

        self._attributes = {}

    @property
    def __str__(self):
        return str(getattr(self, self._name))

    @property
    def name(self):
        return self._name

    @property
    def state(self):
        return self._state

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._unique_id

    @property
    def extra_state_attributes(self):
        return self._attributes

    def convert_decimals(self, data):
        """Convert decimals in data structure to strings."""
        for key, value in data.items():
            if isinstance(value, decimal.Decimal):
                data[key] = str(value)

    def execute_query(self):
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

    def update(self):
        result = self.execute_query()
        if result:
            self._attributes = {}

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
