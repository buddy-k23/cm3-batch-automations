"""Mapping-vs-table reconciliation service seam (#407).

Sprint 12 (ADR 0022) made :class:`src.database.reconciliation.SchemaReconciler`
adapter-agnostic — it holds a
:class:`~src.database.adapters.base.DatabaseAdapter` (built via
:func:`~src.database.adapters.factory.get_database_adapter`) and reconciles a
mapping document against a real DB table on Oracle, PostgreSQL, or SQLite.

Until now the only way to reach it was the ``valdo reconcile`` CLI in
``src/main.py``. This module is the single, thin service seam that the CLI,
the REST endpoint (``POST /api/v2/reconcile``), and the MCP tool
(``reconcile_mapping``) all call — Architecture Principle #1 (no business
logic in routers / main / tools).

The service:

* Loads + parses the mapping (by repo-relative path OR bare mapping id under
  ``config/mappings/``, via :class:`~src.config.loader.ConfigLoader`).
* Optionally overrides the mapping's declared target table / schema so a
  caller can reconcile the same mapping against an ad-hoc table without
  editing the mapping JSON.
* Builds the adapter via :func:`get_database_adapter` (honours ``DB_ADAPTER``;
  an explicit ``db_adapter`` argument wins), runs the reconciler, and projects
  the engine verdict onto a stable, JSON-serialisable dict.

Verdict shape (the contract every surface returns)::

    {
        "status": "clean" | "advisories" | "mismatch" | "error",
        "valid": bool,                 # engine validity (no hard errors)
        "mapping_name": str,
        "table": str | None,           # the table actually reconciled
        "schema": str | None,
        "db_adapter": str,             # resolved adapter name
        "summary": {
            "mapped_columns": int,
            "database_columns": int,
            "error_count": int,
            "warning_count": int,
            "advisory_count": int,
            "mismatch_count": int,
        },
        "errors": [str, ...],          # hard failures (missing table/column)
        "mismatches": [str, ...],      # genuine type conflicts
        "advisories": [str, ...],      # non-blocking informational notes
        "warnings": [str, ...],        # the full warning list (superset)
        "unmapped_required": [str, ...],
    }
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.config.loader import ConfigLoader
from src.config.mapping_parser import MappingParser
from src.database.adapters.factory import get_database_adapter
from src.database.reconciliation import SchemaReconciler

logger = logging.getLogger(__name__)

__all__ = ["reconcile_mapping_service", "ReconcileServiceError"]

# Warning substrings the engine uses to distinguish a genuine type *conflict*
# from a merely informational *advisory*. The engine appends both kinds to a
# single ``warnings`` list; we split them here so each surface can present
# "mismatch" (actionable) separately from "advisory" (FYI), per the #407
# field-level-verdict requirement. Kept as lowercase substrings so the match
# is resilient to the exact phrasing in ``reconciliation.py``.
_MISMATCH_MARKER = "type mismatch"
_ADVISORY_MARKERS = (
    "no native boolean",
    "type could not be determined",
)


class ReconcileServiceError(ValueError):
    """Raised for caller-fixable reconciliation errors (bad input).

    Distinct from infrastructure failures (a missing DB driver, an
    unreachable server) which surface as the underlying exception type so
    the caller's own error handling can classify them.
    """


def _classify_warning(warning: str) -> str:
    """Bucket an engine warning string into mismatch / advisory / other.

    Args:
        warning: A single warning string emitted by
            :class:`~src.database.reconciliation.SchemaReconciler`.

    Returns:
        One of ``"mismatch"``, ``"advisory"``, or ``"other"``.
    """
    lowered = warning.lower()
    if _MISMATCH_MARKER in lowered:
        return "mismatch"
    if any(marker in lowered for marker in _ADVISORY_MARKERS):
        return "advisory"
    return "other"


def _derive_status(error_count: int, mismatch_count: int, advisory_count: int) -> str:
    """Derive the headline verdict status from the bucketed counts.

    Args:
        error_count: Number of hard errors (missing table / required column).
        mismatch_count: Number of genuine type conflicts.
        advisory_count: Number of non-blocking informational advisories.

    Returns:
        ``"error"`` when any hard error is present, else ``"mismatch"`` when a
        genuine type conflict is present, else ``"advisories"`` when only
        informational notes are present, else ``"clean"``.
    """
    if error_count > 0:
        return "error"
    if mismatch_count > 0:
        return "mismatch"
    if advisory_count > 0:
        return "advisories"
    return "clean"


def reconcile_mapping_service(
    mapping: str,
    *,
    table: Optional[str] = None,
    schema: Optional[str] = None,
    db_adapter: Optional[str] = None,
    mappings_dir: str = "config/mappings",
) -> Dict[str, Any]:
    """Reconcile a mapping document against a database table.

    This is the single seam wrapping
    :class:`~src.database.reconciliation.SchemaReconciler` for every surface
    (CLI / REST / MCP). It loads + parses the mapping, optionally overrides
    the target table / schema, builds the adapter (honouring ``DB_ADAPTER``),
    runs the reconciler, and returns a structured, JSON-serialisable verdict.

    Args:
        mapping: Either a repo-relative path to a mapping JSON file, or a
            bare mapping id (filename stem) resolved under *mappings_dir*.
            Resolution is delegated to
            :meth:`~src.config.loader.ConfigLoader.load_mapping`.
        table: Optional target table name. When supplied it overrides the
            mapping's declared ``target.table_name`` so a caller can reconcile
            the same mapping against an ad-hoc table. May be schema-qualified
            (``SCHEMA.TABLE``); when *schema* is also given it is prepended.
        schema: Optional schema / owner. Prepended to *table* (``schema.table``)
            when *table* is not already qualified. Ignored when *table* is None
            and the mapping already carries a qualified table name.
        db_adapter: Optional explicit adapter name (``oracle`` / ``postgresql``
            / ``sqlite``). When None the ``DB_ADAPTER`` env var is honoured via
            :func:`~src.database.adapters.factory.get_database_adapter`.
        mappings_dir: Directory used to resolve a bare mapping id. Defaults to
            ``config/mappings``.

    Returns:
        The structured verdict dict documented in the module docstring.

    Raises:
        ReconcileServiceError: When the mapping cannot be loaded / parsed, or
            when no target table can be determined (neither *table* nor the
            mapping declares one).
        ValueError: When *db_adapter* is an unrecognised adapter name (from
            the factory).
    """
    if not mapping or not isinstance(mapping, str):
        raise ReconcileServiceError("mapping is required and must be a non-empty string")

    loader = ConfigLoader()
    try:
        mapping_dict = _load_mapping(loader, mapping, mappings_dir)
    except FileNotFoundError as exc:
        raise ReconcileServiceError(str(exc)) from exc

    try:
        mapping_doc = MappingParser().parse(mapping_dict)
    except (ValueError, KeyError) as exc:
        raise ReconcileServiceError(f"Failed to parse mapping {mapping!r}: {exc}") from exc

    # Apply table / schema overrides onto the parsed target so the same
    # mapping can be reconciled against an ad-hoc table without editing JSON.
    target = dict(mapping_doc.target or {})
    if table or schema:
        target["type"] = "database"
        resolved_table = _qualify_table(table or target.get("table_name"), schema)
        if resolved_table:
            target["table_name"] = resolved_table
        mapping_doc.target = target

    resolved_table_name = mapping_doc.target.get("table_name") if mapping_doc.target else None
    if mapping_doc.target.get("type") == "database" and not resolved_table_name:
        raise ReconcileServiceError(
            "No target table: supply 'table' or set target.table_name in the mapping"
        )

    adapter = get_database_adapter(db_adapter)
    resolved_adapter_name = type(adapter).__name__

    reconciler = SchemaReconciler(adapter)
    result = reconciler.reconcile_mapping(mapping_doc)

    return _project_verdict(
        result,
        mapping_name=mapping_doc.mapping_name,
        table=resolved_table_name,
        schema=schema,
        db_adapter=resolved_adapter_name,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _looks_like_path(value: str) -> bool:
    """Return True when *value* looks like a filesystem path rather than an id."""
    return "/" in value or "\\" in value or value.endswith(".json")


def _load_mapping(loader: ConfigLoader, mapping: str, mappings_dir: str) -> Dict[str, Any]:
    """Load a mapping dict by path or bare id.

    ``ConfigLoader.load_mapping`` already resolves a bare filename under
    ``<config_dir>/mappings/``. We additionally accept a bare id *without* the
    ``.json`` suffix and a non-default *mappings_dir* by normalising before the
    delegation.

    Args:
        loader: The :class:`ConfigLoader` instance.
        mapping: Path or bare mapping id.
        mappings_dir: Directory to resolve a bare id against.

    Returns:
        The parsed mapping dictionary.

    Raises:
        FileNotFoundError: When the mapping cannot be found.
    """
    import os

    if _looks_like_path(mapping):
        return loader.load_mapping(mapping)

    # Bare id: try ``<mappings_dir>/<id>.json`` first, then defer to the
    # loader's own default-directory resolution as a fallback.
    candidate = os.path.join(mappings_dir, mapping if mapping.endswith(".json") else f"{mapping}.json")
    if os.path.exists(candidate):
        return loader.load_mapping(candidate)
    return loader.load_mapping(mapping if mapping.endswith(".json") else f"{mapping}.json")


def _qualify_table(table: Optional[str], schema: Optional[str]) -> Optional[str]:
    """Return a schema-qualified table name when a schema is supplied.

    Args:
        table: Bare or already-qualified table name (may be None).
        schema: Schema / owner to prepend (may be None).

    Returns:
        ``"schema.table"`` when *schema* is given and *table* is not already
        qualified; otherwise *table* unchanged.
    """
    if not table:
        return table
    if schema and "." not in table:
        return f"{schema}.{table}"
    return table


def _project_verdict(
    result: Dict[str, Any],
    *,
    mapping_name: str,
    table: Optional[str],
    schema: Optional[str],
    db_adapter: str,
) -> Dict[str, Any]:
    """Project the raw engine result onto the stable #407 verdict shape.

    Splits the engine's flat ``warnings`` list into mismatches (genuine type
    conflicts) and advisories (informational notes) so each surface can render
    a field-level verdict.

    Args:
        result: Raw dict from
            :meth:`~src.database.reconciliation.SchemaReconciler.reconcile_mapping`.
        mapping_name: Parsed mapping name for display.
        table: The table that was actually reconciled.
        schema: The schema / owner, if one was supplied.
        db_adapter: Resolved adapter class name.

    Returns:
        The structured verdict dict (see module docstring).
    """
    errors: List[str] = list(result.get("errors", []) or [])
    warnings: List[str] = list(result.get("warnings", []) or [])

    mismatches: List[str] = []
    advisories: List[str] = []
    for warning in warnings:
        bucket = _classify_warning(warning)
        if bucket == "mismatch":
            mismatches.append(warning)
        elif bucket == "advisory":
            advisories.append(warning)

    status = _derive_status(len(errors), len(mismatches), len(advisories))

    return {
        "status": status,
        "valid": bool(result.get("valid", False)),
        "mapping_name": mapping_name,
        "table": table,
        "schema": schema,
        "db_adapter": db_adapter,
        "summary": {
            "mapped_columns": int(result.get("mapped_columns", 0) or 0),
            "database_columns": int(result.get("database_columns", 0) or 0),
            "error_count": len(errors),
            "warning_count": len(warnings),
            "advisory_count": len(advisories),
            "mismatch_count": len(mismatches),
        },
        "errors": errors,
        "mismatches": mismatches,
        "advisories": advisories,
        "warnings": warnings,
        "unmapped_required": list(result.get("unmapped_required", []) or []),
    }
