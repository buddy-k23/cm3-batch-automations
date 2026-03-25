Python Modules API
==================

Core CLI
--------

.. automodule:: src.main
   :members:
   :undoc-members:
   :show-inheritance:

Commands
~~~~~~~~

.. automodule:: src.commands.db_compare
   :members:
   :undoc-members:

.. automodule:: src.commands.watch_command
   :members:
   :undoc-members:

.. automodule:: src.commands.run_tests_command
   :members:
   :undoc-members:

.. automodule:: src.commands.schedule_command
   :members:
   :undoc-members:

Database
--------

.. automodule:: src.database.connection
   :members:
   :undoc-members:

.. automodule:: src.database.query_executor
   :members:
   :undoc-members:

.. automodule:: src.database.reconciliation
   :members:
   :undoc-members:

.. automodule:: src.database.extractor
   :members:
   :undoc-members:

.. automodule:: src.database.transaction
   :members:
   :undoc-members:

.. automodule:: src.database.run_history
   :members:
   :undoc-members:

Parsers
-------

.. automodule:: src.parsers.format_detector
   :members:
   :undoc-members:

.. automodule:: src.parsers.pipe_delimited_parser
   :members:
   :undoc-members:

.. automodule:: src.parsers.fixed_width_parser
   :members:
   :undoc-members:

.. automodule:: src.parsers.chunked_parser
   :members:
   :undoc-members:

.. automodule:: src.parsers.validator
   :members:
   :undoc-members:

.. automodule:: src.parsers.enhanced_validator
   :members:
   :undoc-members:

.. automodule:: src.parsers.chunked_validator
   :members:
   :undoc-members:

Comparators
-----------

.. automodule:: src.comparators.file_comparator
   :members:
   :undoc-members:

.. automodule:: src.comparators.chunked_comparator
   :members:
   :undoc-members:

Configuration & Validators
--------------------------

.. automodule:: src.config.loader
   :members:
   :undoc-members:

.. automodule:: src.config.models
   :members:
   :undoc-members:

.. automodule:: src.config.mapping_parser
   :members:
   :undoc-members:

.. automodule:: src.config.universal_mapping_parser
   :members:
   :undoc-members:

.. automodule:: src.config.template_converter
   :members:
   :undoc-members:

.. automodule:: src.config.rules_template_converter
   :members:
   :undoc-members:

.. automodule:: src.validators.mapping_validator
   :members:
   :undoc-members:

.. automodule:: src.validators.field_validator
   :members:
   :undoc-members:

.. automodule:: src.validators.cross_field_validator
   :members:
   :undoc-members:

.. automodule:: src.validators.rule_engine
   :members:
   :undoc-members:

.. automodule:: src.validators.threshold
   :members:
   :undoc-members:

API Routers
-----------

.. automodule:: src.api.auth
   :members:
   :undoc-members:

Web UI
~~~~~~

.. automodule:: src.api.routers.ui
   :members:
   :undoc-members:

Runs
~~~~

.. automodule:: src.api.routers.runs
   :members:
   :undoc-members:

API Tester
~~~~~~~~~~

.. automodule:: src.api.routers.api_tester
   :members:
   :undoc-members:

.. automodule:: src.api.models.api_tester
   :members:
   :undoc-members:

Services
--------

.. automodule:: src.services.db_file_compare_service
   :members:
   :undoc-members:

.. automodule:: src.services.run_history_service
   :members:
   :undoc-members:

.. automodule:: src.services.job_state_store
   :members:
   :undoc-members:

.. automodule:: src.services.scheduler_service
   :members:
   :undoc-members:

.. automodule:: src.services.notification_service
   :members:
   :undoc-members:

Contracts & Adapters
--------------------

.. automodule:: src.contracts.task_contracts
   :members:
   :undoc-members:

.. automodule:: src.contracts.validation
   :members:
   :undoc-members:

.. automodule:: src.adapters.api_task_adapter
   :members:
   :undoc-members:

.. automodule:: src.adapters.cli_task_adapter
   :members:
   :undoc-members:

Pipeline
--------

.. automodule:: src.pipeline.suite_config
   :members:
   :undoc-members:

Reporting & Utilities
---------------------

.. automodule:: src.reporters.html_reporter
   :members:
   :undoc-members:

.. automodule:: src.reporters.validation_reporter
   :members:
   :undoc-members:

.. automodule:: src.utils.logger
   :members:
   :undoc-members:

.. automodule:: src.utils.progress
   :members:
   :undoc-members:

.. automodule:: src.utils.memory_monitor
   :members:
   :undoc-members:

.. automodule:: src.utils.audit_logger
   :members:
   :undoc-members:

.. automodule:: src.utils.archive
   :members:
   :undoc-members:

.. automodule:: src.utils.config_validator
   :members:
   :undoc-members:
