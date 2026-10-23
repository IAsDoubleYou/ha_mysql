"""Checks that the files describing the integration agree with the code.

Home Assistant shows the strings and services.yaml to the user, and nothing
fails when they drift from the code, so these tests do.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import get_args

import probatio
import yaml

from custom_components.ha_mysql.const import SERVICE_SELECT_RECORD, SERVICE_SET_QUERY
from custom_components.ha_mysql.coordinator import ConnectFailure
from custom_components.ha_mysql.services import SELECT_RECORD_SCHEMA, SET_QUERY_SCHEMA
from custom_components.ha_mysql.sql import validate_read_only_query

PACKAGE = Path(__file__).resolve().parent.parent / "custom_components" / "ha_mysql"
STRINGS = json.loads((PACKAGE / "strings.json").read_text())
EN = json.loads((PACKAGE / "translations" / "en.json").read_text())
NL = json.loads((PACKAGE / "translations" / "nl.json").read_text())
SERVICES = yaml.safe_load((PACKAGE / "services.yaml").read_text())
SOURCE = {path.name: path.read_text() for path in PACKAGE.glob("*.py")}
FLOW = SOURCE["config_flow.py"]

SCHEMAS = {
    SERVICE_SET_QUERY: SET_QUERY_SCHEMA,
    SERVICE_SELECT_RECORD: SELECT_RECORD_SCHEMA,
}

# The kinds of connection failure. Setup and the forms pass one on as the key
# of its message instead of writing the key out, so the source cannot list them.
CONNECT_FAILURES = set(get_args(ConnectFailure.__value__))
# The same goes for the keys the read-only check returns.
READ_ONLY_ERRORS = {
    validate_read_only_query(query) for query in ("SELECT 1; SELECT 2", "DELETE FROM t")
}


def _paths(node: object, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    """Return the path of every leaf of a nested dict."""
    if isinstance(node, dict):
        return {p for key, value in node.items() for p in _paths(value, (*prefix, key))}
    return {prefix}


def _translation_keys() -> set[str]:
    """Return every translation_key the code writes out."""
    keys: set[str] = set()
    for text in SOURCE.values():
        keys |= set(re.findall(r'translation_key="([a-z_]+)"', text))
    return keys


def test_english_translation_is_the_strings_file() -> None:
    """en.json is a copy of strings.json."""
    assert EN == STRINGS


def test_dutch_translation_has_exactly_the_keys_of_the_english_one() -> None:
    """No missing and no leftover key in nl.json."""
    assert _paths(NL) == _paths(EN)


def test_every_exception_key_in_the_code_exists_and_every_key_is_used() -> None:
    """Each translation_key is defined, and none is defined for nothing."""
    used = _translation_keys() | CONNECT_FAILURES | READ_ONLY_ERRORS

    assert used == set(STRINGS["exceptions"])


def test_no_repairs_issue_is_raised() -> None:
    """The code raises no Repairs issue, so the strings describe none.

    Release 1.7.0 raised one for the YAML configuration. The code only deletes
    that issue now, and its text is gone with it.
    """
    assert "async_create_issue" not in "".join(SOURCE.values())
    assert "issues" not in STRINGS


def test_every_form_error_key_is_defined_and_used() -> None:
    """The errors the connection forms can show are the ones the strings define."""
    used = set(re.findall(r'return "([a-z_]+)", ', FLOW)) | CONNECT_FAILURES

    assert used == set(STRINGS["config"]["error"])


def test_every_sensor_form_error_key_is_defined_and_used() -> None:
    """The errors the sensor forms can show are the ones the strings define.

    They are set on the query field, on the name field, or carried by the
    outcome of a test run of the query.
    """
    used = set(
        re.findall(r'(?:\{CONF_QUERY: |errors\[CONF_NAME\] = |error=)"([a-z_]+)"', FLOW)
    )
    used |= READ_ONLY_ERRORS

    assert used == set(STRINGS["options"]["error"])


def test_every_abort_reason_is_defined_and_used() -> None:
    """The reasons come from the flows and from Home Assistant's own helpers.

    The two successful reasons come from async_update_reload_and_abort.
    """
    used = set(re.findall(r'reason="([a-z_]+)"', FLOW))
    defined = set(STRINGS["config"]["abort"]) | set(STRINGS["options"]["abort"])

    assert used | {"reauth_successful", "reconfigure_successful"} == defined


def test_every_step_is_defined_and_used() -> None:
    """The steps in the strings are the ones the flows can show."""
    steps = set(re.findall(r'step_id="([a-z_]+)"', FLOW))

    assert steps == set(STRINGS["config"]["step"]) | set(STRINGS["options"]["step"])


def test_the_menu_offers_the_steps_the_strings_name() -> None:
    """Each menu option has a label, and each label belongs to an option."""
    options: set[str] = set()
    for listed in re.findall(r"menu \+?= \[([^\]]*)\]", FLOW):
        options |= set(re.findall(r'"([a-z_]+)"', listed))

    assert options == set(STRINGS["options"]["step"]["init"]["menu_options"])


def test_services_are_the_ones_that_are_registered() -> None:
    """services.yaml, the strings and the code name the same actions."""
    assert set(SERVICES) == set(STRINGS["services"]) == set(SCHEMAS)


def test_service_fields_match_the_schema_one_to_one() -> None:
    """services.yaml has the fields of the schema, with its required and default."""
    for service, schema in SCHEMAS.items():
        fields = SERVICES[service]["fields"]
        keys = {str(key): key for key in schema}

        assert set(fields) == set(keys) == set(STRINGS["services"][service]["fields"])
        for name, field in fields.items():
            key = keys[name]
            assert field.get("required", False) is isinstance(key, probatio.Required), (
                service,
                name,
            )
            if key.default is probatio.UNDEFINED:
                assert "default" not in field, (service, name)
            else:
                assert field["default"] == key.default(), (service, name)
