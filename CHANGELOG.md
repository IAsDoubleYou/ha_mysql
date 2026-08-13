# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-08-13

This release rebuilds the integration on the modern Home Assistant building
blocks. Existing `configuration.yaml` setups keep working, and their sensors
keep the entity IDs and the history they already had.

### Added

- Setup through the user interface. **Settings → Devices & services → Add
  integration → HA MySQL** asks for the connection details and tests them
  before the entry is saved.
- An options flow to add, edit and remove sensors from the integration card.
  Changes take effect immediately, without restarting Home Assistant.
- Every connection now shows up as a device, with all of its sensors grouped
  underneath it.
- `value_column` and `value_template` to use a value from the result set as
  the state of the sensor, instead of the number of rows.
- `unit_of_measurement`, `device_class`, `state_class` and
  `suggested_display_precision`, which make the sensors usable for long term
  statistics and the energy dashboard.
- `max_json_rows` to limit the number of rows stored in the `json_result`
  attribute, together with a `json_result_truncated` attribute that says when
  the result was cut short.
- A warning in the log when a query returns more than 1000 rows while
  `max_json_rows` is not set, because the whole result set is written into the
  recorder on every update.
- A `row_count` attribute, so the number of rows stays available even when the
  state holds a value from the result set.
- English and Dutch translations for the configuration screens.
- A test suite, and continuous integration running pytest, hassfest, ruff and
  the HACS validation.

### Changed

- The sensor platform is built on a `DataUpdateCoordinator`. Every sensor polls
  on its own `scan_interval`, and all sensors of one connection share a pool of
  at most five connections.
- Sensors defined in `configuration.yaml` are imported into a config entry on
  every start, so the YAML file stays the source of truth for those sensors
  while sensors added through the interface are left untouched.
- A sensor whose query fails becomes `unavailable` and recovers on its own once
  the database answers again, instead of keeping a stale state.
- A failing query no longer stops the other sensors of the same connection from
  loading.
- Wrong credentials or a missing database now fail the setup with a clear
  message, while an unreachable server is retried.
- `BINARY`, `VARBINARY` and `BLOB` columns are decoded as UTF-8 text. Values
  that are not text become a short hexadecimal preview instead of raw bytes.
  `TIME` columns become a readable duration and `SET` columns become a list, so
  both survive the trip into an attribute and into `json_result`.
- A state longer than the 255 characters Home Assistant allows is truncated,
  and reported once in the log instead of on every update.
- `ha_mysql.set_query` and `ha_mysql.select_record` are entity services now, so
  they work with `entity_id`, areas, devices and labels, and they can address
  more than one sensor at a time.
- Sensors that are removed from the configuration also disappear from the
  entity registry.
- Every release now ships a `homeassistant-ha_mysql.zip`, which is what HACS
  installs and what a manual installation can be unpacked from.

### Fixed

- A connection that the server dropped, for example after `wait_timeout`, is
  rebuilt automatically. Previously the single connection was opened once at
  startup and every later query failed until Home Assistant was restarted.
- Queries now run with autocommit enabled. Without it the open transaction made
  InnoDB return the same snapshot on every poll, so the sensor never saw new
  rows.
- `ha_mysql.select_record` actually selects the row it is given. The
  `valueof_*` attributes always showed the first row of the result set.
- `ha_mysql.set_query` restores the configured query when it is called without
  one, and refreshes the sensor right away.
- A row number beyond the end of the result set falls back to the first row and
  is reported in the log, instead of raising.

## Earlier releases

See the [releases page](https://github.com/IAsDoubleYou/ha_mysql/releases) for
the notes of 1.0.3 and older.

[1.1.0]: https://github.com/IAsDoubleYou/ha_mysql/releases/tag/v1.1.0
