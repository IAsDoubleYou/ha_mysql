"""Fixtures for the HA MySQL tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

CONFIG = {
    "ha_mysql": {
        "host": "db.local",
        "username": "user",
        "password": "secret",
        "database": "testdb",
    },
    "sensor": [
        {
            "platform": "ha_mysql",
            "name": "Employees",
            "query": "SELECT * FROM emp",
        }
    ],
}

ROWS = [
    {"id": 1, "name": "Alice", "salary": "1000.50"},
    {"id": 2, "name": "Bob", "salary": "2000.00"},
]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Enable loading of the custom integration in every test."""
    yield


@pytest.fixture
def mock_execute() -> Generator[object]:
    """Replace the blocking database call with a mock."""
    with patch(
        "custom_components.ha_mysql.coordinator.MySQLConnectionManager.execute",
        autospec=True,
        return_value=list(ROWS),
    ) as mock:
        yield mock
