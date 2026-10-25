# HA MySQL Sensor for Home Assistant

[![HACS Custom][hacs_shield]][hacs]
[![GitHub Latest Release][releases_shield]][latest_release]
[![GitHub Downloads (latest Release)][downloads_latest_shield]][latest_release]
[![GitHub All Releases][downloads_total_shield]][releases]
[![Tests][tests_shield]][tests]
[![Community Forum][community_forum_shield]][community_forum]
[![GitHub Stars](https://img.shields.io/github/stars/IAsDoubleYou/ha_mysql?style=social)](https://github.com/IAsDoubleYou/ha_mysql/stargazers)

> **Questions, ideas or want to show what you built?** Join the conversation in [GitHub Discussions](https://github.com/IAsDoubleYou/ha_mysql/discussions).

> **Looking to run one-off queries or write to the database, instead of tracking a value continuously?** See [MySQL Query](https://github.com/IAsDoubleYou/homeassistant-mysql_query), a sibling integration built for that — full comparison at the [bottom of this README](#ha-mysql-or-mysql-query).

A Home Assistant custom integration that turns the result of a MySQL or MariaDB query into a sensor. Every sensor runs its own query on its own interval, and the result is available as the **state**, as the columns of one selected row in **`valueof_*` attributes**, and as the whole result set in the **`json_result` attribute**. A query can be replaced at runtime with [`ha_mysql.set_query`](#ha_mysqlset_query).

## Why HA MySQL?

- **Asynchronous**: built for MySQL and MariaDB on an asynchronous driver, with one shared pool per connection and a timeout on every query, so a slow database never holds up Home Assistant.
- **Strictly read only**: queries are checked before they run, and every session is read only, so the database itself refuses a write.
- **Diagnostics**: download a file with the settings, queries and pool status of a connection, with the credentials masked, when something goes wrong.

## What is the state of the sensor?

**By default the state is the number of rows the query returned**, not a value from the result set. This surprises most people at first.

```sql
SELECT name, salary FROM emp
```

| | Value |
|---|---|
| State | `2` (two rows) |
| `valueof_name` | `Alice` |
| `valueof_salary` | `1000.50` |

For an actual measurement as the state, set [`value_column`](#configuration) or [`value_template`](#configuration). That is also what the energy dashboard and long term statistics need.

## Requirements

- Home Assistant 2026.9.0 or newer.
- MySQL 5.7+ or MariaDB 10.3+, reachable from the machine running Home Assistant.
- The `aiomysql` driver, which Home Assistant installs automatically.

Note: This integration closely follows the latest Home Assistant releases. Keeping Home Assistant up-to-date is recommended to receive the newest updates and features of this component.

The integration only reads, so a user with `SELECT` rights on the tables you query is enough:

```sql
CREATE USER 'homeassistant'@'%' IDENTIFIED BY 'a-good-password';
GRANT SELECT ON mydatabase.* TO 'homeassistant'@'%';
```

## Installation

### Using [HACS](https://hacs.xyz/)

- Add `https://github.com/IAsDoubleYou/ha_mysql` as a [custom repository](https://hacs.xyz/docs/faq/custom_repositories/) of type Integration.
- Download **HA MySQL** and restart Home Assistant.

### Manual

- Download `homeassistant-ha_mysql.zip` from the [latest release](https://github.com/IAsDoubleYou/ha_mysql/releases/latest).
- Extract it straight into `custom_components/ha_mysql` (the archive has no top-level folder) and restart Home Assistant.

---

## Configuration

1. Go to **Settings → Devices & services → Add integration** and search for **HA MySQL**.
2. Fill in the connection. It is tested before it is saved, so mistakes are reported right away.
3. Open **Configure** on the integration card to add sensors. Changes take effect immediately, without a restart.

Every connection becomes one device with its sensors underneath it. Add the integration once per database.

| Setting | Key | Default | Where | Description |
|---|---|---|---|---|
| Host | `host` | | Connection | Host name or IP address of the server |
| Port | `port` | `3306` | Connection | Port the server listens on |
| Username | `username` | | Connection | User the queries run as |
| Password | `password` | | Connection | Password of that user. Leave empty on **Reconfigure** to keep it |
| Database name | `database` | | Connection | Database the connection opens |
| Encrypt the connection (TLS) | `use_tls` | off | Connection | See [Encrypting the connection](#encrypting-the-connection) |
| Name | `name` | | Sensor | Name of the sensor, combined with the device name for its entity ID |
| Query | `query` | | Sensor | One `SELECT`-style statement |
| Update interval (seconds) | `scan_interval` | `30` | Sensor | Seconds between two runs of the query |
| Value column | `value_column` | | Sensor | Column of the selected row to use as the state, instead of the row count |
| Value template | `value_template` | | Sensor | Template that produces the state. Takes precedence over the value column |
| Unit of measurement | `unit_of_measurement` | | Sensor | For example `kWh` or `°C` |
| Device class | `device_class` | | Sensor | A [sensor device class](https://www.home-assistant.io/integrations/sensor/#device-class), for example `energy` |
| State class | `state_class` | | Sensor | `measurement`, `total` or `total_increasing`. Needed for long term statistics |
| Display precision | `suggested_display_precision` | | Sensor | Number of decimals shown |
| Maximum rows in json_result | `max_json_rows` | `0` | Sensor | `0` keeps all rows |

The connection (host, port, username, password, database and TLS) is changed with **Reconfigure** in the **⋮** menu of the connection; the new settings are tested first. When the database refuses the stored credentials, Home Assistant asks you to sign in again and updates the connection instead of adding a second one. Sensors are added, edited and removed through **Configure**.

If `unit_of_measurement`, `state_class` or `suggested_display_precision` is set, the state has to be numeric. A value that cannot be read as a number becomes `unknown` and is reported once in the log.

### Encrypting the connection

TLS is off by default. With **Encrypt the connection (TLS)** on, the traffic, including the password, can no longer be read on the network. The server's certificate is **not verified**, because home databases nearly always use a self signed one, so this protects against eavesdropping but not against someone who actively intercepts the connection.

The server must support TLS. If it does not, the connection is refused with a message instead of quietly falling back to plain text. `SHOW GLOBAL VARIABLES LIKE 'have_ssl';` returns `YES` when it does.

### Several databases

Add the integration once per database. A query can also read another database on the same server by qualifying the table, as in `SELECT * FROM personnel.dept`, if the user has `SELECT` rights there.

---

## Read only by design

This is a sensor integration, so it never writes, and that is enforced twice:

- **The query is checked** before it is stored or run, wherever it comes from. More than one statement, anything that is not `SELECT`-style, or a `SET`, is refused with the reason in the form or the log.
- **Every session is read only** (`SET SESSION TRANSACTION READ ONLY` before the first query, also after a dropped connection is rebuilt). The database itself then refuses an `INSERT`, `UPDATE`, `DELETE` or temporary table with error 1792.

Neither is a full security boundary: a `SELECT` can still write a file with `INTO OUTFILE` when the user has the `FILE` privilege. The database user having only `SELECT` rights, as shown above, is what actually stops a write.

## Results

### Template variables

`value_template` is rendered with `row` (the selected row as a dictionary, or `none` when empty), `rows` (all rows) and `row_count`.

| Value template | Result set | State |
|---|---|---|
| `{{ row.salary \| float * 1.21 }}` | `row.salary` is `1000.50` | `1210.605` |
| `{{ rows \| map(attribute='kwh') \| map('float') \| sum }}` | `kwh` 1.2, 0.8 and 2.1 | `4.1` |
| `{{ 'busy' if row_count > 10 else 'quiet' }}` | 2 rows, or 15 rows | `quiet`, or `busy` |

### Attributes

| Attribute | Description |
|---|---|
| `valueof_<column>` | The value of that column in the selected row, one attribute per column. The prefix avoids clashes such as `friendly_name` |
| `selected_row` | Index of the selected row, or `-1` when the result is empty |
| `row_count` | Number of rows the query returned |
| `json_result` | The complete result set as a JSON string, or `{}` when empty |
| `json_result_truncated` | Only present, and `true`, when `max_json_rows` cut the result short |
| `executed_sql_query` | The query that produced the result, including one set with `set_query` |
| `query_date`, `query_time` | When the query ran, as `YYYY-MM-DD` and `HH:MM:SS` |

`json_result`, `json_result_truncated`, `executed_sql_query`, `query_date` and `query_time` are in the current state but not in the history, because the recorder leaves them out. Otherwise every run would store a new copy of the result set.

### Column types

| Column type | Becomes |
|---|---|
| `DECIMAL`, `NUMERIC` | A string such as `1000.50`. Use `float` in a template to calculate with it |
| `BINARY`, `VARBINARY`, `BLOB` | The text it holds, or a short hexadecimal preview such as `0x89504e47...` when it is not valid UTF-8 |
| `TIME` | A readable duration such as `1:30:00` |
| `SET` | A sorted list of the selected members |
| `DATE`, `DATETIME`, `TIMESTAMP` | Left as they are, so `device_class: date` and `timestamp` work |
| `NULL` | `None`, shown as `unknown` in the state |

Do not select an image or another large binary value. Select a length or checksum instead, for example `SELECT LENGTH(photo) AS bytes FROM staff`.

---

## Examples

Sensors are added through **Configure → Add a sensor**. Fields that are not listed keep their default.

### Daily energy consumption

```sql
SELECT ROUND(SUM(kwh), 3) AS total FROM energy_log WHERE DATE(logged_at) = CURDATE()
```

Set the update interval to `300`, the value column to `total`, the unit to `kWh`, the device class to `energy` and the state class to `total_increasing`. The sensor can then be added under **Settings → Dashboards → Energy** as a consumption source.

### Counting rows

The default behaviour, useful for queues, open orders or unread messages.

```sql
SELECT id, customer, total FROM orders WHERE status = 'open'
```

The state is the number of open orders and `valueof_customer` shows the first one. Use [`ha_mysql.select_record`](#ha_mysqlselect_record) to walk through the others.

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

The value is bound to the `%s` placeholder instead of being pasted into the query, so whatever the helper holds cannot change what the statement does.

---

## Actions

### `ha_mysql.set_query`

Replaces the query of a sensor and refreshes it right away. Only a single reading statement is allowed, as everywhere else a query enters ha_mysql.

| Field | Required | Description |
|---|---|---|
| `entity_id` | yes | The sensor or sensors to change |
| `query` | no | The query to run from now on. Leave it out, or empty, to restore the configured query |
| `values` | no | Values for the `%s` placeholders in `query`, in order. Requires a new `query` in the same call |
| `persist` | no | Keep the replacement (and its `values`) across a restart. Off by default |

By default the replacement lasts until it is replaced again or Home Assistant restarts, after which the sensor runs its configured query again. With `persist: true` it survives the restart, which suits a sensor that only shows the result of a runtime lookup. Restoring the configured query always drops a persisted replacement. The selected row is reset to the first one, and the active query is always readable in `executed_sql_query`.

```yaml
action: ha_mysql.set_query
target:
  entity_id: sensor.last_caller
data:
  query: SELECT announcement FROM contact WHERE phonenumber = %s
  values:
    - "{{ trigger.event.data.phonenumber }}"
  persist: true
```

#### Parameterized queries with `values`

Write a `%s` placeholder for each value and list the values in the same order. They travel to MySQL separately from the query text and the driver escapes each one, so a quote or semicolon in the data cannot change the statement.

- Home Assistant renders templates in a value before the action runs, so a template that produces a number is bound as a number. The integration does not render values itself: a call through the REST API with `{{ ... }}` in a value binds that text.
- A `%s` stands for exactly one value, never a list. Write one placeholder per value (`IN (%s, %s)`); the counts must match.
- Once `values` is used, a literal `%` in the query must be doubled, or passed as a value:

```yaml
query: "SELECT * FROM emp WHERE name LIKE '%%kitchen%%' AND active = %s"   # doubled
query: "SELECT * FROM emp WHERE name LIKE %s AND active = %s"              # or a value
values: ["%kitchen%", 1]
```

Without `values`, nothing in the query is interpreted and `LIKE '%kitchen%'` works as usual.

### `ha_mysql.select_record`

Chooses which row of the result set is exposed through the `valueof_*` attributes.

| Field | Required | Description |
|---|---|---|
| `entity_id` | yes | The sensor or sensors to change |
| `rownumber` | yes | Zero based index of the row. A number beyond the result falls back to the first row and is logged |

```yaml
action: ha_mysql.select_record
target:
  entity_id: sensor.emp
data:
  rownumber: 1
```

---

## Diagnostics

When you report a problem, download the diagnostics: **Settings** > **Devices & services** > **HA MySQL** > the **⋮** menu of the connection > **Download diagnostics**. The file holds the connection settings, the configuration and queries of every sensor, the figures of the last run of each sensor and the size of the connection pool.

The host, port, username, password and database name are masked, and so are the title and unique ID of the connection. No row of the database is included. **The queries are**, because they are usually what needs looking at, so read them before you share the file.

## Troubleshooting

Turn on debug logging to see why a query fails:

```yaml
logger:
  default: warning
  logs:
    custom_components.ha_mysql: debug
```

| Symptom | Cause and fix |
|---|---|
| Sensor is `unavailable` | The last query failed. It is retried on every interval and recovers when the database answers again |
| Could not connect | Wrong host or port, a firewall, or the server is down or starting. Home Assistant tries again by itself. Check with `telnet <host> 3306` from the Home Assistant machine |
| The database refused the username or password | Wrong or changed credentials. Home Assistant asks you to sign in again |
| The database does not exist, or its user has no access | Wrong name or missing rights. Correct it with **Reconfigure** |
| The database server refused the connection | Not retried, because waiting does not fix it. MySQL rights are per host (`'user'@'localhost'` is not `'user'@'%'`), or the account is locked or its password expired |
| Set to use TLS, but the server accepted the connection without encrypting it | Configure TLS on the database, or turn the option off with **Reconfigure** |
| "Only SELECT-style statements are allowed" or "Only one statement is allowed" | ha_mysql only reads, see [Read only by design](#read-only-by-design). Split statements into separate sensors and replace writes with a `SELECT` |
| State is `unknown` | The `value_column` is not in the result (the log lists the columns), the value is not numeric while a unit or class is set, or the query returned no rows |
| State does not follow the data | Queries run every `scan_interval` seconds. Lower it, or call `homeassistant.update_entity` |
| Energy dashboard or statistics reject the sensor | They need a numeric state with a `state_class`. Set `value_column` or `value_template` with `state_class` and `unit_of_measurement` |
| Database or recorder grows quickly | A large `json_result` sits in the current state. Limit it with `max_json_rows` or narrow the query. Do not exclude the whole sensor from the recorder: that also drops its history and statistics |
| Log says the value was cut off | A state holds at most 255 characters. Shorten the value in SQL or use an attribute |
| Too many connections | Each connection uses at most ten, shared by its sensors. Raise `max_connections` on the server or use longer intervals |

## Upgrading from 1.x

Release 2.0.0 no longer reads `configuration.yaml`. The connection and sensors that earlier releases imported from it keep working, with the same entity IDs and history. Remove the `ha_mysql:` section and the `platform: ha_mysql` sensors from `configuration.yaml`.

The imported sensors are those from the last start with an earlier release, so look at them under **Configure**. A YAML configuration that was never imported is not picked up: add it through the user interface.

---

## HA MySQL or MySQL Query?

HA MySQL creates sensors that follow a query on an interval, SELECT only. MySQL Query runs a query only when you call the action, can write as well, and returns the result as response data. They can be installed side by side.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for a full list of changes per version, including the migration steps of older releases, and the [GitHub releases](https://github.com/IAsDoubleYou/ha_mysql/releases) for the release notes.

## Support

If you enjoy using this integration, please consider giving it a ⭐ on [GitHub](https://github.com/IAsDoubleYou/ha_mysql)! It costs nothing, shows your appreciation, and helps other Home Assistant users find this integration more easily.

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
