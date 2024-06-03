[![HACS Custom][hacs_shield]][hacs]
[![GitHub Latest Release][releases_shield]][latest_release]
[![GitHub All Releases][downloads_total_shield]][releases]
[![Community Forum][community_forum_shield]][community_forum]

[hacs_shield]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge
[hacs]: https://github.com/hacs/integration

[latest_release]: https://github.com/IAsDoubleYou/homeassistant-ha_mysql/releases/latest
[releases_shield]: https://img.shields.io/github/release/IAsDoubleYou/ha_mysql.svg?style=for-the-badge

[releases]: https://github.com/IAsDoubleYou/ha_mysql/releases/
[downloads_total_shield]: https://img.shields.io/github/downloads/IAsDoubleYou/ha_mysql/total?style=for-the-badge

[community_forum_shield]: https://img.shields.io/static/v1.svg?label=%20&message=Forum&style=for-the-badge&color=41bdf5&logo=HomeAssistant&logoColor=white
[community_forum]: https://community.home-assistant.io/t/mysql-query/734346

# ha_mysql
Custom component that provides a HA sensor to retrieve the result of a specified MySQL Query

The component creates sensors for each default query that is defined in the configuration.yaml.
The query can be overruled at run time when executing the **ha_mysql.execute** function

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
4. Download _all_ the files from the `custom_components/mysql_query/` directory (folder) of this repository.
5. Place the files you downloaded in the new directory (folder) you created.
6. Restart Home Assistant
7. Apply the <i>configuration</i> as described below
8. Restart Home Assistant once more

## Configuration
The MySQL database configuration should be added as follow in configuration.yaml:
```text
ha_mysql:
  mysql_host: <mysqldb host ip address>
  mysql_port: <mysql port>
  mysql_username: <mysqldb username>
  mysql_password: <mysqldb password>

sensor:
- platform: ha_mysql
  name: <desired query name>
  friendly_name: <friendly name for the query>
  mysql_db: <mysqldb databasename>
  default_query: <default query>
```
The port number (mysql_port) is optional and defaults to 3306

<b>Examples:</b><br>
```text
- platform: ha_mysql
  name: Query Employees
  friendly_name: "Employees"
  mysql_db: testdb
  default_query: SELECT * FROM emp
- platform: ha_mysql
  name: Query Departments
  friendly_name: "Departments"
  mysql_db: testdb
  default_query: SELECT * FROM dept

## Services
1. ha_mysql.execute (ready)
2. ha_mysql.next (TBD)
3. ha_mysql.previous (TBD)
4. ha_mysql.first (TBD)
5. ha_mysql.last (TBD)
6. ha_mysql.goto (TBD)
   
## Usage
### ha_mysql.execute
The service should be called by passing the entity_id of the sensor and optionally a query parameter which wil replace the default_query of the sensor.
Being able to replacing the default query can be useful to build dynamic quries that depends on information that only becomes available at runtime.

### Request
<b>Examples:</b><br>
```text

**HIER VERDER**

service: mysql_query.query
data:
  query: select * from contact where phonenumber='1234567890'
  db4query: privatedb

service: mysql_query.query
data:
  query: select * from contact where phonenumber like '0%'

service: mysql_query.query
data:
  query: |
    with cte_query as (select 'Hello World' from dual)
    select * from cte_query
```

### Response
If the query achieves a result, this will be returned as a collection.
<b>Example response in YAML format:</b><br>
```text
result:
  - phonenumber: "0111111111"
    announcement: Announcement for phonenumber 0111111111
    language: en
  - phonenumber: "0222222222"
    announcement: Announcement for phonenumber 0222222222
    language: en
```

### Usage from automation
<b>An example of how to use this service and it's response from within an automation:</b><br>
```text
alias: mysql_query test
description: ""
trigger: []
condition: []
action:
  - variables:
      response: null
  - service: mysql_query.query
    data:
      query: select * from contact
    response_variable: response
  - service: notify.your_gmail_com
    data:
      message: |-
        Response from MySQL Query Service:

        {%for item in response.result %}
            {{ item.phonenumber }}<br>
            {{ item.announcement }}<br>
            {{ item.language }}<br><br>
        {% endfor %}
      title: Test of MySQL Query Service
      target: youraccount@gmail.com
mode: single
```
## Multiple databases
The database configured with the mysql_db configuration parameter in configuration.yaml acts as the default database for each query.
However,the default database can be overridden for each query by providing <b>db4query</b> alongside the query parameter.

Example:
```text
service: mysql_query.query
data:
  query: select "hello world" from dual
  db4query: contact
```
The query from this example will be executed against the contact database, although the default database specified by the mysql_db configuration parameter may be a complete different database.


