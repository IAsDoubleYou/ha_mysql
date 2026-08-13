# HA MySQL Sensor for Home Assistant

[![HACS Custom][hacs_shield]][hacs]
[![GitHub Latest Release][releases_shield]][latest_release]
[![GitHub Downloads (latest Release)][downloads_latest_shield]][latest_release]
[![GitHub All Releases][downloads_total_shield]][releases]
[![Tests][tests_shield]][tests]
[![Community Forum][community_forum_shield]][community_forum]

Home Assistant custom integration that turns the result of a MySQL or MariaDB query into a sensor.

Every sensor runs its own query on its own interval. The result is available in three ways:

* the **state** of the sensor,
* the columns of one selected row as **`valueof_*` attributes**,
* the complete result set as JSON in the **`json_result` attribute**.

Queries can be replaced at runtime with the [`ha_mysql.set_query`](#ha_mysqlset_query) action, which makes it possible to build queries that depend on information only known at runtime.

> Looking for a service instead of a sensor? See [MySQL Query](https://github.com/IAsDoubleYou/homeassistant-mysql_query).

## What is the state of the sensor?

**By default the state is the number of rows the query returned**, not a value from the result set. This surprises most people at first, so it is worth repeating.

```sql
SELECT name, salary FROM emp
```

| | Value |
|---|---|
| State | `2` (two rows) |
| `valueof_name` | `Alice` |
| `valueof_salary` | `1000.50` |

If you want an actual measurement as the state, set [`value_column`](#sensor-options) or [`value_template`](#sensor-options). That is also what you need for the energy dashboard and long term statistics.

```sql
SELECT SUM(kwh) AS total FROM energy WHERE DATE(logged_at) = CURDATE()
```

With `value_column: total`, `unit_of_measurement: kWh`, `device_class: energy` and `state_class: total_increasing`, the state becomes the number itself.

## Requirements

| | |
|---|---|
| Home Assistant | 2025.1 or newer |
| Database | MySQL 5.7+ or MariaDB 10.3+ |
| Driver | `mysql-connector-python` 9.7.0, installed automatically |
| Network | The database has to be reachable from the machine running Home Assistant |

The integration only reads. A user with `SELECT` rights on the tables you query is enough:

```sql
CREATE USER 'homeassistant'@'%' IDENTIFIED BY 'a-good-password';
GRANT SELECT ON mydatabase.* TO 'homeassistant'@'%';
FLUSH PRIVILEGES;
```

## Installation

### Using [HACS](https://hacs.xyz/)

Add this repository as a custom repository, following [these directions](https://hacs.xyz/docs/faq/custom_repositories/), using `https://github.com/IAsDoubleYou/ha_mysql` as the repository URL. Install **HA MySQL** and restart Home Assistant.

### Manual

1. Download `homeassistant-ha_mysql.zip` from the [latest release](https://github.com/IAsDoubleYou/ha_mysql/releases/latest).
2. Open the directory of your Home Assistant configuration, the one holding `configuration.yaml`.
3. Create a `custom_components` directory there if it does not exist yet.
4. Inside `custom_components`, create a directory called `ha_mysql`.
5. Unpack the zip into it, so `manifest.json` ends up as `custom_components/ha_mysql/manifest.json`.
6. Restart Home Assistant.

## Configuration through the user interface

1. Go to **Settings → Devices & services → Add integration**.
2. Search for **HA MySQL**.
3. Fill in the connection details. The connection is tested before it is saved, so mistakes are reported right away.
4. Open **Configure** on the integration card to add sensors.

Every connection becomes one device, with all its sensors underneath it. Sensors are added, edited and removed through **Configure**; changes take effect immediately, without a restart.

## Configuration through `configuration.yaml`

YAML keeps working. On every start the settings are read and written into the integration, so `configuration.yaml` stays the source of truth for the sensors defined there. Sensors you added through the user interface are left untouched.

```yaml
ha_mysql:
  host: 192.168.1.10
  port: 3306
  username: homeassistant
  password: !secret mysql_password
  database: mydatabase

sensor:
  - platform: ha_mysql
    name: Employees
    query: SELECT * FROM emp

  - platform: ha_mysql
    name: Departments
    query: SELECT * FROM dept
    scan_interval: 300
```

A few things worth knowing:

* Removing a sensor from `configuration.yaml` also removes it from Home Assistant on the next restart.
* Sensors that came from YAML keep the entity ID and the history they had in earlier releases.
* Editing a YAML sensor through the user interface works, but `configuration.yaml` wins again after a restart. Pick one place per sensor.

### Connection options

Used by both the user interface and `configuration.yaml`.

| Option | Required | Default | Description |
|---|---|---|---|
| `host` | yes | | Host name or IP address of the database server |
| `port` | no | `3306` | Port the server listens on |
| `username` | yes | | User the queries run as |
| `password` | yes | | Password of that user |
| `database` | yes | | Default database for queries that do not name one themselves |

### Sensor options

| Option | Required | Default | Description |
|---|---|---|---|
| `name` | yes | | Name of the sensor, and the base of its entity ID |
| `query` | yes | | SQL query to run |
| `scan_interval` | no | `30` | Seconds between two runs of the query |
| `value_column` | no | | Column of the selected row to use as the state, instead of the row count |
| `value_template` | no | | Template that produces the state. Takes precedence over `value_column` |
| `unit_of_measurement` | no | | Unit shown behind the value, for example `kWh` or `°C` |
| `device_class` | no | | [Sensor device class](https://www.home-assistant.io/integrations/sensor/#device-class), for example `energy` or `temperature` |
| `state_class` | no | | `measurement`, `total` or `total_increasing`. Needed for long term statistics |
| `suggested_display_precision` | no | | Number of decimals shown in the interface |
| `max_json_rows` | no | `0` | Maximum number of rows in `json_result`. `0` keeps all of them |

Whenever `unit_of_measurement`, `state_class` or `suggested_display_precision` is set, the state has to be numeric. A value that cannot be read as a number becomes `unknown` and is reported once in the log.

### Template variables

`value_template` is rendered with these variables:

| Variable | Description |
|---|---|
| `row` | The selected row as a dictionary, or `none` when the result set is empty |
| `rows` | All rows as a list of dictionaries |
| `row_count` | The number of rows |

```yaml
value_template: "{{ row.salary | float * 1.21 }}"
value_template: "{{ rows | map(attribute='kwh') | map('float') | sum }}"
value_template: "{{ 'busy' if row_count > 10 else 'quiet' }}"
```

### Attributes

Every sensor exposes these attributes:

| Attribute | Description |
|---|---|
| `valueof_<column>` | The value of that column in the selected row. One attribute per column |
| `selected_row` | Index of the selected row, or `-1` when the result set is empty |
| `row_count` | Number of rows the query returned |
| `json_result` | The complete result set as a JSON string, or `{}` when it is empty |
| `json_result_truncated` | Only present, and `true`, when `max_json_rows` cut the result short |
| `executed_sql_query` | The query that produced this result, including one set with `set_query` |
| `query_date` | Date the query ran, as `YYYY-MM-DD` |
| `query_time` | Time the query ran, as `HH:MM:SS` |

The `valueof_` prefix avoids collisions with attributes such as `friendly_name`. A column named `friendly_name` becomes `valueof_friendly_name`.

### Column types

Most columns come back as the type you would expect. These are converted so they can be stored in a state, in an attribute and in `json_result`:

| Column type | Becomes |
|---|---|
| `DECIMAL`, `NUMERIC` | A string, for example `1000.50`. Use `float` in a template to calculate with it |
| `BINARY`, `VARBINARY`, `BLOB` | The text it holds. Data that is not valid UTF-8 becomes a short hexadecimal preview such as `0x89504e47...` |
| `TIME` | A readable duration, for example `1:30:00` |
| `SET` | A sorted list of the selected members |
| `DATE`, `DATETIME`, `TIMESTAMP` | Left as they are, so `device_class: date` and `device_class: timestamp` work |
| `NULL` | `None`, which shows up as `unknown` in the state |

Storing an image or another large binary value in a sensor is a bad idea regardless. Select it as a length or a checksum instead, for example `SELECT LENGTH(photo) AS bytes FROM staff`.

## Examples

### Daily energy consumption

```yaml
sensor:
  - platform: ha_mysql
    name: Energy today
    query: >
      SELECT ROUND(SUM(kwh), 3) AS total
      FROM energy_log
      WHERE DATE(logged_at) = CURDATE()
    scan_interval: 300
    value_column: total
    unit_of_measurement: kWh
    device_class: energy
    state_class: total_increasing
    suggested_display_precision: 2
```

This sensor can be used directly in the energy dashboard.

### Temperature from a logging table

```yaml
sensor:
  - platform: ha_mysql
    name: Greenhouse temperature
    query: >
      SELECT temperature, measured_at
      FROM measurements
      WHERE sensor_id = 3
      ORDER BY measured_at DESC
      LIMIT 1
    scan_interval: 60
    value_column: temperature
    unit_of_measurement: "°C"
    device_class: temperature
    state_class: measurement
    suggested_display_precision: 1
```

`valueof_measured_at` tells you how old the reading is.

### Counting rows

The default behaviour, useful for queues, open orders or unread messages.

```yaml
sensor:
  - platform: ha_mysql
    name: Open orders
    query: SELECT id, customer, total FROM orders WHERE status = 'open'
    scan_interval: 120
```

The state is the number of open orders, and `valueof_customer` shows the first one. Use [`ha_mysql.select_record`](#ha_mysqlselect_record) to walk through the others.

### A list without flooding the database

```yaml
sensor:
  - platform: ha_mysql
    name: Recent alarms
    query: SELECT moment, message FROM alarms ORDER BY moment DESC
    scan_interval: 600
    max_json_rows: 20
```

### Building a template sensor on top of it

Handy when you want several values from one query without running it more than once.

```yaml
template:
  - sensor:
      - name: Greenhouse humidity
        state: "{{ state_attr('sensor.greenhouse_temperature', 'valueof_humidity') }}"
        unit_of_measurement: "%"
        device_class: humidity
        state_class: measurement
```

### A query that depends on runtime information

```yaml
automation:
  - alias: Look up the selected customer
    triggers:
      - trigger: state
        entity_id: input_text.customer_id
    actions:
      - action: ha_mysql.set_query
        target:
          entity_id: sensor.customer
        data:
          query: >
            SELECT name, city, phone FROM customers
            WHERE id = {{ states('input_text.customer_id') | int }}
```

## Actions

### `ha_mysql.set_query`

Replaces the query of a sensor and refreshes it right away.

| Field | Required | Description |
|---|---|---|
| `entity_id` | yes | The sensor or sensors to change |
| `query` | no | The query to run from now on. Leave it out, or empty, to restore the query from the configuration |

```yaml
action: ha_mysql.set_query
target:
  entity_id: sensor.department
data:
  query: SELECT 'Hello Friends' FROM DUAL
```

The replacement lasts until it is replaced again, or until Home Assistant restarts. The selected row is reset to the first one.

### `ha_mysql.select_record`

Chooses which row of the result set is exposed through the `valueof_*` attributes.

| Field | Required | Description |
|---|---|---|
| `entity_id` | yes | The sensor or sensors to change |
| `rownumber` | yes | Zero based index of the row. The first row is `0`, the last one is the row count minus 1 |

```yaml
action: ha_mysql.select_record
target:
  entity_id: sensor.emp
data:
  rownumber: 1
```

A row number beyond the result set falls back to the first row and is reported in the log.

## Troubleshooting

### The sensor is `unavailable`

The last query failed. The integration keeps trying on every interval and recovers on its own once the database answers again. Turn on debug logging to see the reason:

```yaml
logger:
  default: warning
  logs:
    custom_components.ha_mysql: debug
```

### The integration does not start

| Message | Cause |
|---|---|
| Could not reach the server | Wrong host or port, or a firewall in between. Check with `telnet <host> 3306` from the Home Assistant machine |
| The server refused the username or password | Wrong credentials, or the user is not allowed to connect from this host. MySQL rights are per host: `'user'@'localhost'` is not the same as `'user'@'%'` |
| The database does not exist | Wrong database name, or the user has no rights on it |

### The state is `unknown`

* The column in `value_column` is not part of the result. The log lists the columns that are available.
* The value is not numeric while a unit, device class or state class is set.
* The query returned no rows at all. In that case `row_count` is `0` and `selected_row` is `-1`.

### The state stays the same while the data changed

Queries run every `scan_interval` seconds, 30 by default. Lower it, or call `homeassistant.update_entity` to force a refresh.

### There are no statistics or the energy dashboard does not accept the sensor

Statistics need a numeric state with a `state_class`. Set `value_column` or `value_template` together with `state_class` and `unit_of_measurement`.

### The database or the recorder is growing quickly

`json_result` holds the complete result set and is written on every update. With large results this fills the recorder database. Limit the rows with `max_json_rows`, and keep the attributes out of the recorder:

```yaml
recorder:
  exclude:
    entity_globs:
      - sensor.my_mysql_sensor
```

### The log says the value was cut off

A state can hold at most 255 characters. Longer values are truncated. Shorten the value in SQL, or move it into an attribute instead.

### Too many connections

Each connection uses a pool of at most five connections, shared by all sensors of that connection. If your server is tight on `max_connections`, raise the server limit or spread the sensors over longer intervals.

## Notes

* All sensors of one connection share the same connection pool, so adding sensors does not add connections.
* A connection that the server dropped, for example after `wait_timeout`, is rebuilt automatically. A restart of Home Assistant is not needed.
* Queries run in the background and never block Home Assistant.
* Only read the database. `INSERT`, `UPDATE` and `DELETE` are not supported and the query runs on every interval.

## Multiple databases

One database can be configured per connection, but the integration can be added more than once, each with its own database. A query can also read from another database on the same server by qualifying the table name:

```yaml
sensor:
  - platform: ha_mysql
    name: Departments
    query: SELECT * FROM personnel.dept
```

The user has to have `SELECT` rights on that database as well.

[hacs_shield]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square
[hacs]: https://github.com/hacs/integration
[latest_release]: https://github.com/IAsDoubleYou/ha_mysql/releases/latest
[releases_shield]: https://img.shields.io/github/v/release/IAsDoubleYou/ha_mysql?style=flat-square
[releases]: https://github.com/IAsDoubleYou/ha_mysql/releases/
[downloads_total_shield]: https://img.shields.io/github/downloads/IAsDoubleYou/ha_mysql/total?style=flat-square
[downloads_latest_shield]: https://img.shields.io/github/downloads/IAsDoubleYou/ha_mysql/latest/total?style=flat-square
[tests_shield]: https://img.shields.io/github/actions/workflow/status/IAsDoubleYou/ha_mysql/tests.yaml?branch=main&label=tests&style=flat-square
[tests]: https://github.com/IAsDoubleYou/ha_mysql/actions/workflows/tests.yaml
[community_forum_shield]: https://img.shields.io/static/v1.svg?label=%20&message=Forum&style=flat-square&color=41bdf5&logo=HomeAssistant&logoColor=white
[community_forum]: https://community.home-assistant.io/t/mysql-query/734346
