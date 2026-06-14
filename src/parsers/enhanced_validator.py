"""Enhanced file validation with data profiling and quality metrics."""

import os
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np
from datetime import datetime
from .base_parser import BaseParser


class EnhancedFileValidator:
    """Enhanced validator with comprehensive data profiling and quality metrics."""

    def __init__(self, parser: BaseParser, mapping_config: Optional[Dict] = None, 
                 rules_config_path: Optional[str] = None):
        """Initialize enhanced validator.
        
        Args:
            parser: Parser instance to use for validation
            mapping_config: Optional mapping configuration for schema validation
            rules_config_path: Optional path to business rules JSON configuration
        """
        self.parser = parser
        self.mapping_config = mapping_config
        self.rules_config_path = rules_config_path
        self.errors: List[Dict[str, Any]] = []
        self.warnings: List[Dict[str, Any]] = []
        self.info: List[Dict[str, Any]] = []
        self.rule_engine = None
        
        # Load business rules if provided
        if rules_config_path:
            self.rule_engine = self._load_rule_engine(rules_config_path)

    def validate(self, detailed: bool = True,
                 strict_fixed_width: bool = False,
                 strict_level: str = 'all') -> Dict[str, Any]:
        """Perform comprehensive file validation with data profiling.
        
        Args:
            detailed: Include detailed field-level analysis
        
        Returns:
            Comprehensive validation results dictionary
        """
        self.errors = []
        self.warnings = []
        self.info = []

        # File metadata
        file_metadata = self._get_file_metadata()

        # Check file exists
        if not self._validate_file_exists():
            return self._build_result(False, file_metadata, None)

        # Check file size
        self._validate_file_size()

        # Check file format
        format_ok = self._validate_format()
        if not format_ok and not hasattr(self.parser, 'column_specs'):
            return self._build_result(False, file_metadata, None)

        # Fixed-width row-length validation (default, non-strict)
        self._validate_fixed_width_row_lengths()

        # Parse and analyze data
        try:
            df = self.parser.parse()
            
            # Data quality metrics
            quality_metrics = self._calculate_quality_metrics(df)
            
            # Field-level analysis
            field_analysis = self._analyze_fields(df) if detailed else {}
            
            # Duplicate analysis
            duplicate_analysis = self._analyze_duplicates(df)
            
            # Date field analysis
            date_analysis = self._analyze_date_fields(df) if detailed else {}
            
            # Schema validation (if mapping provided)
            if self.mapping_config:
                self._validate_schema(df)
            
            # Optional/auto strict fixed-width validation
            strict_result = None
            auto_strict = (
                not strict_fixed_width
                and self.mapping_config
                and isinstance(self.mapping_config, dict)
                and self.mapping_config.get('fields')
                and self.parser.__class__.__name__ == 'FixedWidthParser'
            )
            if strict_fixed_width or auto_strict:
                strict_result = self._validate_strict_fixed_width(df, strict_level=strict_level)

            # First-misalignment diagnostics for fixed-width rows
            fixed_width_alignment = None
            should_run_alignment = (
                self.mapping_config
                and self.mapping_config.get('fields')
                and self.parser.__class__.__name__ == 'FixedWidthParser'
                and (not strict_fixed_width or (strict_level or 'all').lower() in {'format', 'all'})
            )
            if should_run_alignment:
                fixed_width_alignment = self._detect_first_misalignment_by_row(max_details=200)

            # Data profiling
            data_profile = self._profile_data(df) if detailed else {}
            
            # Appendix data
            appendix_data = self._build_appendix_data(df, detailed)
            
            # Business rules validation
            business_rules_result = self._validate_business_rules(df) if self.rule_engine else None
            
        except Exception as e:
            self.errors.append({
                'severity': 'critical',
                'category': 'parsing',
                'message': f"Parse error: {str(e)}",
                'row': None,
                'field': None
            })
            return self._build_result(False, file_metadata, None)

        # Build comprehensive result
        return self._build_result(
            valid=len(self.errors) == 0,
            file_metadata=file_metadata,
            df=df,
            quality_metrics=quality_metrics,
            field_analysis=field_analysis,
            duplicate_analysis=duplicate_analysis,
            date_analysis=date_analysis,
            data_profile=data_profile,
            appendix=appendix_data,
            business_rules=business_rules_result,
            strict_fixed_width=strict_result,
            fixed_width_alignment=fixed_width_alignment,
            issue_code_summary=self._build_issue_code_summary()
        )

    def _get_file_metadata(self) -> Dict[str, Any]:
        """Get file metadata."""
        file_path = self.parser.file_path
        
        if not os.path.exists(file_path):
            return {'file_path': file_path, 'exists': False}
        
        stat = os.stat(file_path)
        
        return {
            'file_path': file_path,
            'file_name': os.path.basename(file_path),
            'exists': True,
            'size_bytes': stat.st_size,
            'size_mb': stat.st_size / (1024 * 1024),
            'modified_time': datetime.fromtimestamp(stat.st_mtime).isoformat(),
            'format': self.parser.__class__.__name__.replace('Parser', '').lower()
        }

    def _validate_file_exists(self) -> bool:
        """Check if file exists."""
        if not os.path.exists(self.parser.file_path):
            self.errors.append({
                'severity': 'critical',
                'category': 'file',
                'message': f"File not found: {self.parser.file_path}",
                'row': None,
                'field': None
            })
            return False
        return True

    def _validate_file_size(self) -> None:
        """Check file size."""
        size = os.path.getsize(self.parser.file_path)
        
        if size == 0:
            self.errors.append({
                'severity': 'critical',
                'category': 'file',
                'message': "File is empty",
                'row': None,
                'field': None
            })
        elif size < 10:
            self.warnings.append({
                'severity': 'warning',
                'category': 'file',
                'message': "File is very small (< 10 bytes)",
                'row': None,
                'field': None
            })
        elif size > 1024 * 1024 * 1024:  # 1GB
            self.info.append({
                'severity': 'info',
                'category': 'file',
                'message': f"Large file detected ({size / (1024**3):.2f} GB)",
                'row': None,
                'field': None
            })

    def _validate_format(self) -> bool:
        """Validate file format."""
        try:
            if not self.parser.validate_format():
                self.errors.append({
                    'severity': 'critical',
                    'category': 'format',
                    'message': "Invalid file format",
                    'row': None,
                    'field': None
                })

                # For fixed-width files, include line-length and impacted-field diagnostics.
                self._add_fixed_width_format_diagnostics()
                return False
            return True
        except Exception as e:
            self.errors.append({
                'severity': 'critical',
                'category': 'format',
                'message': f"Format validation error: {str(e)}",
                'row': None,
                'field': None
            })
            return False

    def _validate_fixed_width_row_lengths(self, max_issue_details: int = 200) -> None:
        """Detect fixed-width row length defects and attach row-level errors."""
        if not hasattr(self.parser, 'column_specs'):
            return

        specs = getattr(self.parser, 'column_specs', None) or []
        if not specs:
            return

        expected_len = max(end for _, _, end in specs)
        mismatch_count = 0

        try:
            with open(self.parser.file_path, 'r', encoding='utf-8', errors='replace') as fh:
                for row_num, line in enumerate(fh, start=1):
                    actual_len = len(line.rstrip('\r\n'))
                    if actual_len != expected_len:
                        mismatch_count += 1
                        if mismatch_count <= max_issue_details:
                            self.errors.append({
                                'severity': 'error',
                                'category': 'format',
                                'code': 'FW_LEN_001',
                                'message': f"Row {row_num} length mismatch: expected {expected_len}, got {actual_len}",
                                'row': row_num,
                                'field': None,
                            })

            if mismatch_count > max_issue_details:
                self.warnings.append({
                    'severity': 'warning',
                    'category': 'format',
                    'code': 'FW_LEN_002',
                    'message': (
                        f"Detected {mismatch_count} row-length mismatches; "
                        f"showing first {max_issue_details} in error details"
                    ),
                    'row': None,
                    'field': None,
                })
        except Exception as e:
            self.warnings.append({
                'severity': 'warning',
                'category': 'format',
                'code': 'FW_LEN_003',
                'message': f"Row-length validation skipped: {e}",
                'row': None,
                'field': None,
            })

    def _calculate_quality_metrics(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Calculate overall data quality metrics."""
        total_cells = df.shape[0] * df.shape[1]
        null_cells = df.isnull().sum().sum()
        filled_cells = total_cells - null_cells
        
        # Completeness
        completeness = (filled_cells / total_cells * 100) if total_cells > 0 else 0
        
        # Uniqueness
        total_rows = len(df)
        unique_rows = len(df.drop_duplicates())
        uniqueness = (unique_rows / total_rows * 100) if total_rows > 0 else 0
        
        # Overall quality score (weighted average)
        quality_score = (completeness * 0.6 + uniqueness * 0.4)
        
        return {
            'total_rows': total_rows,
            'total_columns': df.shape[1],
            'total_cells': total_cells,
            'filled_cells': filled_cells,
            'null_cells': null_cells,
            'completeness_pct': round(completeness, 2),
            'unique_rows': unique_rows,
            'duplicate_rows': total_rows - unique_rows,
            'uniqueness_pct': round(uniqueness, 2),
            'quality_score': round(quality_score, 2)
        }

    def _analyze_fields(self, df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
        """Perform field-level analysis."""
        field_analysis = {}
        
        for col in df.columns:
            field_analysis[col] = self._analyze_field(df[col], col)
        
        return field_analysis

    def _analyze_field(self, series: pd.Series, field_name: str) -> Dict[str, Any]:
        """Analyze a single field."""
        total = len(series)
        null_count = series.isnull().sum()
        filled_count = total - null_count
        fill_rate = (filled_count / total * 100) if total > 0 else 0
        
        # Infer data type
        dtype = str(series.dtype)
        inferred_type = self._infer_data_type(series)
        
        # Unique values
        unique_count = series.nunique()
        unique_ratio = (unique_count / filled_count * 100) if filled_count > 0 else 0
        
        # Sample values
        sample_values = series.dropna().head(5).tolist()
        
        analysis = {
            'data_type': dtype,
            'inferred_type': inferred_type,
            'total_values': total,
            'null_count': null_count,
            'filled_count': filled_count,
            'fill_rate_pct': round(fill_rate, 2),
            'unique_count': unique_count,
            'unique_ratio_pct': round(unique_ratio, 2),
            'sample_values': [str(v) for v in sample_values]
        }
        
        # Numeric analysis
        if inferred_type == 'numeric':
            analysis.update(self._analyze_numeric_field(series))
        
        # String analysis
        elif inferred_type == 'string':
            analysis.update(self._analyze_string_field(series))
        
        return analysis

    def _infer_data_type(self, series: pd.Series) -> str:
        """Infer the actual data type of a field."""
        import re
        
        non_null = series.dropna()
        
        if len(non_null) == 0:
            return 'empty'
        
        # Check if field name suggests it's a date
        field_name = series.name if hasattr(series, 'name') else ''
        is_date_field = bool(re.search(r'date|time|dt|timestamp', str(field_name), re.IGNORECASE))
        
        # For potential date fields, try datetime first
        if is_date_field:
            # Check for YYYYMMDD pattern (8-digit dates) first
            # Sample first 100 values to check pattern
            sample = non_null.head(100).astype(str).str.strip()
            yyyymmdd_pattern = r'^\d{8}$'
            yyyymmdd_matches = sample.str.match(yyyymmdd_pattern).sum()
            
            if yyyymmdd_matches / len(sample) >= 0.8:  # 80% match YYYYMMDD pattern
                # Try to parse as dates
                try:
                    import warnings
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        parsed = pd.to_datetime(sample, format='%Y%m%d', errors='coerce')
                        valid_dates = parsed.notna().sum()
                        if valid_dates / len(sample) >= 0.8:
                            return 'datetime'
                except:
                    pass
            
            # Try general datetime parsing for date fields
            try:
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    parsed = pd.to_datetime(non_null, errors='coerce')
                    valid_pct = parsed.notna().sum() / len(non_null) * 100
                    if valid_pct >= 50:
                        return 'datetime'
            except:
                pass
        
        # Try numeric (but only if not a date pattern)
        try:
            pd.to_numeric(non_null)
            return 'numeric'
        except:
            pass
        
        # Try datetime for non-date-named fields
        if not is_date_field:
            try:
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    pd.to_datetime(non_null)
                    return 'datetime'
            except:
                pass
        
        return 'string'

    def _analyze_numeric_field(self, series: pd.Series) -> Dict[str, Any]:
        """Analyze numeric field."""
        numeric_series = pd.to_numeric(series, errors='coerce').dropna()
        
        if len(numeric_series) == 0:
            return {}
        
        return {
            'min': float(numeric_series.min()),
            'max': float(numeric_series.max()),
            'mean': float(numeric_series.mean()),
            'median': float(numeric_series.median()),
            'std_dev': float(numeric_series.std())
        }

    def _analyze_string_field(self, series: pd.Series) -> Dict[str, Any]:
        """Analyze string field."""
        string_series = series.dropna().astype(str)
        
        if len(string_series) == 0:
            return {}
        
        lengths = string_series.str.len()
        
        return {
            'min_length': int(lengths.min()),
            'max_length': int(lengths.max()),
            'avg_length': round(float(lengths.mean()), 2)
        }

    def _analyze_duplicates(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Analyze duplicate rows."""
        total_rows = len(df)
        unique_rows = len(df.drop_duplicates())
        duplicate_rows = total_rows - unique_rows
        
        # Find most duplicated rows
        if duplicate_rows > 0:
            dup_counts = df.groupby(list(df.columns)).size().reset_index(name='count')
            dup_counts = dup_counts[dup_counts['count'] > 1].sort_values('count', ascending=False)
            top_duplicates = dup_counts.head(10)['count'].tolist()
        else:
            top_duplicates = []
        
        return {
            'total_rows': total_rows,
            'unique_rows': unique_rows,
            'duplicate_rows': duplicate_rows,
            'duplicate_pct': round((duplicate_rows / total_rows * 100) if total_rows > 0 else 0, 2),
            'top_duplicate_counts': top_duplicates
        }

    def _validate_schema(self, df: pd.DataFrame) -> None:
        """Validate data against schema from mapping."""
        if not self.mapping_config or 'fields' not in self.mapping_config:
            return
        
        expected_fields = {f['name'] for f in self.mapping_config['fields']}
        actual_fields = set(df.columns)
        
        # Missing fields
        missing = expected_fields - actual_fields
        if missing:
            for field in missing:
                self.errors.append({
                    'severity': 'error',
                    'category': 'schema',
                    'code': 'VAL_SCHEMA_MISSING_FIELD',
                    'message': f"Missing required field: {field}",
                    'row': None,
                    'field': field
                })
        
        # Extra fields — exclude internal columns added by parsers (e.g. __source_row__)
        extra = actual_fields - expected_fields - {'__source_row__'}
        if extra:
            for field in extra:
                self.warnings.append({
                    'severity': 'warning',
                    'category': 'schema',
                    'message': f"Unexpected field: {field}",
                    'row': None,
                    'field': field
                })

    def _add_fixed_width_format_diagnostics(self) -> None:
        """Add detailed fixed-width diagnostics (line length + impacted fields)."""
        from src.parsers.fixed_width_parser import FixedWidthParser

        if not isinstance(self.parser, FixedWidthParser):
            return

        try:
            analysis = self.parser.analyze_line_lengths(sample_size=200)
            expected_len = analysis.get('expected_length', 0)
            mismatch_count = analysis.get('mismatch_count', 0)
            total_lines = analysis.get('total_lines', 0)

            correct_count = total_lines - mismatch_count
            incorrect_rows = [m['line_number'] for m in analysis.get('mismatches', [])]

            # Always include correct/incorrect line summary for operator visibility.
            self.info.append({
                'severity': 'info',
                'category': 'format',
                'code': 'FW_LEN_000',
                'message': (
                    f"Line length summary: correct={correct_count}, incorrect={mismatch_count}, "
                    f"total={total_lines}, expected_length={expected_len}."
                ),
                'row': None,
                'field': None,
            })

            if mismatch_count == 0:
                return

            self.errors.append({
                'severity': 'error',
                'category': 'format',
                'code': 'FW_LEN_001',
                'message': (
                    f"Line length mismatch found in {mismatch_count} of {total_lines} rows "
                    f"(expected length={expected_len}). Incorrect rows sample: {incorrect_rows[:20]}"
                ),
                'row': None,
                'field': None,
            })

            # Build field ranges from parser spec
            field_ranges = [(name, start, end) for name, start, end in self.parser.column_specs]

            for mm in analysis.get('mismatches', []):
                row = mm['line_number']
                actual = mm['actual_length']
                if actual < expected_len:
                    impacted = [name for name, start, end in field_ranges if end > actual]
                    field_hint = ', '.join(impacted[:8])
                    if len(impacted) > 8:
                        field_hint += f", +{len(impacted)-8} more"
                    msg = (
                        f"Row {row} length mismatch: expected {expected_len}, got {actual}. "
                        f"Impacted/truncated fields: {field_hint if field_hint else 'unknown'}."
                    )
                    self.errors.append({
                        'severity': 'error',
                        'category': 'format',
                        'code': 'FW_LEN_002',
                        'message': msg,
                        'row': row,
                        'field': field_hint if field_hint else None,
                        'expected_length': expected_len,
                        'actual_length': actual,
                    })
                else:
                    extra = actual - expected_len
                    msg = (
                        f"Row {row} length mismatch: expected {expected_len}, got {actual}. "
                        f"Trailing extra data length: {extra}."
                    )
                    self.errors.append({
                        'severity': 'error',
                        'category': 'format',
                        'code': 'FW_LEN_003',
                        'message': msg,
                        'row': row,
                        'field': '__TRAILING_DATA__',
                        'expected_length': expected_len,
                        'actual_length': actual,
                    })

            remaining = mismatch_count - len(analysis.get('mismatches', []))
            if remaining > 0:
                self.warnings.append({
                    'severity': 'warning',
                    'category': 'format',
                    'code': 'FW_LEN_004',
                    'message': f"{remaining} additional mismatched rows omitted from detailed diagnostics.",
                    'row': None,
                    'field': None,
                })
        except Exception as e:
            self.warnings.append({
                'severity': 'warning',
                'category': 'format',
                'message': f"Could not compute fixed-width diagnostics: {e}",
                'row': None,
                'field': None,
            })

    def _profile_data(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Generate data profile statistics."""
        return {
            'row_count': len(df),
            'column_count': len(df.columns),
            'memory_usage_mb': round(df.memory_usage(deep=True).sum() / (1024 * 1024), 2),
            'columns': list(df.columns)
        }

    def _analyze_date_fields(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Analyze date/datetime fields comprehensively."""
        import warnings
        
        date_analysis = {}
        
        for col in df.columns:
            # Try to parse as datetime
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    date_series = pd.to_datetime(df[col], errors='coerce')
                
                # Check if at least 50% of values are valid dates
                valid_dates = date_series.notna()
                valid_pct = valid_dates.sum() / len(df) * 100 if len(df) > 0 else 0
                
                if valid_pct >= 50:  # Consider it a date field if >= 50% are valid dates
                    invalid_count = (~valid_dates).sum()
                    future_count = (date_series > pd.Timestamp.now()).sum()
                    null_count = df[col].isna().sum()
                    
                    # Detect date formats
                    detected_formats = self._detect_date_formats(df[col])
                    
                    # Calculate date range
                    valid_date_series = date_series[valid_dates]
                    if len(valid_date_series) > 0:
                        earliest = valid_date_series.min()
                        latest = valid_date_series.max()
                        date_range_days = (latest - earliest).days
                    else:
                        earliest = None
                        latest = None
                        date_range_days = 0
                    
                    date_analysis[col] = {
                        'earliest_date': earliest.isoformat() if earliest else None,
                        'latest_date': latest.isoformat() if latest else None,
                        'date_range_days': date_range_days,
                        'valid_date_count': valid_dates.sum(),
                        'valid_date_pct': round(valid_pct, 2),
                        'invalid_date_count': invalid_count,
                        'invalid_date_pct': round((invalid_count / len(df) * 100) if len(df) > 0 else 0, 2),
                        'future_date_count': future_count,
                        'future_date_pct': round((future_count / len(df) * 100) if len(df) > 0 else 0, 2),
                        'null_date_count': null_count,
                        'null_date_pct': round((null_count / len(df) * 100) if len(df) > 0 else 0, 2),
                        'detected_formats': detected_formats
                    }
                    
                    # Add warnings for invalid/future dates
                    if invalid_count > 0:
                        expected_format = self._get_expected_date_format(col)
                        format_hint = f" Expected format: {expected_format}." if expected_format else ""
                        self.warnings.append({
                            'severity': 'warning',
                            'category': 'data',
                            'message': (
                                f"Field '{col}' has {invalid_count} invalid date values "
                                f"({invalid_count / len(df) * 100:.2f}%).{format_hint}"
                            ),
                            'row': None,
                            'field': col,
                            'expected_format': expected_format
                        })
                    
                    if future_count > 0:
                        self.info.append({
                            'severity': 'info',
                            'category': 'data',
                            'message': f"Field '{col}' has {future_count} future date values ({future_count / len(df) * 100:.2f}%)",
                            'row': None,
                            'field': col
                        })
            except:
                continue
        
        return date_analysis

    def _get_expected_date_format(self, field_name: str) -> Optional[str]:
        """Get expected date format from mapping config, if provided."""
        if not self.mapping_config:
            return None

        # Universal mapping format
        for field in self.mapping_config.get('fields', []):
            if field.get('name') == field_name and field.get('format'):
                return str(field.get('format'))

        # Legacy mapping format
        for m in self.mapping_config.get('mappings', []):
            if m.get('source_column') == field_name:
                if m.get('format'):
                    return str(m.get('format'))
                for rule in m.get('validation_rules', []):
                    if rule.get('type') == 'date_format':
                        fmt = rule.get('parameters', {}).get('format')
                        if fmt:
                            return str(fmt)

        return None

    def _detect_date_formats(self, series: pd.Series) -> List[str]:
        """Detect common date formats in the series."""
        import warnings
        
        formats = []
        sample = series.dropna().head(100)
        
        if len(sample) == 0:
            return formats
        
        common_formats = [
            ('%Y-%m-%d', 'YYYY-MM-DD'),
            ('%m/%d/%Y', 'MM/DD/YYYY'),
            ('%d/%m/%Y', 'DD/MM/YYYY'),
            ('%Y%m%d', 'YYYYMMDD'),
            ('%m-%d-%Y', 'MM-DD-YYYY'),
            ('%d-%m-%Y', 'DD-MM-YYYY'),
            ('%Y/%m/%d', 'YYYY/MM/DD'),
        ]
        
        for fmt_code, fmt_name in common_formats:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    parsed = pd.to_datetime(sample, format=fmt_code, errors='coerce')
                    if parsed.notna().sum() / len(sample) > 0.8:
                        formats.append(fmt_name)
            except:
                continue
        
        return formats if formats else ['Mixed/Unknown']

    def _build_appendix_data(self, df: pd.DataFrame, detailed: bool) -> Dict[str, Any]:
        """Build appendix data for the report."""
        # Validation configuration
        validation_config = {
            'detailed_mode': detailed,
            'mapping_file': self.mapping_config.get('file_path') if self.mapping_config and 'file_path' in self.mapping_config else None,
            'validation_timestamp': datetime.now().isoformat(),
            'validator_version': '1.0.0'
        }
        
        # Mapping details
        mapping_details = self._get_mapping_details()
        
        # Affected rows summary
        all_issues = self.errors + self.warnings
        affected_rows_summary = self._get_affected_rows_summary(all_issues, len(df))
        
        return {
            'validation_config': validation_config,
            'mapping_details': mapping_details,
            'affected_rows': affected_rows_summary
        }

    def _get_mapping_details(self) -> Optional[Dict[str, Any]]:
        """Extract mapping file details."""
        if not self.mapping_config or 'fields' not in self.mapping_config:
            return None
        
        fields = self.mapping_config.get('fields', [])
        required_fields = [f['name'] for f in fields if f.get('required', False)]
        
        # Calculate total width for fixed-width files
        total_width = sum(f.get('length', 0) for f in fields)
        
        return {
            'total_fields': len(fields),
            'field_names': [f['name'] for f in fields],
            'required_fields': required_fields,
            'required_field_count': len(required_fields),
            'total_width': total_width if total_width > 0 else None
        }

    def _get_affected_rows_summary(self, issues: List[Dict], total_rows: int) -> Dict[str, Any]:
        """Get summary of rows affected by issues."""
        affected_rows = set()
        issues_by_row = {}
        
        for issue in issues:
            if 'row' in issue and issue['row'] is not None:
                row_num = issue['row']
                affected_rows.add(row_num)
                
                if row_num not in issues_by_row:
                    issues_by_row[row_num] = []
                issues_by_row[row_num].append(issue)
        
        # Keep row ordering stable/ascending for operator readability.
        top_problematic = sorted(
            issues_by_row.items(),
            key=lambda x: int(x[0])
        )[:100]
        
        return {
            'total_affected_rows': len(affected_rows),
            'affected_row_pct': round((len(affected_rows) / total_rows * 100) if total_rows > 0 else 0, 2),
            'top_problematic_rows': [
                {
                    'row_number': row,
                    'issue_count': len(issues),
                    'issues': [i.get('message', '') for i in issues[:10]]  # Limit to 10 issues per row
                }
                for row, issues in top_problematic
            ]
        }

    def _build_fixed_width_field_specs(self) -> List[Dict[str, Any]]:
        """Build ordered fixed-width field specs (name/start/end/length/meta).

        Uses parser column specs as the positional source of truth to match actual parsing,
        then enriches with mapping metadata (required/format/valid_values).
        """
        specs: List[Dict[str, Any]] = []
        mapping_by_name = {
            str(f.get('name')): f for f in (self.mapping_config or {}).get('fields', []) if f.get('name')
        }

        parser_specs = getattr(self.parser, 'column_specs', []) or []
        for name, start, end in parser_specs:
            meta = mapping_by_name.get(str(name), {})
            length = int(end) - int(start)
            specs.append({
                'name': name,
                'start': int(start),
                'end': int(end),
                'length': length,
                'required': bool(meta.get('required', False)),
                'format': str(meta.get('format') or '').upper(),
                'valid_values': meta.get('valid_values') or [],
            })
        return specs

    def _is_value_valid_for_format(self, value: str, fmt: str) -> bool:
        """Validate a string value against fixed-width picture format."""
        import re

        v = str(value).strip()
        fmt = str(fmt or '').upper()
        if not fmt:
            return True

        if fmt == 'XXX':
            return bool(re.fullmatch(r'[A-Za-z]{3}', v))
        if fmt == 'CCYYMMDD':
            return bool(re.fullmatch(r'\d{8}', v))

        m_s9 = re.fullmatch(r'S9\((\d+)\)', fmt)
        if m_s9:
            n = int(m_s9.group(1))
            return bool(re.fullmatch(rf'[+-]?\d{{{n}}}', v))

        m_9 = re.fullmatch(r'9\((\d+)\)', fmt)
        if m_9:
            n = int(m_9.group(1))
            return bool(re.fullmatch(rf'\d{{{n}}}', v))

        m_dec = re.fullmatch(r'([+S])?9\((\d+)\)V9\((\d+)\)', fmt)
        if m_dec:
            sign_kind = m_dec.group(1)
            n = int(m_dec.group(2))
            m = int(m_dec.group(3))
            if sign_kind == '+':
                return bool(re.fullmatch(rf'[+-]\d{{{n+m}}}', v))
            if sign_kind == 'S':
                return bool(re.fullmatch(rf'[+-]?\d{{{n+m}}}', v))
            return bool(re.fullmatch(rf'\d{{{n+m}}}', v))

        # X(N) — alphanumeric characters (A-Za-z0-9) and spaces; length not
        # enforced here because the parser strips trailing spaces from text fields.
        m_xn = re.fullmatch(r'X\((\d+)\)', fmt)
        if m_xn:
            return bool(re.fullmatch(r'[A-Za-z0-9 ]*', v))

        # A(N) — alphabetic characters and spaces only.
        m_an = re.fullmatch(r'A\((\d+)\)', fmt)
        if m_an:
            return bool(re.fullmatch(r'[A-Za-z ]*', v))

        return True

    def _detect_first_misalignment_by_row(self, max_details: int = 200) -> Dict[str, Any]:
        """Detect first incorrect mapping position per row in fixed-width file."""
        specs = self._build_fixed_width_field_specs()
        if not specs:
            return {'enabled': False, 'rows_scanned': 0, 'misaligned_rows': 0, 'details': []}

        expected_len = max(s['end'] for s in specs)
        details = []
        total_rows = 0
        misaligned_rows = 0
        length_mismatch_rows = 0
        field_error_rows = 0

        with open(self.parser.file_path, 'r', encoding='utf-8', errors='replace') as f:
            for row_no, line in enumerate(f, start=1):
                total_rows += 1
                raw = line.rstrip('\n')
                actual_len = len(raw)

                first_issue = None
                has_length_mismatch = actual_len != expected_len
                if has_length_mismatch:
                    length_mismatch_rows += 1
                    first_issue = {
                        'reason': 'line_length_mismatch',
                        'field': None,
                        'offset': min(actual_len, expected_len),
                        'expected_length': expected_len,
                        'actual_length': actual_len,
                        'expected': str(expected_len),
                        'actual': str(actual_len),
                    }

                # Field-level checks in order; first failing field becomes first misalignment.
                for s in specs:
                    seg = raw[s['start']:s['end']] if s['start'] < actual_len else ''

                    if len(seg) < s['length']:
                        first_issue = {
                            'reason': 'field_truncated',
                            'field': s['name'],
                            'offset': s['start'] + 1,
                            'expected_length': expected_len,
                            'actual_length': actual_len,
                            'expected': f"len={s['length']}",
                            'actual': f"len={len(seg)}",
                        }
                        break

                    val = seg.strip()
                    if s['required'] and val == '':
                        first_issue = {
                            'reason': 'required_empty',
                            'field': s['name'],
                            'offset': s['start'] + 1,
                            'expected_length': expected_len,
                            'actual_length': actual_len,
                            'expected': 'non-empty',
                            'actual': 'empty',
                        }
                        break

                    if val and s['valid_values']:
                        allowed = {str(v).strip() for v in s['valid_values']}
                        if val not in allowed:
                            first_issue = {
                                'reason': 'invalid_value',
                                'field': s['name'],
                                'offset': s['start'] + 1,
                                'expected_length': expected_len,
                                'actual_length': actual_len,
                                'expected': f"one of {sorted(list(allowed))[:10]}",
                                'actual': val,
                            }
                            break

                    if val and s['format'] and not self._is_value_valid_for_format(val, s['format']):
                        first_issue = {
                            'reason': 'invalid_format',
                            'field': s['name'],
                            'offset': s['start'] + 1,
                            'expected_length': expected_len,
                            'actual_length': actual_len,
                            'expected': s['format'],
                            'actual': val,
                        }
                        break

                if first_issue:
                    misaligned_rows += 1
                    if first_issue.get('reason') != 'line_length_mismatch':
                        field_error_rows += 1
                    if len(details) < max_details:
                        details.append({'row': row_no, **first_issue})
                        self.errors.append({
                            'severity': 'error',
                            'category': 'fixed_width_alignment',
                            'code': 'FW_ALIGN_002',
                            'message': (
                                f"Row {row_no} first misalignment at offset {first_issue['offset']}: "
                                f"field={first_issue.get('field') or '__ROW__'}, reason={first_issue['reason']}, "
                                f"expected={first_issue['expected']}, actual={first_issue['actual']}"
                            ),
                            'row': row_no,
                            'field': first_issue.get('field'),
                        })

        self.info.append({
            'severity': 'info',
            'category': 'fixed_width_alignment',
            'code': 'FW_ALIGN_000',
            'message': (
                f"Fixed-width alignment summary: correct_by_length={total_rows - length_mismatch_rows}, "
                f"incorrect_by_length={length_mismatch_rows}, rows_with_field_validation_errors={field_error_rows}, "
                f"total_rows={total_rows}, expected_length={expected_len}."
            ),
            'row': None,
            'field': None,
        })

        if misaligned_rows > 0:
            self.errors.append({
                'severity': 'error',
                'category': 'fixed_width_alignment',
                'code': 'FW_ALIGN_001',
                'message': (
                    f"Found first-misalignment issues in {misaligned_rows}/{total_rows} rows "
                    f"(showing up to {max_details} rows)."
                ),
                'row': None,
                'field': None,
            })

        if misaligned_rows > len(details):
            self.warnings.append({
                'severity': 'warning',
                'category': 'fixed_width_alignment',
                'code': 'FW_ALIGN_003',
                'message': f"{misaligned_rows - len(details)} additional misaligned rows omitted from detail section.",
                'row': None,
                'field': None,
            })

        return {
            'enabled': True,
            'rows_scanned': total_rows,
            'misaligned_rows': misaligned_rows,
            'correct_rows': total_rows - misaligned_rows,
            'correct_by_length': total_rows - length_mismatch_rows,
            'incorrect_by_length': length_mismatch_rows,
            'rows_with_field_validation_errors': field_error_rows,
            'expected_length': expected_len,
            'details': details,
        }

    def _validate_strict_fixed_width(self, df: pd.DataFrame, strict_level: str = 'all') -> Dict[str, Any]:
        """Strict fixed-width validation for required/format/valid_values checks."""
        strict_level = (strict_level or 'all').lower()
        if strict_level == 'all':
            strict_level = 'format'

        mapping_fields = (self.mapping_config or {}).get('fields', [])
        invalid_row_numbers = set()
        format_errors = 0

        for _, field in enumerate(mapping_fields):
            name = field.get('name')
            if name not in df.columns:
                continue

            col = df[name]
            required = bool(field.get('required', False))

            # Required checks (basic + format)
            if required:
                mask_required = col.isna() | (col.astype(str).str.strip() == '')
                for idx in df[mask_required].index:
                    invalid_row_numbers.add(int(idx) + 1)
                    self.errors.append({
                        'severity': 'error',
                        'category': 'strict_fixed_width',
                        'code': 'FW_REQ_001',
                        'message': f"Required field '{name}' is empty",
                        'row': int(idx) + 1,
                        'field': name,
                    })

            if strict_level not in {'format'}:
                continue

            non_empty = col[~(col.isna() | (col.astype(str).str.strip() == ''))].astype(str)

            # valid_values checks (FW_VAL_001)
            valid_values = field.get('valid_values')
            if valid_values:
                allowed = {str(v) for v in valid_values}
                bad_mask = ~non_empty.isin(allowed)
                for idx in non_empty[bad_mask].index:
                    invalid_row_numbers.add(int(idx) + 1)
                    format_errors += 1
                    self.errors.append({
                        'severity': 'error',
                        'category': 'strict_fixed_width',
                        'code': 'FW_VAL_001',
                        'message': f"Field '{name}' has invalid value '{df.loc[idx, name]}'",
                        'row': int(idx) + 1,
                        'field': name,
                    })

            # format checks (FW_FMT_001)
            fmt = str(field.get('format') or '').upper()
            if fmt:
                bad_mask = ~non_empty.apply(lambda v: self._is_value_valid_for_format(v, fmt))
                for idx in non_empty[bad_mask].index:
                    invalid_row_numbers.add(int(idx) + 1)
                    format_errors += 1
                    self.errors.append({
                        'severity': 'error',
                        'category': 'strict_fixed_width',
                        'code': 'FW_FMT_001',
                        'message': f"Field '{name}' has invalid format for value '{df.loc[idx, name]}'",
                        'row': int(idx) + 1,
                        'field': name,
                    })

        invalid_rows_sorted = sorted(invalid_row_numbers)
        return {
            'enabled': True,
            'strict_level': strict_level,
            'invalid_records': len(invalid_rows_sorted),
            'invalid_row_numbers': invalid_rows_sorted,
            'format_errors': format_errors,
        }

    def _build_issue_code_summary(self) -> Dict[str, int]:
        summary: Dict[str, int] = {}
        for issue in self.errors + self.warnings + self.info:
            code = issue.get('code') if isinstance(issue, dict) else None
            if code:
                summary[code] = summary.get(code, 0) + 1
        return summary

    def _build_result(self, valid: bool, file_metadata: Dict[str, Any], 
                     df: Optional[pd.DataFrame], **kwargs) -> Dict[str, Any]:
        """Build comprehensive validation result."""
        result = {
            'valid': valid,
            'timestamp': datetime.now().isoformat(),
            'file_metadata': file_metadata,
            'errors': self.errors,
            'warnings': self.warnings,
            'info': self.info,
            'error_count': len(self.errors),
            'warning_count': len(self.warnings),
            'info_count': len(self.info)
        }
        
        # Add optional components
        result.update(kwargs)

        if 'issue_code_summary' not in result:
            result['issue_code_summary'] = self._build_issue_code_summary()
        
        return result
    
    def _load_rule_engine(self, rules_config_path: str):
        """Load business rules engine from configuration file."""
        import json
        from src.validators.rule_engine import RuleEngine
        
        try:
            with open(rules_config_path, 'r') as f:
                rules_config = json.load(f)
            
            return RuleEngine(rules_config)
        except Exception as e:
            self.warnings.append({
                'severity': 'warning',
                'category': 'business_rules',
                'message': f"Failed to load business rules: {str(e)}",
                'row': None,
                'field': None
            })
            return None
    
    def _validate_business_rules(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Execute business rules and return violations."""
        if not self.rule_engine:
            return {
                'violations': [],
                'statistics': {},
                'enabled': False
            }
        
        try:
            # Set total rows for compliance calculation
            self.rule_engine.set_total_rows(len(df))
            
            # Execute rules
            violations = self.rule_engine.validate(df)
            statistics = self.rule_engine.get_statistics()
            
            # Convert violations to dictionaries
            violations_dict = [v.to_dict() for v in violations]
            
            # Add rule violations to warnings/errors based on severity
            for violation in violations:
                if violation.severity == 'error':
                    self.errors.append({
                        'severity': 'error',
                        'category': 'business_rule',
                        'message': violation.message,
                        'row': violation.row_number,
                        'field': violation.field,
                        'rule_id': violation.rule_id,
                        'rule_name': violation.rule_name
                    })
                elif violation.severity == 'warning':
                    self.warnings.append({
                        'severity': 'warning',
                        'category': 'business_rule',
                        'message': violation.message,
                        'row': violation.row_number,
                        'field': violation.field,
                        'rule_id': violation.rule_id,
                        'rule_name': violation.rule_name
                    })
                else:  # info
                    self.info.append({
                        'severity': 'info',
                        'category': 'business_rule',
                        'message': violation.message,
                        'row': violation.row_number,
                        'field': violation.field,
                        'rule_id': violation.rule_id,
                        'rule_name': violation.rule_name
                    })
            
            return {
                'violations': violations_dict,
                'statistics': statistics,
                'enabled': True
            }
        
        except Exception as e:
            self.warnings.append({
                'severity': 'warning',
                'category': 'business_rules',
                'message': f"Error executing business rules: {str(e)}",
                'row': None,
                'field': None
            })
            return {
                'violations': [],
                'statistics': {},
                'enabled': False,
                'error': str(e)
            }
