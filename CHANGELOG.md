# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-08-15

This release is about connections that stop answering. Sensors of one
connection share a pool, and a query that never returned kept its connection
for good, until the pool was empty and every sensor was `unavailable`. It also
makes the configuration screens say what actually went wrong.

### Added

- Adding or editing a sensor runs its query once against the database before
  the sensor is saved. A query the server refuses is shown on the form
  together with the message from the database, so a typo, a missing table or a
  missing column is caught before the sensor exists. A query that returns no
  rows only warns: submitting it again saves it, because an empty result can
  be perfectly valid.
- An unexpected error in the connection step and in the query test now shows
  the message behind it, instead of a bare "unexpected error". Errors without
  a message fall back to their type, and long messages are shortened to 255
  characters.
- A refused connection, a timeout and a failed TLS handshake all mean
  "cannot connect" while each needs a different fix, so the connection step
  now shows the reason from the driver alongside the generic advice.

### Changed

- The pool holds ten connections instead of five, and a query waits up to five
  seconds for a free one instead of failing the moment the pool is full.
- A pool that stays full is reported as a connection problem rather than as an
  unexpected error.
- Testing the connection settings uses a single connection instead of building
  a whole pool. That check runs on every setup and on every submitted form,
  including the retries of an entry that is not ready yet.
- The TLS settings are spelled out instead of left to the driver, so a driver
  update cannot start demanding a certificate that a database on a home
  network does not have.

### Fixed

- Every read and write is bounded now. The driver drops the connect timeout
  once the handshake is done, so a query that stopped getting answers, because
  a route dropped or a firewall forgot about the connection, held its
  connection forever. That is the one leak that no `try`/`finally` can close.
- A lost connection no longer throws away a pool that another sensor has just
  rebuilt. Sensors fail together, and the two kept replacing each other's
  connections.
- A connection is handed back on every path out of a query now: after a
  successful one, after a failed one and after an unexpected exception.
- Timeouts from the driver are recognised as connection problems. They were
  counted as queries the server had rejected, which failed the config entry
  for good instead of retrying it.

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

[1.2.0]: https://github.com/IAsDoubleYou/ha_mysql/releases/tag/v1.2.0
[1.1.0]: https://github.com/IAsDoubleYou/ha_mysql/releases/tag/v1.1.0
