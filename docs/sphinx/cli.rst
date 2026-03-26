CLI Reference
=============

The CLI entrypoint is ``valdo`` (implemented in ``src.main``).

Commands
--------

- ``detect``: Auto-detect file format
- ``parse``: Parse files (supports ``--use-chunked``)
- ``validate``: Validate files and schema (supports ``--use-chunked``)
- ``compare``: Compare files (supports ``--use-chunked``)
- ``reconcile``: Reconcile one mapping against Oracle schema
- ``reconcile-all``: Reconcile all mappings in a directory, with optional baseline drift detection
- ``extract``: Extract table/query data from Oracle to file
- ``convert-rules``: Convert Excel/CSV rules templates to JSON
- ``info``: System and dependency diagnostics
- ``watch``: Poll a directory for batch trigger files and run matching suites automatically

For command-specific details run:

.. code-block:: bash

   valdo --help
   valdo <command> --help
