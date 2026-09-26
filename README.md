# HA MySQL Sensor for Home Assistant

[![HACS Custom][hacs_shield]][hacs]
[![GitHub Latest Release][releases_shield]][latest_release]
[![GitHub Downloads (latest Release)][downloads_latest_shield]][latest_release]
[![GitHub All Releases][downloads_total_shield]][releases]
[![Tests][tests_shield]][tests]
[![Community Forum][community_forum_shield]][community_forum]

> **Questions, ideas or want to show what you built?** Join the conversation in [GitHub Discussions](https://github.com/IAsDoubleYou/ha_mysql/discussions).

> **Looking to run one-off queries or write to the database, instead of tracking a value continuously?** See [MySQL Query](https://github.com/IAsDoubleYou/homeassistant-mysql_query), a sibling integration built for that — full comparison at the [bottom of this README](#ha-mysql-or-mysql-query).

Home Assistant custom integration that turns the result of a MySQL or MariaDB query into a sensor.

Every sensor runs its own query on its own interval. The result is available in three ways:

* the **state** of the sensor,
* the columns of one selected row as **`valueof_*` attributes**,
* the complete result set as JSON in the **`json_result` attribute**.

Queries can be replaced at runtime with the [`ha_mysql.set_query`](#ha_mysqlset_query) action, which makes it possible to build queries that depend on information only known at runtime.

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

## Read-only by design

This is a sensor integration, so it never writes: a query is checked, wherever it comes from — the user interface, `configuration.yaml` or [`ha_mysql.set_query`](#ha_mysqlset_query) — before it is stored or run. A query with more than one statement, or one that is not `SELECT`-style, is refused with the reason logged or shown on the form; nothing invalid is ever sent to the database.

**What this does not protect against.** The check reads the leading keyword of the query; it is not a full security boundary. A `SELECT` can still write through `INTO OUTFILE` or a stored function with side effects, and MySQL 8 accepts a CTE in front of an `UPDATE`. The database user having only `SELECT` rights, as shown above, is what actually stops a write.

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
4. Open **Configure** on the integration card to add sensors, or to change the database connection itself.

Every connection becomes one device, with all its sensors underneath it. Sensors are added, edited and removed through **Configure**; changes take effect immediately, without a restart. The host, port, username, password and database can be changed the same way, through **Configure → Change the database connection**; the new connection is tested before it replaces the old one.

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
| `name` | yes | | Name of the sensor. Combined with the connection's device name for a new sensor's entity ID and friendly name |
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
# row.salary "1000.50" -> state 1210.605

value_template: "{{ rows | map(attribute='kwh') | map('float') | sum }}"
# rows with kwh 1.2, 0.8 and 2.1 -> state 4.1

value_template: "{{ 'busy' if row_count > 10 else 'quiet' }}"
# row_count 2 -> state "quiet", row_count 15 -> state "busy"
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

Because of `device_class: energy` and `state_class: total_increasing`, it can be added under **Settings → Dashboards → Energy** as a consumption source.

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
          query: SELECT name, city, phone FROM customers WHERE id = %s
          values:
            - "{{ states('input_text.customer_id') }}"
```

The value is bound to the `%s` placeholder instead of being pasted into the query text, so whatever `input_text.customer_id` holds cannot change what the statement does. See [Parameterized queries](#parameterized-queries-with-values) below.

## Actions

### `ha_mysql.set_query`

Replaces the query of a sensor and refreshes it right away. Only a single, reading statement is allowed — the same restriction as everywhere else a query enters ha_mysql; see [Read-only by design](#read-only-by-design).

| Field | Required | Description |
|---|---|---|
| `entity_id` | yes | The sensor or sensors to change |
| `query` | no | The query to run from now on. Leave it out, or empty, to restore the query from the configuration |
| `values` | no | List of values for the `%s` placeholders in `query`, in the order they appear. Requires a new `query` in the same call — see [Parameterized queries](#parameterized-queries-with-values) |

```yaml
action: ha_mysql.set_query
target:
  entity_id: sensor.department
data:
  query: SELECT 'Hello Friends' FROM DUAL
```

The replacement lasts until it is replaced again, or until Home Assistant restarts. The selected row is reset to the first one. The query that is currently active is always available as the `executed_sql_query` attribute, so it can be read back and reused with new `values` later.

### Parameterized queries with `values`

`values` is optional. Leave it out and the query is sent exactly as written.

When you do use it, write a `%s` placeholder in the query for each value and list the values in the same order. The values then travel to MySQL separately from the query text, and the driver quotes and escapes each one according to its type. A quote, a semicolon or a stray backslash in the value can no longer change what the statement does:

```yaml
action: ha_mysql.set_query
target:
  entity_id: sensor.department
data:
  query: SELECT * FROM emp WHERE department = %s AND active = %s
  values:
    - "{{ states('input_text.department') }}"
    - 1
```

Every value is rendered through the Home Assistant template engine with native typing, so a template that produces a number or a boolean is bound as such instead of as text; a plain value without `{{ ... }}` is passed through untouched. A `%s` always stands for exactly one value, never a list — `WHERE id IN (%s)` with a list of ids does not work; write one placeholder per value instead. The number of placeholders and the number of values must match, or MySQL rejects the query.

As soon as `values` is used, the `%` character becomes special in the query text, the same way it would in Python string formatting. A literal percent sign then has to be doubled:

```yaml
# Wrong: the % of the LIKE pattern is read as a placeholder
query: "SELECT * FROM emp WHERE name LIKE '%kitchen%' AND active = %s"

# Correct: double the literal percent signs
query: "SELECT * FROM emp WHERE name LIKE '%%kitchen%%' AND active = %s"

# Better: pass the whole pattern as a value
query: "SELECT * FROM emp WHERE name LIKE %s AND active = %s"
values: ["%kitchen%", 1]
```

This only applies once `values` is present. Without it, nothing in the query is interpreted and `LIKE '%kitchen%'` works as usual.

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

### A query is refused with "Only SELECT-style statements are allowed" or "Only one statement is allowed in a query"

ha_mysql only reads; see [Read-only by design](#read-only-by-design). Split a query that contains more than one statement, separated by `;`, into separate sensors, and replace `INSERT`, `UPDATE`, `DELETE` or any other write with a `SELECT`.

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

Each connection uses a pool of at most ten connections, shared by all sensors of that connection. If your server is tight on `max_connections`, raise the server limit or spread the sensors over longer intervals.

## Notes

* All sensors of one connection share the same connection pool, so adding sensors does not add connections.
* A connection that the server dropped, for example after `wait_timeout`, is rebuilt automatically. A restart of Home Assistant is not needed.
* Queries run in the background and never block Home Assistant.
* Only reading statements are allowed; see [Read-only by design](#read-only-by-design).

## Multiple databases

One database can be configured per connection, but the integration can be added more than once, each with its own database. A query can also read from another database on the same server by qualifying the table name:

```yaml
sensor:
  - platform: ha_mysql
    name: Departments
    query: SELECT * FROM personnel.dept
```

The user has to have `SELECT` rights on that database as well.

## HA MySQL or MySQL Query?

Two integrations, two different jobs. They can be installed side by side.

| | **HA MySQL** (this repository) | **[MySQL Query](https://github.com/IAsDoubleYou/homeassistant-mysql_query)** |
|---|---|---|
| Approach | Automatic sensors | Actions (services) for scripts and automations |
| Runs a query | On its own interval, per sensor | Only when you call the action |
| Result ends up in | The state and the attributes of a sensor | The response of the action, and optionally in an event |
| Creates entities | Yes, one sensor per query | No |
| History and statistics | Yes, through the sensor | No |
| Writing to the database | No, `SELECT` only | Yes, `INSERT`, `UPDATE` and `DELETE` as well |
| Best for | Values you want to follow continuously, dashboards, the energy dashboard | Lookups on demand, queries with runtime parameters, changing data |

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
