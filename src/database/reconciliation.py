"""Database schema reconciliation with mapping documents.

Adapter-agnostic per ADR 0022 (S12-1b, #404).  The reconciliation engine no
longer issues raw Oracle catalog SQL (the ALL_/USER_ data-dictionary views).
Instead it holds a
:class:`~src.database.adapters.base.DatabaseAdapter` and reads schema facts via
:meth:`~src.database.adapters.base.DatabaseAdapter.table_exists`,
:meth:`~src.database.adapters.base.DatabaseAdapter.get_table_columns`, and
:meth:`~src.database.adapters.base.DatabaseAdapter.get_column_metadata`.  Type
compatibility is decided by a **dialect-free** mapping-declared-type ->
:class:`~src.database.adapters.base.CanonicalType` matrix, so the same mapping
reconciles correctly against Oracle, PostgreSQL, and SQLite.
"""

from typing import Dict, List, Any, Optional, Tuple, Set
from decimal import Decimal, InvalidOperation

from ..config.mapping_parser import MappingDocument
from .adapters.base import CanonicalType, ColumnMeta, DatabaseAdapter
from ..utils.logger import get_logger


# ---------------------------------------------------------------------------
# Dialect-free type model (ADR 0022 §3)
# ---------------------------------------------------------------------------

#: Mapping-declared ``data_type`` -> the set of :class:`CanonicalType` members
#: it is compatible with.  This is the single, dialect-free matrix that
#: replaced the Oracle-only ``_types_compatible`` string list.  ``boolean``
#: accepts ``BOOLEAN`` (native, e.g. PostgreSQL) plus ``INTEGER`` and ``STRING``
#: (numeric/char flags on backends without a native boolean — Oracle/SQLite),
#: emitted as a dialect-neutral *advisory*, never an error.
MAPPING_TYPE_COMPATIBILITY: Dict[str, Set[CanonicalType]] = {
    "string": {CanonicalType.STRING},
    "integer": {CanonicalType.INTEGER},
    "number": {CanonicalType.INTEGER, CanonicalType.DECIMAL, CanonicalType.FLOAT},
    "decimal": {CanonicalType.DECIMAL, CanonicalType.FLOAT},
    "date": {CanonicalType.DATE, CanonicalType.TIMESTAMP},
    "boolean": {CanonicalType.BOOLEAN, CanonicalType.INTEGER, CanonicalType.STRING},
}

#: Dialect-agnostic raw-type-name -> CanonicalType fallback, used only when a
#: caller supplies a raw type string (e.g. a monkeypatched ``_get_column_details``
#: returning a legacy ``data_type``) instead of a normalised ``canonical_type``.
#: Covers Oracle, PostgreSQL, and SQLite type names so the engine never needs
#: per-dialect knowledge of its own.
_RAW_NAME_TO_CANONICAL: Dict[str, CanonicalType] = {
    # STRING
    "VARCHAR2": CanonicalType.STRING, "NVARCHAR2": CanonicalType.STRING,
    "VARCHAR": CanonicalType.STRING, "CHARACTER VARYING": CanonicalType.STRING,
    "CHAR": CanonicalType.STRING, "NCHAR": CanonicalType.STRING,
    "BPCHAR": CanonicalType.STRING, "TEXT": CanonicalType.STRING,
    "CLOB": CanonicalType.STRING, "NCLOB": CanonicalType.STRING,
    # INTEGER
    "INTEGER": CanonicalType.INTEGER, "INT": CanonicalType.INTEGER,
    "BIGINT": CanonicalType.INTEGER, "SMALLINT": CanonicalType.INTEGER,
    "INT2": CanonicalType.INTEGER, "INT4": CanonicalType.INTEGER,
    "INT8": CanonicalType.INTEGER,
    # DECIMAL
    "NUMERIC": CanonicalType.DECIMAL, "DECIMAL": CanonicalType.DECIMAL,
    # FLOAT
    "FLOAT": CanonicalType.FLOAT, "REAL": CanonicalType.FLOAT,
    "DOUBLE": CanonicalType.FLOAT, "DOUBLE PRECISION": CanonicalType.FLOAT,
    "FLOAT8": CanonicalType.FLOAT, "BINARY_FLOAT": CanonicalType.FLOAT,
    "BINARY_DOUBLE": CanonicalType.FLOAT,
    # BOOLEAN
    "BOOLEAN": CanonicalType.BOOLEAN, "BOOL": CanonicalType.BOOLEAN,
    # DATE / TIMESTAMP
    "DATE": CanonicalType.DATE,
    "TIMESTAMP": CanonicalType.TIMESTAMP, "TIMESTAMPTZ": CanonicalType.TIMESTAMP,
    "DATETIME": CanonicalType.TIMESTAMP,
    # BINARY
    "BLOB": CanonicalType.BINARY, "BYTEA": CanonicalType.BINARY,
    "RAW": CanonicalType.BINARY, "LONG RAW": CanonicalType.BINARY,
}


def _raw_name_to_canonical(raw_type: Optional[str], scale: Optional[int] = None) -> CanonicalType:
    """Map a raw backend type *name* to a :class:`CanonicalType` (fallback only).

    Used when the engine is handed a raw type string rather than an
    adapter-normalised ``canonical_type``.  ``NUMBER`` is resolved by scale
    (``NUMBER(p,0)`` -> INTEGER, otherwise DECIMAL) to match the Oracle
    adapter's own normalisation.

    Args:
        raw_type: A backend-native type name (any dialect), or ``None``.
        scale: Numeric scale, used to disambiguate Oracle ``NUMBER``.

    Returns:
        The matching :class:`CanonicalType`; :attr:`CanonicalType.UNKNOWN` when
        the name is empty or unrecognised.
    """
    t = (raw_type or "").strip().upper()
    if t == "":
        return CanonicalType.UNKNOWN
    if t == "NUMBER":
        if scale is not None and int(scale) == 0:
            return CanonicalType.INTEGER
        return CanonicalType.DECIMAL
    if t.startswith("TIMESTAMP"):
        return CanonicalType.TIMESTAMP
    return _RAW_NAME_TO_CANONICAL.get(t, CanonicalType.UNKNOWN)


def canonical_compatible(mapping_type: str, canonical: CanonicalType) -> bool:
    """Decide whether a mapping ``data_type`` is compatible with a canonical type.

    Dialect-free per ADR 0022 §3.  :attr:`CanonicalType.UNKNOWN` is treated as
    compatible-with-everything (the honest answer for a typeless backend; an
    informational note is emitted by the caller).  An unrecognised mapping type
    is non-blocking (returns ``True``) so unfamiliar vocab never hard-fails a
    reconciliation.

    Args:
        mapping_type: The mapping's declared ``data_type`` (case-insensitive).
        canonical: The DB column's :class:`CanonicalType`.

    Returns:
        ``True`` if compatible, ``False`` for a genuine type conflict.
    """
    if canonical is CanonicalType.UNKNOWN:
        return True
    allowed = MAPPING_TYPE_COMPATIBILITY.get(mapping_type.lower())
    if allowed is None:
        return True
    return canonical in allowed


def is_advisory(mapping_type: str, canonical: CanonicalType) -> bool:
    """Whether a *compatible* pairing is merely advisory rather than exact.

    Two dialect-neutral advisory cases per ADR 0022 §3:

    - ``boolean`` stored as a non-native carrier (``INTEGER``/``STRING``) — the
      "no native boolean on this backend" note.
    - any mapping type over an :attr:`CanonicalType.UNKNOWN` column — the
      "type could not be determined" note for a typeless backend.

    Args:
        mapping_type: The mapping's declared ``data_type``.
        canonical: The DB column's :class:`CanonicalType`.

    Returns:
        ``True`` when an informational warning should accompany a clean verdict.
    """
    if canonical is CanonicalType.UNKNOWN:
        return True
    if mapping_type.lower() == "boolean" and canonical is not CanonicalType.BOOLEAN:
        return True
    return False


class SchemaReconciler:
    """Reconciles mapping documents with actual database schema.

    Holds a :class:`~src.database.adapters.base.DatabaseAdapter` (ADR 0022).
    All catalog reads route through the adapter, so the same reconciler works
    against Oracle, PostgreSQL, and SQLite without dialect-specific SQL.
    """

    def __init__(self, adapter: DatabaseAdapter):
        """Initialize schema reconciler.

        Args:
            adapter: A :class:`~src.database.adapters.base.DatabaseAdapter`
                instance.  The reconciler connects/disconnects it around each
                :meth:`reconcile_mapping` call when the adapter exposes the
                ``connect``/``disconnect`` lifecycle and is not already open.
        """
        self.adapter = adapter
        self.logger = get_logger(__name__)

    # ------------------------------------------------------------------
    # Connection lifecycle helpers
    # ------------------------------------------------------------------

    def _ensure_connected(self) -> bool:
        """Open the adapter connection if needed.

        Returns:
            ``True`` if this call opened the connection (and is therefore
            responsible for closing it), ``False`` otherwise.
        """
        connect = getattr(self.adapter, "connect", None)
        if not callable(connect):
            return False
        # Already-open adapters expose a populated ``_connection``.
        if getattr(self.adapter, "_connection", None) is not None:
            return False
        try:
            connect()
            return True
        except Exception as exc:  # pragma: no cover - exercised via callers
            self.logger.error(f"Failed to connect adapter for reconcile: {exc}")
            return False

    def _maybe_disconnect(self, owns_connection: bool) -> None:
        """Disconnect the adapter only if this reconciler opened it."""
        if not owns_connection:
            return
        disconnect = getattr(self.adapter, "disconnect", None)
        if callable(disconnect):
            try:
                disconnect()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass

    def reconcile_mapping(self, mapping: MappingDocument) -> Dict[str, Any]:
        """Reconcile mapping document with database schema.

        Args:
            mapping: MappingDocument to reconcile.

        Returns:
            Reconciliation results with errors and warnings.
        """
        owns_connection = self._ensure_connected()
        try:
            return self._reconcile_mapping(mapping)
        finally:
            self._maybe_disconnect(owns_connection)

    def _reconcile_mapping(self, mapping: MappingDocument) -> Dict[str, Any]:
        """Core reconciliation logic (assumes the adapter is connected)."""
        errors: List[str] = []
        warnings: List[str] = []

        # Get target table name
        if mapping.target['type'] != 'database':
            return {
                'valid': True,
                'errors': [],
                'warnings': ['Target is not a database, skipping reconciliation'],
                'error_count': 0,
                'warning_count': 1,
            }

        table_name = mapping.target.get('table_name')
        if not table_name:
            errors.append("No target table name specified")
            return {
                'valid': False,
                'errors': errors,
                'warnings': warnings,
                'error_count': len(errors),
                'warning_count': len(warnings),
            }

        owner, normalized_table_name = self._parse_table_reference(table_name)

        # Check if table exists
        if not self._table_exists(normalized_table_name, owner):
            errors.append(f"Target table does not exist: {table_name}")
            return {
                'valid': False,
                'errors': errors,
                'warnings': warnings,
                'error_count': len(errors),
                'warning_count': len(warnings),
            }

        # Get table columns
        db_columns = self._get_table_columns(normalized_table_name, owner)
        db_column_info = self._get_column_details(normalized_table_name, owner)

        # Check each mapping
        for col_mapping in mapping.mappings:
            target_col = col_mapping.target_column

            # Check if column exists
            if target_col not in db_columns:
                if col_mapping.required:
                    errors.append(f"Required target column not found: {target_col}")
                else:
                    warnings.append(f"Optional target column not found: {target_col}")
                continue

            # Get column info
            col_info = db_column_info.get(target_col, {})

            # Check data type compatibility via the dialect-free canonical matrix
            mapping_type = col_mapping.data_type
            db_type = col_info.get('data_type', '')
            canonical = self._resolve_canonical(col_info)

            if not canonical_compatible(mapping_type, canonical):
                warnings.append(
                    f"Type mismatch for {target_col}: "
                    f"mapping expects '{mapping_type}', database has '{db_type}'"
                )
            elif is_advisory(mapping_type, canonical):
                warnings.append(self._advisory_message(target_col, mapping_type, canonical, db_type))

            # Check nullable constraint
            if col_mapping.required and self._is_nullable(col_info):
                warnings.append(
                    f"Column {target_col} is required in mapping but nullable in database"
                )

            # Check length constraints
            if mapping_type.lower() == 'string':
                db_length = col_info.get('data_length')
                if db_length:
                    # Check validation rules for max_length
                    for rule in col_mapping.validation_rules:
                        if rule['type'] == 'max_length':
                            max_len = rule.get('parameters', {}).get('length', 0)
                            if max_len > db_length:
                                warnings.append(
                                    f"Column {target_col}: validation max_length ({max_len}) "
                                    f"exceeds database length ({db_length})"
                                )

            # Check numeric precision/scale constraints
            if mapping_type.lower() in ('number', 'decimal', 'integer'):
                numeric_warning = self._check_numeric_precision_scale(target_col, col_mapping, col_info)
                if numeric_warning:
                    warnings.append(numeric_warning)

            # Check date format compatibility (where specified in mapping)
            date_warning = self._check_date_format_compatibility(target_col, col_mapping, db_type, canonical)
            if date_warning:
                warnings.append(date_warning)

        # Check for unmapped required database columns
        mapped_columns = {m.target_column for m in mapping.mappings}
        required_db_columns = self._get_required_columns(normalized_table_name, owner)

        unmapped_required = required_db_columns - mapped_columns
        if unmapped_required:
            warnings.append(
                f"Required database columns not in mapping: {sorted(unmapped_required)}"
            )

        # Check whether mapping keys align with DB PK/unique constraints
        key_target_columns = self._resolve_mapping_key_targets(mapping)
        if key_target_columns:
            constrained_sets = self._get_pk_unique_constraint_columns(normalized_table_name, owner)
            if constrained_sets and set(key_target_columns) not in constrained_sets:
                warnings.append(
                    f"Mapping key columns {sorted(key_target_columns)} do not exactly match any "
                    f"database PK/UNIQUE constraint"
                )

        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
            'error_count': len(errors),
            'warning_count': len(warnings),
            'table_name': table_name,
            'mapped_columns': len(mapping.mappings),
            'database_columns': len(db_columns),
            'unmapped_required': list(unmapped_required),
        }

    def _parse_table_reference(self, table_reference: str) -> Tuple[Optional[str], str]:
        """Parse table reference into (owner, table_name).

        Note: the adapter handles per-dialect identifier casing.  The table
        name is passed through verbatim so non-Oracle (case-sensitive)
        backends are not forced to uppercase; the owner is uppercased to match
        the legacy Oracle owner-qualified behaviour preserved by the tests.
        """
        if '.' in table_reference:
            owner, table_name = table_reference.split('.', 1)
            return owner.upper(), table_name
        return None, table_reference

    def _table_exists(self, table_name: str, owner: Optional[str] = None) -> bool:
        """Check if table exists, via the adapter (dialect-neutral)."""
        try:
            return self.adapter.table_exists(table_name, owner)
        except Exception as e:
            self.logger.error(f"Error checking table existence: {e}")
            return False

    def _get_table_columns(self, table_name: str, owner: Optional[str] = None) -> List[str]:
        """Get list of column names for table, via the adapter."""
        try:
            return list(self.adapter.get_table_columns(table_name, owner))
        except Exception as e:
            self.logger.error(f"Error getting table columns: {e}")
            return []

    def _get_column_details(self, table_name: str, owner: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """Get detailed column information, via the adapter.

        Returns a dict keyed by column name.  Each value carries the portable
        ``canonical_type`` (a :class:`CanonicalType`) plus legacy-shaped keys
        (``data_type``/``data_length``/``data_precision``/``data_scale``/
        ``nullable`` as ``'Y'``/``'N'``) for backward compatibility with
        existing reconcile tests and report consumers.
        """
        try:
            metadata: Dict[str, ColumnMeta] = self.adapter.get_column_metadata(table_name, owner)
        except Exception as e:
            self.logger.error(f"Error getting column details: {e}")
            return {}

        result: Dict[str, Dict[str, Any]] = {}
        for name, meta in metadata.items():
            result[name] = {
                'canonical_type': meta.canonical_type,
                'data_type': meta.raw_type,
                'data_length': meta.length,
                'data_precision': meta.precision,
                'data_scale': meta.scale,
                'nullable': 'Y' if meta.nullable else 'N',
            }
        return result

    def _get_required_columns(self, table_name: str, owner: Optional[str] = None) -> Set[str]:
        """Get set of required (NOT NULL) columns, via the adapter metadata."""
        try:
            metadata: Dict[str, ColumnMeta] = self.adapter.get_column_metadata(table_name, owner)
        except Exception as e:
            self.logger.error(f"Error getting required columns: {e}")
            return set()
        return {name for name, meta in metadata.items() if not meta.nullable}

    # ------------------------------------------------------------------
    # Type-resolution helpers
    # ------------------------------------------------------------------

    def _resolve_canonical(self, col_info: Dict[str, Any]) -> CanonicalType:
        """Resolve a column's :class:`CanonicalType` from its info dict.

        Prefers an adapter-supplied ``canonical_type``; falls back to mapping
        the raw ``data_type`` name (so monkeypatched legacy-shaped col_info
        dicts that omit ``canonical_type`` still reconcile correctly).
        """
        canonical = col_info.get('canonical_type')
        if isinstance(canonical, CanonicalType):
            return canonical
        return _raw_name_to_canonical(
            col_info.get('data_type'), col_info.get('data_scale')
        )

    def _is_nullable(self, col_info: Dict[str, Any]) -> bool:
        """Whether a column is nullable, from its (legacy ``'Y'``/``'N'``) info."""
        return str(col_info.get('nullable', 'Y')).strip().upper() != 'N'

    def _advisory_message(
        self, target_col: str, mapping_type: str, canonical: CanonicalType, db_type: str
    ) -> str:
        """Build the informational advisory warning for a compatible-but-noted pairing."""
        if canonical is CanonicalType.UNKNOWN:
            return (
                f"Column {target_col}: database type could not be determined "
                f"(typeless backend) — mapping '{mapping_type}' accepted without "
                f"a type assertion"
            )
        # boolean over a non-native carrier
        return (
            f"Column {target_col}: declared '{mapping_type}' stored as "
            f"'{db_type or canonical.value}' — no native boolean on this backend "
            f"(advisory, not an error)"
        )

    def _resolve_mapping_key_targets(self, mapping: MappingDocument) -> List[str]:
        """Resolve mapping key columns from source names to target column names."""
        source_to_target = {m.source_column: m.target_column for m in mapping.mappings}
        key_targets = []
        for key_col in mapping.key_columns or []:
            key_targets.append(source_to_target.get(key_col, key_col).upper())
        return key_targets

    def _get_pk_unique_constraint_columns(self, table_name: str, owner: Optional[str] = None) -> List[Set[str]]:
        """Get PK/UNIQUE constraint column sets for table.

        Cross-dialect constraint reconciliation is deferred to a follow-up
        (ADR 0022 §6 — ``get_constraints`` is not yet on the adapter ABC).  The
        adapter seam exposes no constraint metadata, so this returns an empty
        list (the constraint sub-check degrades to "skipped" — never a false
        failure).  Existing tests monkeypatch this method directly to exercise
        the key-alignment warning.
        """
        return []

    def _extract_rule_param(self, validation_rules: List[Dict[str, Any]], rule_type: str, param_name: str) -> Optional[Any]:
        """Extract parameter value from first matching validation rule."""
        for rule in validation_rules or []:
            if rule.get('type') == rule_type:
                return rule.get('parameters', {}).get(param_name)
        return None

    def _check_numeric_precision_scale(self, target_col: str, col_mapping, col_info: Dict[str, Any]) -> Optional[str]:
        """Validate mapping numeric constraints against DB precision/scale.

        Gated on a numeric :class:`CanonicalType` so the check is dialect-free;
        SQLite returns ``None`` precision/scale, so the range sub-check is
        skipped there (no data -> no false warning, per ADR 0022 §3).
        """
        canonical = self._resolve_canonical(col_info)
        if canonical not in {CanonicalType.INTEGER, CanonicalType.DECIMAL, CanonicalType.FLOAT}:
            return None

        precision = col_info.get('data_precision')
        scale = col_info.get('data_scale') or 0
        mapping_type = col_mapping.data_type.lower()

        if mapping_type == 'integer' and scale and scale > 0:
            return f"Column {target_col}: mapping type is integer but DB scale is {scale}"

        min_val = self._extract_rule_param(col_mapping.validation_rules, 'range', 'min')
        max_val = self._extract_rule_param(col_mapping.validation_rules, 'range', 'max')

        if precision is None or (min_val is None and max_val is None):
            return None

        try:
            candidates = [v for v in (min_val, max_val) if v is not None]
            digits_before = 0
            digits_after = 0
            for value in candidates:
                dec = Decimal(str(value)).copy_abs()
                dec_str = format(dec, 'f')
                if '.' in dec_str:
                    before, after = dec_str.split('.', 1)
                else:
                    before, after = dec_str, ''
                digits_before = max(digits_before, len(before.lstrip('0')) or 1)
                digits_after = max(digits_after, len(after.rstrip('0')))

            max_before_allowed = int(precision) - int(scale)
            if digits_before > max_before_allowed:
                return (
                    f"Column {target_col}: mapping range needs {digits_before} digits before decimal, "
                    f"but DB allows {max_before_allowed} (NUMBER({precision},{scale}))"
                )

            if digits_after > int(scale):
                return (
                    f"Column {target_col}: mapping range implies {digits_after} decimal places, "
                    f"but DB scale is {scale}"
                )
        except (InvalidOperation, ValueError, TypeError):
            return None

        return None

    def _check_date_format_compatibility(
        self, target_col: str, col_mapping, db_type: str, canonical: Optional[CanonicalType] = None
    ) -> Optional[str]:
        """Warn when mapping has date format hints against non-date DB types.

        Uses the dialect-free :class:`CanonicalType` (DATE/TIMESTAMP) so the
        check is backend-neutral.  When *canonical* is not supplied it is
        derived from the raw ``db_type`` (preserves legacy callers/tests).
        """
        mapping_type = col_mapping.data_type.lower()
        date_format_rule = self._extract_rule_param(col_mapping.validation_rules, 'date_format', 'format')
        declared_format = getattr(col_mapping, 'format', None)

        if mapping_type != 'date' and not date_format_rule and not declared_format:
            return None

        if canonical is None:
            canonical = _raw_name_to_canonical(db_type)

        db_type_u = (db_type or '').upper()
        if canonical not in {CanonicalType.DATE, CanonicalType.TIMESTAMP, CanonicalType.UNKNOWN}:
            return (
                f"Column {target_col}: mapping expects date/date_format but database type is '{db_type_u}'"
            )

        # Optional informational mismatch for time-bearing format on a DATE column.
        effective_format = date_format_rule or declared_format or ''
        if canonical is CanonicalType.DATE and any(
            token in str(effective_format).upper() for token in ('HH', 'MI', 'SS')
        ):
            return (
                f"Column {target_col}: mapping format '{effective_format}' includes time components, "
                f"but database column type is DATE"
            )

        return None

    def _types_compatible(self, mapping_type: str, db_type: str) -> bool:
        """Check if a mapping type is compatible with a raw DB type string.

        Retained for backward compatibility.  Now dialect-free: the raw
        ``db_type`` name is normalised to a :class:`CanonicalType` and compared
        against the canonical matrix, so it answers correctly for Oracle,
        PostgreSQL, and SQLite type names alike.

        Args:
            mapping_type: Type from the mapping document.
            db_type: Raw type name from the database catalog.

        Returns:
            ``True`` if the types are compatible.
        """
        canonical = _raw_name_to_canonical(db_type)
        return canonical_compatible(mapping_type, canonical)

    def generate_reconciliation_report(self, mapping: MappingDocument) -> str:
        """Generate human-readable reconciliation report.

        Args:
            mapping: MappingDocument to reconcile.

        Returns:
            Formatted report string.
        """
        result = self.reconcile_mapping(mapping)

        report = []
        report.append("=" * 70)
        report.append("MAPPING RECONCILIATION REPORT")
        report.append("=" * 70)
        report.append(f"Mapping: {mapping.mapping_name} (v{mapping.version})")
        report.append(f"Target Table: {result.get('table_name', 'N/A')}")
        report.append(f"Status: {'VALID' if result['valid'] else 'INVALID'}")
        report.append("")

        report.append(f"Mapped Columns: {result.get('mapped_columns', 0)}")
        report.append(f"Database Columns: {result.get('database_columns', 0)}")
        report.append("")

        if result['errors']:
            report.append("ERRORS:")
            for error in result['errors']:
                report.append(f"  ✗ {error}")
            report.append("")

        if result['warnings']:
            report.append("WARNINGS:")
            for warning in result['warnings']:
                report.append(f"  ⚠ {warning}")
            report.append("")

        if not result['errors'] and not result['warnings']:
            report.append("✓ No issues found")
            report.append("")

        report.append("=" * 70)

        return "\n".join(report)


class MappingValidator:
    """Validates mapping documents against database schema."""

    def __init__(self, adapter: DatabaseAdapter):
        """Initialize mapping validator.

        Args:
            adapter: A :class:`~src.database.adapters.base.DatabaseAdapter`
                instance.
        """
        self.reconciler = SchemaReconciler(adapter)
        self.logger = get_logger(__name__)

    def validate_all_mappings(self, mappings: List[MappingDocument]) -> Dict[str, Any]:
        """Validate multiple mapping documents.

        Args:
            mappings: List of MappingDocument instances.

        Returns:
            Validation results for all mappings.
        """
        results = {}
        total_valid = 0
        total_invalid = 0

        for mapping in mappings:
            result = self.reconciler.reconcile_mapping(mapping)
            results[mapping.mapping_name] = result

            if result['valid']:
                total_valid += 1
            else:
                total_invalid += 1

        return {
            'total_mappings': len(mappings),
            'valid': total_valid,
            'invalid': total_invalid,
            'results': results,
        }

    def validate_mapping_file(self, mapping_file_path: str) -> Dict[str, Any]:
        """Validate a mapping file against database.

        Args:
            mapping_file_path: Path to mapping JSON file.

        Returns:
            Validation results.
        """
        from ..config.loader import ConfigLoader
        from ..config.mapping_parser import MappingParser

        # Load and parse mapping
        loader = ConfigLoader()
        mapping_dict = loader.load_mapping(mapping_file_path)

        parser = MappingParser()
        mapping = parser.parse(mapping_dict)

        # Reconcile with database
        return self.reconciler.reconcile_mapping(mapping)
