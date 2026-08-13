[![HACS Custom][hacs_shield]][hacs]
[![GitHub Latest Release][releases_shield]][latest_release]
[![GitHub All Releases][downloads_total_shield]][releases]
[![Community Forum][community_forum_shield]][community_forum]

[hacs_shield]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge
[hacs]: https://github.com/hacs/integration

[latest_release]: https://github.com/IAsDoubleYou/ha_mysql/releases/latest
[releases_shield]: https://img.shields.io/github/release/IAsDoubleYou/ha_mysql.svg?style=for-the-badge

[releases]: https://github.com/IAsDoubleYou/ha_mysql/releases/
[downloads_total_shield]: https://img.shields.io/github/downloads/IAsDoubleYou/ha_mysql/total?style=for-the-badge

[community_forum_shield]: https://img.shields.io/static/v1.svg?label=%20&message=Forum&style=for-the-badge&color=41bdf5&logo=HomeAssistant&logoColor=white
[community_forum]: https://community.home-assistant.io/t/mysql-query/734346

# HA MySQL
Custom component that provides a HA sensor to retrieve the result of a specified MySQL Query

The component creates a sensor for each (initial) query that is defined in the configuration.yaml.
The query can be overriden at run time when executing the **ha_mysql.set_query** function

Please checkout [MySQL Query](https://github.com/IAsDoubleYou/homeassistant-mysql_query) component for a mysql *service* component.

## HA MySQL usage
The query should be written in the form:
```text
select col1, col2, .... from table where condition
```

<b>Examples:</b><br>
```text
  select * from contacts
  select name, phonenumber from contacts
```
## Requirements
(TBD)

## Installation

### Using [HACS](https://hacs.xyz/)
This component can be installed using HACS. Please follow directions [here](https://hacs.xyz/docs/faq/custom_repositories/) and use [https://github.com/IAsDoubleYou/ha_mysql](https://github.com/IAsDoubleYou/ha_mysql) as the repository URL.

### Manual

1. Using the tool of choice open the directory (folder) for your HA configuration (where you find `configuration.yaml`).
2. If you do not have a `custom_components` directory (folder) there, you need to create it.
3. In the `custom_components` directory (folder) create a new folder called `ha_mysql`.
4. Download _all_ the files from the `custom_components/ha_mysql/` directory (folder) of this repository.
5. Place the files you downloaded in the new directory (folder) you created.
6. Restart Home Assistant
7. Apply the <i>configuration</i> as described below
8. Restart Home Assistant once more

## Configuration
The MySQL database configuration should be added as follow in configuration.yaml:
```text
ha_mysql:
  host: <mysqldb host ip address>
  port: <mysql port>
  username: <mysqldb username>
  password: <mysqldb password>
  database: <mysqldb database>

sensor:
- platform: ha_mysql
  name: <desired query name>
  query: <initial query>
```
The port number is optional and defaults to 3306. Both `name` and `query` are required for every sensor.

<b>Examples:</b><br>
```yaml
sensor:
  - platform: ha_mysql
    name: Employees
    query: SELECT * FROM emp
  - platform: ha_mysql
    name: Departments
    query: SELECT * FROM dept
    scan_interval: 300
```

The optional `scan_interval` (in seconds) controls how often the query runs and defaults to 30 seconds.

## Services
1. ha_mysql.set_query (entity_id, query)
2. ha_mysql.select_record (entity_id, rownumber)
   
## Usage
### ha_mysql.set_query
The service should be called by passing the entity_id of the sensor and a query parameter which wil replace the initial query of the sensor.
Being able to replace the initial query can be useful to build dynamic quries that depends on information that only becomes available at runtime.

### Request
<b>Examples:</b><br>
```yaml
action: ha_mysql.set_query
target:
  entity_id: sensor.department
data:
  query: SELECT 'Hello Friends' FROM DUAL
```

Calling the service without a `query`, or with an empty one, restores the query from `configuration.yaml`. The sensor is refreshed immediately after the query is changed.

### ha_mysql.select_record
The service should be called by passing the entity_id of the sensor and a rownumber parameter. The rownumber specifies the number of the row of the resultset to activate. This will cause the fields of the selected record to become available as attributes with names that correspond to the names of the selected columns, prefixed by the string `valueof_`. This prefix is used to avoid collision with other static attributes like query_date, query_time, friendly_name etc.
For example: if the selection list of the query contains a field named `friendly_name`, this will become available as a dynamic attribute with the name `valueof_friendly_name`.
Be aware that the first row is rownumber 0 and the last row is the value of the state - 1.
In the example below the *second* row (rownumber: 1) will be selected.

```yaml
action: ha_mysql.select_record
target:
  entity_id: sensor.emp
data:
  rownumber: 1
```

## Multiple databases (not implemented yet)
> **Note:** this is a planned feature. It is **not available** in the current release; the `database` option from `configuration.yaml` is used for every query.

The idea is that the database configured with the `database` configuration parameter acts as the default database for each query, and that this default can be overridden per query.

A query can still read from another database on the same server by qualifying the table name:

```yaml
sensor:
  - platform: ha_mysql
    name: Departments
    query: SELECT * FROM personnel.dept
```


