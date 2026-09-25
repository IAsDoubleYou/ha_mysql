"""Small helpers shared between the modules of the HA MySQL integration."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError, TemplateError
from homeassistant.helpers.template import Template

from .const import DOMAIN


def generate_unique_id(name: str) -> str:
    """Return the unique ID used for sensors coming from configuration.yaml.

    The format is kept exactly as it was in earlier releases, so imported
    sensors keep their entity ID and their history.
    """
    return f"{DOMAIN}_{name.lower().replace(' ', '_')}"


def rename_keys(old_dict: dict[str, Any], prefix: str) -> dict[str, Any]:
    """Return a copy of the dict with every key prefixed."""
    return {f"{prefix}{key}": value for key, value in old_dict.items()}


def render_value(hass: HomeAssistant, value: Any) -> Any:
    """Render a single placeholder value, keeping its native Python type.

    A call from an automation arrives with its templates already rendered, but
    one made through the API or the developer tools does not, so render them
    here as well. Rendering is native (``parse_result=True``) so a template
    that yields a number, a boolean or none is bound as an int, float, bool or
    NULL instead of as text.

    Literal strings are handed to MySQL untouched: parsing those as well would
    turn a value like "1,2" into a tuple and "42" into an int, which would
    change what ends up in the database.
    """
    if not isinstance(value, str):
        return value

    template = Template(value, hass)
    if template.is_static:
        return value

    try:
        return template.async_render(parse_result=True)
    except TemplateError as err:
        raise ServiceValidationError(f"Invalid template in values: {err}") from err


def render_values(
    hass: HomeAssistant, values: Sequence[Any] | None
) -> tuple[Any, ...] | None:
    """Render the placeholder values of a parameterized query.

    Returns None when there is nothing to bind. That is not the same as an
    empty tuple: the driver only interpolates the statement when the
    arguments are not None, so an empty tuple would break a plain query that
    contains a literal percent sign, such as LIKE '%text%'.
    """
    if not values:
        return None

    return tuple(render_value(hass, value) for value in values)
