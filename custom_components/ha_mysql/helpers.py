"""Small helpers shared between the modules of the HA MySQL integration."""

from __future__ import annotations

from typing import Any

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
