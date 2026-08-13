"""Constants for the HA MySQL integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "ha_mysql"
PLATFORMS: Final = [Platform.SENSOR]

# Connection keys, shared by configuration.yaml and the config entry.
CONF_MYSQL_HOST: Final = "host"
CONF_MYSQL_PORT: Final = "port"
CONF_MYSQL_USERNAME: Final = "username"
CONF_MYSQL_PASSWORD: Final = "password"
CONF_MYSQL_DATABASE: Final = "database"

# Key used to carry the connection settings through the import flow.
CONF_CONNECTION: Final = "connection"

# hass.data flag telling the sensor platform that configuration.yaml was found
# and imported, so it does not have to report a missing ha_mysql: section.
DATA_YAML_IMPORTED: Final = "yaml_imported"

# Sensor keys.
CONF_SENSORS: Final = "sensors"
CONF_QUERY: Final = "query"
CONF_ROWNUMBER: Final = "rownumber"
CONF_MAX_JSON_ROWS: Final = "max_json_rows"
CONF_VALUE_COLUMN: Final = "value_column"
CONF_VALUE_TEMPLATE: Final = "value_template"
CONF_STATE_CLASS: Final = "state_class"
CONF_SUGGESTED_DISPLAY_PRECISION: Final = "suggested_display_precision"
CONF_UNIQUE_ID: Final = "unique_id"
CONF_SOURCE: Final = "source"

# Marks sensors that came from configuration.yaml, so they can be refreshed on
# every restart without touching the ones added through the user interface.
SOURCE_YAML: Final = "yaml"

# Defaults.
DEFAULT_PORT: Final = 3306
DEFAULT_SCAN_INTERVAL_SECONDS: Final = 30
DEFAULT_SCAN_INTERVAL: Final = timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS)
# 0 means "no limit", which keeps the historical behaviour of dumping the
# complete result set into the json_result attribute.
DEFAULT_MAX_JSON_ROWS: Final = 0
# Number of rows above which a warning is logged, recommending max_json_rows.
LARGE_RESULT_WARNING_THRESHOLD: Final = 1000

# Connection handling.
CONNECT_TIMEOUT: Final = 10
POOL_SIZE: Final = 5
MAX_QUERY_ATTEMPTS: Final = 2
RETRY_DELAY: Final = 1.0

# Services.
SERVICE_SET_QUERY: Final = "set_query"
SERVICE_SELECT_RECORD: Final = "select_record"

# State attributes.
ATTR_EXECUTED_QUERY: Final = "executed_sql_query"
ATTR_JSON_RESULT: Final = "json_result"
ATTR_JSON_TRUNCATED: Final = "json_result_truncated"
ATTR_QUERY_DATE: Final = "query_date"
ATTR_QUERY_TIME: Final = "query_time"
ATTR_ROW_COUNT: Final = "row_count"
ATTR_SELECTED_ROW: Final = "selected_row"

# Prefix used for the columns of the selected row, to avoid collisions with
# the static attributes above.
VALUE_PREFIX: Final = "valueof_"
