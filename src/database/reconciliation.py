"""Database schema reconciliation with mapping documents."""

from typing import Dict, List, Any, Optional, Tuple, Set
from decimal import Decimal, InvalidOperation
import oracledb
from ..config.mapping_parser import MappingDocument
from .connection import OracleConnection
from .query_executor import QueryExecutor
from ..utils.logger import get_logger


class SchemaReconciler:
    """Reconciles mapping documents with actual database schema."""

    def __init__(self, connection: OracleConnection):
        """Initialize schema reconciler.
        
        Args:
            connection: OracleConnection instance
        """
        self.connection = connection
        self.executor = QueryExecutor(connection)
        self.logger = get_logger(__name__)

    def reconcile_mapping(self, mapping: MappingDocument) -> Dict[str, Any]:
        """Reconcile mapping document with database schema.
        
        Args:
            mapping: MappingDocument to reconcile
            
        Returns:
            Reconciliation results with errors and warnings
        """
        errors = []
        warnings = []
        
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
            
            # Check data type compatibility
            db_type = col_info.get('data_type', '')
            mapping_type = col_mapping.data_type
            
            if not self._types_compatible(mapping_type, db_type):
                warnings.append(
                    f"Type mismatch for {target_col}: "
                    f"mapping expects '{mapping_type}', database has '{db_type}'"
                )
            
            # Check nullable constraint
            nullable = col_info.get('nullable', 'Y')
            if col_mapping.required and nullable == 'Y':
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
            date_warning = self._check_date_format_compatibility(target_col, col_mapping, db_type)
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
        """Parse table reference into (owner, table_name)."""
        if '.' in table_reference:
            owner, table_name = table_reference.split('.', 1)
            return owner.upper(), table_name.upper()
        return None, table_reference.upper()

    def _table_exists(self, table_name: str, owner: Optional[str] = None) -> bool:
        """Check if table exists."""
        if owner:
            query = """
                SELECT COUNT(*) as cnt
                FROM all_tables
                WHERE owner = :owner AND table_name = :table_name
            """
            params = {'owner': owner.upper(), 'table_name': table_name.upper()}
        else:
            query = """
                SELECT COUNT(*) as cnt
                FROM user_tables
                WHERE table_name = :table_name
            """
            params = {'table_name': table_name.upper()}

        try:
            df = self.executor.execute_query(query, params)
            return df['CNT'].iloc[0] > 0
        except Exception as e:
            self.logger.error(f"Error checking table existence: {e}")
            return False

    def _get_table_columns(self, table_name: str, owner: Optional[str] = None) -> List[str]:
        """Get list of column names for table."""
        if owner:
            query = """
                SELECT column_name
                FROM all_tab_columns
                WHERE owner = :owner
                AND table_name = :table_name
                ORDER BY column_id
            """
            try:
                df = self.executor.execute_query(query, {'owner': owner.upper(), 'table_name': table_name.upper()})
                return df['COLUMN_NAME'].tolist()
            except Exception as e:
                self.logger.error(f"Error getting table columns: {e}")
                return []

        return self.executor.fetch_table_columns(table_name)

    def _get_column_details(self, table_name: str, owner: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """Get detailed column information."""
        if owner:
            query = """
                SELECT
                    column_name,
                    data_type,
                    data_length,
                    data_precision,
                    data_scale,
                    nullable
                FROM all_tab_columns
                WHERE owner = :owner
                AND table_name = :table_name
                ORDER BY column_id
            """
            params = {'owner': owner.upper(), 'table_name': table_name.upper()}
        else:
            query = """
                SELECT
                    column_name,
                    data_type,
                    data_length,
                    data_precision,
                    data_scale,
                    nullable
                FROM user_tab_columns
                WHERE table_name = :table_name
                ORDER BY column_id
            """
            params = {'table_name': table_name.upper()}

        try:
            df = self.executor.execute_query(query, params)
            
            result = {}
            for _, row in df.iterrows():
                result[row['COLUMN_NAME']] = {
                    'data_type': row['DATA_TYPE'],
                    'data_length': row['DATA_LENGTH'],
                    'data_precision': row['DATA_PRECISION'],
                    'data_scale': row['DATA_SCALE'],
                    'nullable': row['NULLABLE'],
                }
            
            return result
        except Exception as e:
            self.logger.error(f"Error getting column details: {e}")
            return {}

    def _get_required_columns(self, table_name: str, owner: Optional[str] = None) -> Set[str]:
        """Get set of required (NOT NULL) columns."""
        if owner:
            query = """
                SELECT column_name
                FROM all_tab_columns
                WHERE owner = :owner
                AND table_name = :table_name
                AND nullable = 'N'
            """
            params = {'owner': owner.upper(), 'table_name': table_name.upper()}
        else:
            query = """
                SELECT column_name
                FROM user_tab_columns
                WHERE table_name = :table_name
                AND nullable = 'N'
            """
            params = {'table_name': table_name.upper()}

        try:
            df = self.executor.execute_query(query, params)
            return set(df['COLUMN_NAME'].tolist())
        except Exception as e:
            self.logger.error(f"Error getting required columns: {e}")
            return set()

    def _resolve_mapping_key_targets(self, mapping: MappingDocument) -> List[str]:
        """Resolve mapping key columns from source names to target column names."""
        source_to_target = {m.source_column: m.target_column for m in mapping.mappings}
        key_targets = []
        for key_col in mapping.key_columns or []:
            key_targets.append(source_to_target.get(key_col, key_col).upper())
        return key_targets

    def _get_pk_unique_constraint_columns(self, table_name: str, owner: Optional[str] = None) -> List[Set[str]]:
        """Get PK/UNIQUE constraint column sets for table."""
        if owner:
            query = """
                SELECT acc.constraint_name, acc.column_name
                FROM all_constraints ac
                JOIN all_cons_columns acc
                  ON ac.owner = acc.owner
                 AND ac.constraint_name = acc.constraint_name
                 AND ac.table_name = acc.table_name
                WHERE ac.owner = :owner
                  AND ac.table_name = :table_name
                  AND ac.constraint_type IN ('P', 'U')
                ORDER BY acc.constraint_name, acc.position
            """
            params = {'owner': owner.upper(), 'table_name': table_name.upper()}
        else:
            query = """
                SELECT acc.constraint_name, acc.column_name
                FROM user_constraints ac
                JOIN user_cons_columns acc
                  ON ac.constraint_name = acc.constraint_name
                 AND ac.table_name = acc.table_name
                WHERE ac.table_name = :table_name
                  AND ac.constraint_type IN ('P', 'U')
                ORDER BY acc.constraint_name, acc.position
            """
            params = {'table_name': table_name.upper()}

        try:
            df = self.executor.execute_query(query, params)
            grouped: Dict[str, Set[str]] = {}
            for _, row in df.iterrows():
                cname = row['CONSTRAINT_NAME']
                col = row['COLUMN_NAME']
                grouped.setdefault(cname, set()).add(col)
            return list(grouped.values())
        except Exception as e:
            self.logger.error(f"Error getting PK/UNIQUE constraints: {e}")
            return []

    def _extract_rule_param(self, validation_rules: List[Dict[str, Any]], rule_type: str, param_name: str) -> Optional[Any]:
        """Extract parameter value from first matching validation rule."""
        for rule in validation_rules or []:
            if rule.get('type') == rule_type:
                return rule.get('parameters', {}).get(param_name)
        return None

    def _check_numeric_precision_scale(self, target_col: str, col_mapping, col_info: Dict[str, Any]) -> Optional[str]:
        """Validate mapping numeric constraints against DB precision/scale."""
        db_type = str(col_info.get('data_type', '')).upper()
        if db_type not in {'NUMBER', 'INTEGER', 'FLOAT', 'BINARY_FLOAT', 'BINARY_DOUBLE'}:
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

    def _check_date_format_compatibility(self, target_col: str, col_mapping, db_type: str) -> Optional[str]:
        """Warn when mapping has date format hints against non-date DB types."""
        mapping_type = col_mapping.data_type.lower()
        date_format_rule = self._extract_rule_param(col_mapping.validation_rules, 'date_format', 'format')
        declared_format = getattr(col_mapping, 'format', None)

        if mapping_type != 'date' and not date_format_rule and not declared_format:
            return None

        db_type_u = (db_type or '').upper()
        date_types = {'DATE', 'TIMESTAMP', 'TIMESTAMP WITH TIME ZONE', 'TIMESTAMP WITH LOCAL TIME ZONE'}
        if db_type_u not in date_types:
            return (
                f"Column {target_col}: mapping expects date/date_format but database type is '{db_type_u}'"
            )

        # Optional informational mismatch for time-bearing format on DATE
        effective_format = date_format_rule or declared_format or ''
        if db_type_u == 'DATE' and any(token in str(effective_format).upper() for token in ('HH', 'MI', 'SS')):
            return (
                f"Column {target_col}: mapping format '{effective_format}' includes time components, "
                f"but database column type is DATE"
            )

        return None

    def _types_compatible(self, mapping_type: str, db_type: str) -> bool:
        """Check if mapping type is compatible with database type.
        
        Args:
            mapping_type: Type from mapping document
            db_type: Type from database
            
        Returns:
            True if types are compatible
        """
        # Define compatibility matrix
        compatibility = {
            'string': ['VARCHAR2', 'CHAR', 'NVARCHAR2', 'NCHAR', 'CLOB', 'NCLOB'],
            'number': ['NUMBER', 'INTEGER', 'FLOAT', 'BINARY_FLOAT', 'BINARY_DOUBLE'],
            'decimal': ['NUMBER', 'FLOAT', 'BINARY_FLOAT', 'BINARY_DOUBLE'],
            'integer': ['NUMBER', 'INTEGER'],
            'date': ['DATE', 'TIMESTAMP', 'TIMESTAMP WITH TIME ZONE', 'TIMESTAMP WITH LOCAL TIME ZONE'],
            'boolean': ['NUMBER', 'CHAR', 'VARCHAR2'],  # Oracle doesn't have native boolean
        }

        compatible_types = compatibility.get(mapping_type.lower(), [])
        return db_type.upper() in compatible_types

    def generate_reconciliation_report(self, mapping: MappingDocument) -> str:
        """Generate human-readable reconciliation report.
        
        Args:
            mapping: MappingDocument to reconcile
            
        Returns:
            Formatted report string
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

    def __init__(self, connection: OracleConnection):
        """Initialize mapping validator.
        
        Args:
            connection: OracleConnection instance
        """
        self.reconciler = SchemaReconciler(connection)
        self.logger = get_logger(__name__)

    def validate_all_mappings(self, mappings: List[MappingDocument]) -> Dict[str, Any]:
        """Validate multiple mapping documents.
        
        Args:
            mappings: List of MappingDocument instances
            
        Returns:
            Validation results for all mappings
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
            mapping_file_path: Path to mapping JSON file
            
        Returns:
            Validation results
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
