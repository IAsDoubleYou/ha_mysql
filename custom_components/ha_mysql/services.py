"""Actions of the HA MySQL integration."""

from __future__ import annotations

from typing import Any

import probatio

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, service

from .const import (
    ATTR_PERSIST,
    ATTR_VALUES,
    CONF_QUERY,
    CONF_ROWNUMBER,
    DOMAIN,
    SERVICE_SELECT_RECORD,
    SERVICE_SET_QUERY,
)

SET_QUERY_SCHEMA: dict[str | probatio.Marker, Any] = {
    probatio.Optional(CONF_QUERY): cv.string,
    # Values bound to the %s placeholders of query. Only scalars are
    # accepted: those are the types MySQL can bind to a single placeholder.
    probatio.Optional(ATTR_VALUES): probatio.All(
        cv.ensure_list, [probatio.Any(None, bool, int, float, str)]
    ),
    probatio.Optional(ATTR_PERSIST, default=False): cv.boolean,
}
SELECT_RECORD_SCHEMA: dict[str | probatio.Marker, Any] = {
    probatio.Required(CONF_ROWNUMBER): probatio.All(
        probatio.Coerce(int), probatio.Range(min=0)
    )
}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions, once for the integration.

    They act on the sensors, but belong to the integration: registered here,
    they stay listed while no connection is loaded, instead of appearing and
    disappearing with the first and the last one.
    """
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_SET_QUERY,
        entity_domain=SENSOR_DOMAIN,
        schema=SET_QUERY_SCHEMA,
        func="async_set_query",
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_SELECT_RECORD,
        entity_domain=SENSOR_DOMAIN,
        schema=SELECT_RECORD_SCHEMA,
        func="async_select_record",
    )
