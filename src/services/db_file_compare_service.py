"""Service for DB extract → file comparison workflow.

Orchestrates three steps:
1. Extract data from the configured database using a SQL query or table name.
2. Write the extracted DataFrame to a temp pipe-delimited file.
3. Compare that file against an actual batch file using the standard
   run_compare_service pipeline.

The result dict always contains two top-level keys:
- ``workflow`` — metadata about the extraction step.
- ``compare`` — the raw output from run_compare_service.

Backend-agnostic (S15-2, #405, ADR 0022 §5)
-------------------------------------------
The DB side is built via
:func:`~src.database.adapters.factory.get_database_adapter`, so ``db-compare``
honours the ``DB_ADAPTER`` environment variable (``oracle`` / ``postgresql`` /
``sqlite``) and any per-request ``connection_override`` — it no longer falls
back to the Oracle-only ``OracleConnection.from_env()``.  Oracle remains fully
supported, now as ``DB_ADAPTER=oracle`` routed through the same factory.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

from src.comparators.backends.factory import resolve_backend
from src.database.adapters.factory import get_database_adapter
from src.database.extractor import DataExtractor
from src.services.compare_service import run_compare_service
from src.transforms.transform_orchestrator import TransformEngine

# SQL keywords that unambiguously identify a query string vs. a table name.
_SQL_KEYWORDS = frozenset(["select", "with", "from"])

# Adapter names accepted on the db-compare connection-override path. Mirrors the
# set enforced by the API router prior to S18-2 (oracle / postgresql / sqlite).
ALLOWED_DB_ADAPTERS = frozenset({"oracle", "postgresql", "sqlite"})


def build_connection_override(
    *,
    named_connection: Any | None = None,
    profile_config: Any | None = None,
    db_host: str | None = None,
    db_user: str | None = None,
    db_password: str | None = None,
    db_schema: str | None = None,
    db_adapter: str | None = None,
) -> dict[str, Any] | None:
    """Assemble the ``connection_override`` dict for :func:`compare_db_to_file`.

    Resolves the per-request DB connection from one of three mutually
    prioritised sources and returns the override dict that the db-compare
    service forwards to the adapter factory. This is the pure assembly logic
    previously inlined in the ``POST /api/v1/files/db-compare`` endpoint;
    relocating it here keeps the router thin and makes the resolution
    unit-testable at the service layer (S18-2, #428). The output shape and
    field-selection rules are unchanged from the previous inline implementation.

    Resolution precedence:

    1. *named_connection* (the resolved ``DB_CONNECTIONS`` entry): its ``host``
       / ``user`` / ``password`` / ``schema`` / ``adapter`` attributes seed the
       individual ``db_*`` values, overriding any individually supplied fields.
    2. *profile_config* (a resolved ``config/db_connections.yaml`` profile):
       when present (and no named connection), its ``dsn`` / ``user`` /
       ``password`` / ``schema`` / ``db_adapter`` populate the override; the
       individual ``db_*`` fields are ignored.
    3. The individual ``db_host`` / ``db_user`` / ``db_password`` /
       ``db_schema`` / ``db_adapter`` fields: an override is built from the
       non-``None`` subset only when at least one of ``db_host`` / ``db_user``
       / ``db_password`` / ``db_adapter`` is provided.

    ``db_adapter`` (whether seeded from the named connection or supplied
    individually) is validated against :data:`ALLOWED_DB_ADAPTERS` before any
    override is built. The *profile_config* adapter is trusted (already
    validated upstream) and is not re-checked.

    Args:
        named_connection: Optional resolved named connection with ``host``,
            ``user``, ``password``, ``schema``, ``adapter`` attributes.
        profile_config: Optional resolved profile config with ``dsn``, ``user``,
            ``password``, ``schema``, ``db_adapter`` attributes.
        db_host: Optional DB host/DSN override.
        db_user: Optional DB username override.
        db_password: Optional DB password override.
        db_schema: Optional DB schema override.
        db_adapter: Optional adapter name override.

    Returns:
        The ``connection_override`` dict, or ``None`` when no connection source
        is supplied (the env-configured adapter is then used downstream).

    Raises:
        ValueError: When the resolved ``db_adapter`` is not one of
            :data:`ALLOWED_DB_ADAPTERS`.
    """
    if named_connection is not None:
        db_host = named_connection.host
        db_user = named_connection.user
        db_password = named_connection.password
        db_schema = named_connection.schema
        db_adapter = named_connection.adapter

    if db_adapter is not None and db_adapter not in ALLOWED_DB_ADAPTERS:
        raise ValueError(
            f"Invalid db_adapter '{db_adapter}'. "
            f"Must be one of: {', '.join(sorted(ALLOWED_DB_ADAPTERS))}"
        )

    if profile_config is not None:
        return {
            "db_host": profile_config.dsn,
            "db_user": profile_config.user,
            "db_password": profile_config.password,
            "db_schema": profile_config.schema,
            "db_adapter": profile_config.db_adapter,
        }

    if db_host or db_user or db_password or db_adapter:
        return {
            k: v
            for k, v in {
                "db_host": db_host,
                "db_user": db_user,
                "db_password": db_password,
                "db_schema": db_schema,
                "db_adapter": db_adapter,
            }.items()
            if v is not None
        }

    return None


def _is_sql_query(query_or_table: str) -> bool:
    """Return True when *query_or_table* appears to be a SQL statement.

    Detection is based on the presence of SQL keywords (SELECT, WITH, FROM)
    as the first or any token in the lowercased string. A plain table name
    such as ``SHAW_SRC_P327`` or ``APP_INT.FOO`` contains no such keywords.

    Args:
        query_or_table: Either a bare table name or a SQL SELECT statement.

    Returns:
        True if the string contains SQL keyword tokens, False otherwise.
    """
    tokens = set(query_or_table.lower().split())
    return bool(tokens & _SQL_KEYWORDS)


def _build_adapter(connection_override: dict[str, Any] | None):
    """Build a (not-yet-connected) database adapter for the DB side.

    Routes the DB side of ``db-compare`` through the
    :func:`~src.database.adapters.factory.get_database_adapter` factory so the
    configured ``DB_ADAPTER`` is honoured (ADR 0022 §5).  When
    *connection_override* is supplied (the API named-connection / profile path)
    its ``db_adapter`` selects the backend and its credentials are forwarded to
    the concrete adapter's constructor; otherwise the env-configured adapter is
    returned unchanged.

    Credentials in *connection_override* are passed straight to the adapter
    constructor and are never logged here.

    Args:
        connection_override: Optional dict with a ``db_adapter`` key and
            backend-specific connection values (see :func:`compare_db_to_file`).
            When ``None`` (or empty), the env-configured adapter is used.

    Returns:
        A concrete, **not-yet-connected**
        :class:`~src.database.adapters.base.DatabaseAdapter` instance.

    Raises:
        ValueError: If ``db_adapter`` is an unrecognised adapter name.
    """
    override = connection_override or {}
    adapter_type = override.get("db_adapter")  # None → env DB_ADAPTER

    # No override credentials: env-configured adapter (DB_ADAPTER / ORACLE_*/
    # DB_* / DB_PATH env vars resolve inside the adapter constructors).
    has_conn_values = any(
        override.get(k) is not None
        for k in ("db_host", "db_user", "db_password", "db_port", "db_name", "db_path")
    )
    if not has_conn_values:
        return get_database_adapter(adapter_type)

    # Override carries explicit connection values — construct the concrete
    # adapter directly so the per-request credentials are honoured.
    resolved = adapter_type or "oracle"

    if resolved == "oracle":
        from src.database.adapters.oracle_adapter import OracleAdapter

        return OracleAdapter(
            username=override.get("db_user"),
            password=override.get("db_password"),
            dsn=override.get("db_host"),
        )
    if resolved == "postgresql":
        from src.database.adapters.postgresql_adapter import PostgreSQLAdapter

        return PostgreSQLAdapter(
            host=override.get("db_host"),
            port=override.get("db_port"),
            database=override.get("db_name"),
            username=override.get("db_user"),
            password=override.get("db_password"),
        )
    if resolved == "sqlite":
        from src.database.adapters.sqlite_adapter import SQLiteAdapter

        # SQLite is file-based: accept an explicit db_path, else treat the
        # generic db_host/db_name slot as the path (the API maps a profile's
        # dsn into db_host).
        db_path = override.get("db_path") or override.get("db_host") or override.get("db_name")
        return SQLiteAdapter(db_path=db_path)

    # Unknown adapter name with override values — defer to the factory so the
    # single source of truth raises the standard ValueError.
    return get_database_adapter(resolved)


def _df_to_temp_file(df: pd.DataFrame, delimiter: str = "|") -> str:
    """Write a DataFrame to a named temp pipe-delimited file.

    The file is created in the system temp directory with a ``.txt`` suffix.
    The caller is responsible for deleting the file when finished.

    Args:
        df: DataFrame to serialise.
        delimiter: Column separator. Defaults to ``"|"``.

    Returns:
        Absolute path string of the created temp file.
    """
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        delete=False,
        encoding="utf-8",
    ) as fh:
        df.to_csv(fh, sep=delimiter, index=False)
        return fh.name


def _read_delimited_as_strings(path: str, delimiter: str = "|") -> pd.DataFrame:
    """Read a header-keyed delimited file into an all-string DataFrame.

    Mirrors the native backend's header re-read
    (``pd.read_csv(sep='|', dtype=str, keep_default_na=False, header=0)``) so the
    frame is byte-identical to what the temp-file + native path consumes for the
    actual (file2) side.  Used only on the DuckDB frame-direct path (S25-5).

    Args:
        path: Filesystem path to the delimited file.
        delimiter: Column separator. Defaults to ``"|"``.

    Returns:
        An all-string DataFrame with the header row as column names.
    """
    return pd.read_csv(
        path, sep=delimiter, dtype=str, keep_default_na=False, header=0
    )


def _compare_frames_duckdb(
    df1: pd.DataFrame,
    df2: pd.DataFrame,
    key_columns_list: list[str] | None,
) -> dict[str, Any]:
    """Run the DuckDB frame-direct comparison (no temp file) — S25-5.

    Registers the two already-in-memory frames straight into DuckDB via
    :meth:`~src.comparators.backends.duckdb_backend.DuckDBComparisonBackend.compare_frames`
    and returns the identical ``run_compare_service`` result contract the
    temp-file + native path produces.

    Args:
        df1: Source/expected frame (``file1``) — for db-compare this is the DB
            extract.
        df2: Actual frame (``file2``) — the actual-file side read with
            :func:`_read_delimited_as_strings` (or, for excel-compare, the Excel
            frame).
        key_columns_list: Resolved key column names (``None`` → row-by-row, which
            the DuckDB engine rejects; callers only take this path when keys are
            present or accept the raised ``ValueError``).

    Returns:
        The comparison result dict (native in-memory shape).
    """
    from src.comparators.backends.duckdb_backend import DuckDBComparisonBackend

    return DuckDBComparisonBackend().compare_frames(
        df1, df2, key_columns_list, detailed=True
    )


def _determine_workflow_status(compare_result: dict[str, Any]) -> str:
    """Derive a pass/fail status string from compare service output.

    A result is considered 'passed' when:
    - The files are structurally compatible, AND
    - There are zero rows with differences, zero rows only in file 1, and
      zero rows only in file 2.

    Args:
        compare_result: Raw dict returned by run_compare_service.

    Returns:
        ``"passed"`` or ``"failed"``.
    """
    if not compare_result.get("structure_compatible", True):
        return "failed"

    rows_with_diffs = compare_result.get(
        "rows_with_differences",
        compare_result.get("differences", 0),
    )
    only_in_1 = compare_result.get("only_in_file1", 0)
    only_in_2 = compare_result.get("only_in_file2", 0)

    # only_in_file1/2 may be DataFrames (non-chunked path) — use len() to
    # avoid the ambiguous DataFrame truth-value error.
    def _count(val: Any) -> int:
        try:
            return len(val)
        except TypeError:
            return int(val) if val else 0

    if _count(rows_with_diffs) or _count(only_in_1) or _count(only_in_2):
        return "failed"
    return "passed"


def compare_db_to_file(
    query_or_table: str,
    mapping_config: dict[str, Any],
    actual_file: str,
    output_format: str = "json",
    key_columns: list[str] | str | None = None,
    apply_transforms: bool = False,
    connection_override: dict[str, Any] | None = None,
    output_path: str | None = None,
    backend: str | None = None,
) -> dict[str, Any]:
    """Extract data from the configured database, format it, and compare against a file.

    Workflow:
    1. Validate inputs (actual_file must exist).
    2. Build the database adapter via
       :func:`~src.database.adapters.factory.get_database_adapter` (honouring
       ``DB_ADAPTER`` and any *connection_override*), connect, and extract data
       using either a SQL query or a table name.
    3. Optionally apply field-level transforms to each DB row via
       :class:`~src.transforms.transform_orchestrator.TransformEngine`.
    4. Write the (possibly transformed) rows to a temporary pipe-delimited file.
    5. Delegate to :func:`~src.services.compare_service.run_compare_service`
       for the structural and row-level comparison.
    6. Clean up the temp file.
    7. Return a unified result dict with ``workflow`` and ``compare`` sections.

    Args:
        query_or_table: A SQL SELECT statement or a bare table name.
        mapping_config: Parsed mapping JSON dict (must contain a ``fields``
            list with ``name`` entries).
        actual_file: Path to the actual batch file to compare against.
        output_format: Desired output format for the report (``"json"`` or
            ``"html"``).  When ``output_path`` is supplied, ``"html"`` (or an
            ``output_path`` ending in ``.html``) renders a real HTML comparison
            report via
            :class:`~src.reports.renderers.comparison_renderer.HTMLReporter`,
            reusing the file-compare renderer because the db-compare ``compare``
            section is the same ``run_compare_service`` shape it consumes.
        key_columns: Column name(s) used as join keys during comparison.
            May be a comma-separated string or a list. When None, row-by-row
            comparison is used.
        apply_transforms: When ``True``, each DB row is passed through
            :class:`~src.transforms.transform_orchestrator.TransformEngine`
            before comparison, applying the field-level transforms defined in
            *mapping_config*.  Defaults to ``False`` (no transformation).
        connection_override: Optional dict describing a per-request connection
            (the API named-connection / profile path).  ``db_adapter`` selects
            the backend (``"oracle"`` / ``"postgresql"`` / ``"sqlite"``); the
            remaining keys carry backend-specific connection values — Oracle
            uses ``db_host`` (DSN), ``db_user``, ``db_password``; PostgreSQL
            uses ``db_host``, ``db_port``, ``db_name``, ``db_user``,
            ``db_password``; SQLite uses ``db_path`` (or falls back to
            ``db_host``).  ``db_schema`` is accepted but not forwarded.  When
            ``None``, or when no connection values are supplied, the
            env-configured adapter (``DB_ADAPTER`` and the corresponding env
            vars) is used.  Credentials are passed straight to the adapter and
            are never logged.
        output_path: Optional filesystem path for an HTML report.  When set and
            the resolved format is HTML (``output_format == "html"`` or the path
            ends with ``.html``), an HTML report is rendered to this path and the
            resolved path is recorded under the result's ``report_path`` key.
            When ``None`` (the default), no report is written and the result
            shape is unchanged — preserving backward compatibility.
        backend: Optional explicit comparison-backend name (``native`` /
            ``pandas`` / ``duckdb`` / ``auto``), resolved by
            :func:`~src.comparators.backends.factory.resolve_backend`
            (explicit arg → ``COMPARISON_BACKEND`` env → ``native`` default).
            When the resolved backend is ``duckdb`` (S25-5) **and** key columns
            are supplied, the extracted DB frame and the actual file are
            registered **directly** into DuckDB and diffed — skipping the
            extract → temp-file → re-read hop — producing the **identical** result
            contract.  When ``native`` (the default), the temp-file path is used
            **unchanged**.  ``duckdb`` stays optional/lazy: when the package is
            absent, ``auto`` resolves to ``native`` (no import, no error) and the
            temp-file path runs as before.

    Returns:
        Dict with two top-level keys:

        - ``workflow``: status, db_rows_extracted, query_or_table
        - ``compare``: full output of run_compare_service

        When an HTML report is rendered, an additional ``report_path`` key holds
        the path to the written ``.html`` file.

    Raises:
        FileNotFoundError: When *actual_file* does not exist on disk.
        RuntimeError: When DB extraction fails (propagated from DataExtractor).
        ValueError: When mapping_config contains no ``fields`` list, or
            ``connection_override['db_adapter']`` is an unrecognised adapter.
    """
    # --- Input validation ---------------------------------------------------
    actual_path = Path(actual_file)
    if not actual_path.exists():
        raise FileNotFoundError(f"Actual file not found: {actual_file}")

    if not mapping_config.get("fields"):
        raise ValueError("mapping_config must contain a 'fields' list")

    # --- Normalise key_columns ----------------------------------------------
    if isinstance(key_columns, str):
        key_columns_list: list[str] | None = [k.strip() for k in key_columns.split(",") if k.strip()]
    else:
        key_columns_list = list(key_columns) if key_columns else None

    keys_str = ",".join(key_columns_list) if key_columns_list else None

    # --- DB Extraction -------------------------------------------------------
    # Build the adapter via the factory so DB_ADAPTER (and any per-request
    # connection_override) is honoured — no Oracle-only fallback (ADR 0022 §5).
    # The adapter is used as a context manager so connect/disconnect bracket the
    # extraction on every backend.
    adapter = _build_adapter(connection_override)
    with adapter:
        extractor = DataExtractor(adapter)

        if _is_sql_query(query_or_table):
            df = extractor.extract_by_query(query_or_table)
        else:
            df = extractor.extract_table(query_or_table)

    db_rows_extracted = len(df)

    # --- Optionally apply field transforms to each row ----------------------
    transform_details: list | None = None
    if apply_transforms:
        engine = TransformEngine(mapping_config)
        raw_rows = df.to_dict(orient="records")
        transformed_rows = []
        transform_details = []
        for raw_row in raw_rows:
            transformed = engine.apply(raw_row)
            transformed_rows.append(transformed)
            for field_name in transformed:
                transform_details.append({
                    "field": field_name,
                    "source_value": str(raw_row.get(field_name, "")),
                    "transformed_value": str(transformed.get(field_name, "")),
                    "file_value": "",  # populated post-comparison if available
                })
        df = pd.DataFrame(transformed_rows)

    # --- Backend selection (S25-5) ------------------------------------------
    # Resolve the active backend.  The DB extract is in memory (no file), so the
    # ``auto`` size probe uses the actual-file path on both sides; ``auto`` only
    # upgrades to duckdb when duckdb is importable AND the actual file is large.
    resolved_backend = resolve_backend(str(actual_path), str(actual_path), backend)

    # The DuckDB frame-direct path requires key columns (the engine rejects
    # row-by-row).  Use it only when duckdb is active AND keys are present;
    # otherwise fall through to the unchanged temp-file + native path.
    use_frame_direct = resolved_backend == "duckdb" and bool(key_columns_list)

    if use_frame_direct:
        # Zero temp file: register the DB frame (file1) and the actual file
        # (file2, read with native's header re-read semantics) straight into
        # DuckDB.  Identical result contract to the temp-file + native path.
        actual_df = _read_delimited_as_strings(str(actual_path))
        compare_result = _compare_frames_duckdb(df, actual_df, key_columns_list)
    else:
        # --- Native path (unchanged): write DB data to temp file -------------
        temp_path: str | None = None
        try:
            temp_path = _df_to_temp_file(df)

            # --- Comparison --------------------------------------------------
            compare_result = run_compare_service(
                file1=temp_path,
                file2=str(actual_path),
                keys=keys_str,
                mapping=None,  # mapping_config is already parsed; not a file path
                detailed=True,
                backend=resolved_backend,
            )
        finally:
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except OSError:
                    pass

    # --- Build unified result ------------------------------------------------
    workflow_status = _determine_workflow_status(compare_result)

    result: dict[str, Any] = {
        "workflow": {
            "status": workflow_status,
            "db_rows_extracted": db_rows_extracted,
            "query_or_table": query_or_table,
        },
        "compare": compare_result,
    }
    if transform_details is not None:
        result["transform_details"] = transform_details

    # --- Optional HTML report (S23-2, #444) ---------------------------------
    # The db-compare verdict's ``compare`` section is the exact
    # ``run_compare_service`` output shape that the file-compare HTMLReporter
    # already consumes, so we reuse that renderer verbatim (no new HTML engine,
    # no shape adapter).  ``.html`` extension or ``output_format == "html"``
    # both select HTML, honouring the consistent output contract.
    if output_path:
        wants_html = output_format == "html" or output_path.lower().endswith(".html")
        if wants_html:
            # Local import keeps the reporting layer optional for callers that
            # never request HTML and avoids any import cycle at module load.
            from src.reports.renderers.comparison_renderer import HTMLReporter

            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            HTMLReporter().generate(compare_result, str(out))
            result["report_path"] = str(out)

    return result
