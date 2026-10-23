"""The HA MySQL integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
    IntegrationError,
)
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from .const import CONF_SENSORS, CONFIG_ENTRY_VERSION, DOMAIN, PLATFORMS
from .coordinator import (
    MySQLConnectionManager,
    MySQLError,
    TLSUnavailableError,
    classify_connect_error,
)
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

type HAMySQLConfigEntry = ConfigEntry[MySQLConnectionManager]

# Everything is set up through the user interface. A ha_mysql: section in
# configuration.yaml is reported by Home Assistant itself, which says that the
# integration does not support YAML.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Marked the sensors that were imported from configuration.yaml, while that was
# still possible. Nothing reads it any more; migration 1 -> 2 removes it.
_LEGACY_SOURCE_KEY = "source"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions, which belong to the integration, not a connection."""
    async_setup_services(hass)

    # Release 1.7.0 raised this Repairs issue for the YAML configuration. That
    # is gone now, and Repairs would keep showing the issue until it is removed.
    ir.async_delete_issue(hass, DOMAIN, "deprecated_yaml")
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: HAMySQLConfigEntry) -> bool:
    """Bring an entry created by an earlier release up to date.

    Version 1 marked the sensors imported from configuration.yaml with a
    source, so that the import could refresh them on every restart. The import
    is gone, and so is the use of the marker.
    """
    if entry.version > CONFIG_ENTRY_VERSION:
        # Written by a newer release than this one, which cannot be read here.
        return False

    if entry.version == 1:
        sensors: list[dict[str, Any]] = [
            {key: value for key, value in sensor.items() if key != _LEGACY_SOURCE_KEY}
            for sensor in entry.options.get(CONF_SENSORS, [])
        ]
        hass.config_entries.async_update_entry(
            entry,
            options={**entry.options, CONF_SENSORS: sensors},
            version=CONFIG_ENTRY_VERSION,
        )
        _LOGGER.debug("Migrated %s to version %s", entry.title, CONFIG_ENTRY_VERSION)

    return True


def _setup_error(entry: HAMySQLConfigEntry, err: MySQLError) -> IntegrationError:
    """Return the exception that tells Home Assistant why setup failed.

    The cause decides what Home Assistant does next. A server that cannot be
    reached right now (down, still booting, network not up yet) is worth
    retrying, which ConfigEntryNotReady makes Home Assistant do on its own.
    Refused credentials start the sign-in flow, because only the user can fix
    those. A database that does not exist, or that this user may not use, is
    not fixed by a new password and not by waiting, so it is a plain error.
    So is any other refusal by the server, such as a host the account may not
    connect from or a locked account: retrying those would go on forever.

    The forms decide by the same classify_connect_error, so a failure gets the
    same advice in both places. Home Assistant logs the exception it is given,
    so nothing is logged here.
    """
    failure = classify_connect_error(err)
    # The kinds of failure double as the translation keys of their messages.
    kwargs: dict[str, Any] = {
        "translation_domain": DOMAIN,
        "translation_key": failure,
        "translation_placeholders": {
            "connection": entry.title,
            "error": err.detail or type(err).__name__,
        },
    }

    if failure == "invalid_auth":
        return ConfigEntryAuthFailed(**kwargs)
    if failure == "cannot_connect":
        return ConfigEntryNotReady(**kwargs)
    return ConfigEntryError(**kwargs)


async def async_setup_entry(hass: HomeAssistant, entry: HAMySQLConfigEntry) -> bool:
    """Set up a MySQL connection and its sensors.

    There is no update listener: the options flow reloads the entry when the
    sensors changed, and the sign-in and reconfigure flows reload it
    themselves.
    """
    manager = MySQLConnectionManager(dict(entry.data))

    try:
        await manager.test_connection()
    except TLSUnavailableError as err:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="tls_unavailable",
            translation_placeholders={"connection": entry.title},
        ) from err
    except MySQLError as err:
        raise _setup_error(entry, err) from err

    entry.runtime_data = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HAMySQLConfigEntry) -> bool:
    """Unload a MySQL connection and close its pooled connections."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.close()
    return unloaded
