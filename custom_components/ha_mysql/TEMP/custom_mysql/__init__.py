import logging

import mysql.connector
import voluptuous as vol

from homeassistant.helpers import config_validation as cv

_LOGGER = logging.getLogger(__name__)

DOMAIN = "custom_mysql"
SERVICE_EXECUTE_QUERY = "execute_query"
CONF_QUERY = "query"
CONF_MYSQL_HOST = "host"
CONF_MYSQL_PORT = "port"
CONF_MYSQL_DATABASE = "database"
CONF_MYSQL_USERNAME = "username"
CONF_MYSQL_PASSWORD = "password"

SERVICE_EXECUTE_QUERY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_QUERY): cv.string,
    }
)


def setup(hass, config):
    db_config = config[DOMAIN]
    host = db_config[CONF_MYSQL_HOST]
    port = db_config[CONF_MYSQL_PORT]
    username = db_config[CONF_MYSQL_USERNAME]
    password = db_config[CONF_MYSQL_PASSWORD]
    database = db_config[CONF_MYSQL_DATABASE]

    hass.data[DOMAIN] = {
        CONF_MYSQL_HOST: host,
        CONF_MYSQL_PORT: port,
        CONF_MYSQL_USERNAME: username,
        CONF_MYSQL_PASSWORD: password,
        CONF_MYSQL_DATABASE: database,
    }

    # def handle_execute_query(call):
    #     query = call.data[CONF_QUERY]
    #     db = mysql.connector.connect(
    #         host=db_config[CONF_MYSQL_HOST],
    #         port=db_config[CONF_MYSQL_PORT],
    #         username=db_config[CONF_MYSQL_USERNAME],
    #         password=db_config[CONF_MYSQL_PASSWORD],
    #         database=db_config[CONF_MYSQL_DATABASE],
    #     )
    #     cursor = db.cursor()
    #     cursor.execute(query)
    #     result = cursor.fetchall()
    #     cursor.close()
    #     _LOGGER.info(f"Query result: {result}")

    # hass.services.register(
    #     DOMAIN,
    #     SERVICE_EXECUTE_QUERY,
    #     handle_execute_query,
    #     schema=SERVICE_EXECUTE_QUERY_SCHEMA,
    # )

    return True
