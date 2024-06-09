"""The ha_mysql component."""
import logging

import voluptuous as vol

from homeassistant.helpers import config_validation as cv

_LOGGER = logging.getLogger(__name__)

DOMAIN = "ha_mysql"
CONF_MYSQL_HOST = "host"
CONF_MYSQL_PORT = "port"
CONF_MYSQL_USERNAME = "username"
CONF_MYSQL_PASSWORD = "password"
CONF_MYSQL_DATABASE = "database"
CONF_QUERY = "query"

SERVICE_EXECUTE_QUERY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MYSQL_HOST): cv.string,
        vol.Required(CONF_MYSQL_PORT): cv.string,
        vol.Required(CONF_MYSQL_USERNAME): cv.string,
        vol.Required(CONF_MYSQL_PASSWORD): cv.string,
        vol.Required(CONF_MYSQL_DATABASE): cv.string,
        vol.Required(CONF_QUERY): cv.string,
    }
)


def setup(hass, config):  # noqa: D103
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

    return True
