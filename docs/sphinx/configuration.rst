Configuration Reference
=======================

Environment Variables
---------------------

Database connectivity uses:

- ``ORACLE_USER``
- ``ORACLE_PASSWORD``
- ``ORACLE_DSN``

These are consumed by ``src.database.connection.OracleConnection.from_env``.

Configuration Files
-------------------

Default project configuration assets are under ``config/``:

- ``config/mappings``: mapping documents (JSON)
- ``config/rules``: business rule configurations
- ``config/thresholds.json``: comparator thresholds
- ``config/templates``: CSV/XLSX/JSON templates
- ``config/schemas/universal_mapping_schema.json``: mapping schema

Mapping Document Keys
---------------------

Common keys expected by mapping parser:

- ``mapping_name``
- ``version``
- ``description``
- ``source``
- ``target``
- ``mappings`` (list of column mapping objects)
- ``key_columns``

Each mapping entry commonly includes:

- ``source_column``
- ``target_column``
- ``data_type``
- ``required``
- ``transformations``
- ``validation_rules``

Reconciliation Output Fields
----------------------------

Reconciliation command outputs include:

- ``valid``
- ``errors`` / ``warnings``
- ``error_count`` / ``warning_count``
- ``table_name``
- ``mapped_columns``
- ``database_columns``
- ``unmapped_required``
