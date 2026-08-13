"""The HA MySQL integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_MYSQL_DATABASE,
    CONF_MYSQL_HOST,
    CONF_MYSQL_PASSWORD,
    CONF_MYSQL_PORT,
    CONF_MYSQL_USERNAME,
    DATA_CONFIG,
    DATA_MANAGER,
    DEFAULT_PORT,
    DOMAIN,
)
from .coordinator import MySQLConnectionManager

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_MYSQL_HOST): cv.string,
                vol.Optional(CONF_MYSQL_PORT, default=DEFAULT_PORT): cv.port,
                vol.Required(CONF_MYSQL_USERNAME): cv.string,
                vol.Required(CONF_MYSQL_PASSWORD): cv.string,
                vol.Required(CONF_MYSQL_DATABASE): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the shared database configuration from configuration.yaml."""
    if (conf := config.get(DOMAIN)) is None:
        # The component was pulled in by a sensor platform entry without a
        # matching ha_mysql: block. The platform reports this to the user.
        return True

    manager = MySQLConnectionManager(dict(conf))
    hass.data[DOMAIN] = {
        DATA_CONFIG: dict(conf),
        DATA_MANAGER: manager,
    }

    async def _async_close(event: Event) -> None:
        """Close the pooled connections when Home Assistant shuts down."""
        await hass.async_add_executor_job(manager.close)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_close)

    _LOGGER.debug("Configured HA MySQL for %s", manager.target)
    return True
