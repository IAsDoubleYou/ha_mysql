"""Small helpers shared between the modules of the HA MySQL integration."""

from __future__ import annotations

from typing import Any


def rename_keys(old_dict: dict[str, Any], prefix: str) -> dict[str, Any]:
    """Return a copy of the dict with every key prefixed."""
    return {f"{prefix}{key}": value for key, value in old_dict.items()}
