"""Chunked file validator for memory-efficient validation."""

import json
import time
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
import pandas as pd
from typing import Dict, Any, List, Optional
from .chunked_parser import ChunkedFileParser
from ..utils.progress import ProgressTracker
from ..utils.memory_monitor import MemoryMonitor
from ..utils.logger import get_logger
from ..validators.rule_engine import RuleEngine


def _is_integer(value: str) -> bool:
    """Return True if *value* represents a valid integer."""
    try:
        int(value)
        return True
    except (ValueError, TypeError):
        return False


def _is_float(value: str) -> bool:
    """Return True if *value* represents a valid float/decimal number."""
    try:
        float(value)
        return True
    except (ValueError, TypeError):
        return False


def _is_value_valid_for_format(value: str, fmt: str) -> bool:
    """Check whether *value* matches the COBOL-style format specifier *fmt*.

    Supported format codes include:
    - ``XXX`` — exactly three alphabetic characters.
    - ``CCYYMMDD`` / ``YYYYMMDD`` — eight-digit date string.
    - ``S9(n)`` — signed numeric with *n* digits.
    - ``9(n)`` — unsigned numeric with *n* digits.
    - ``[+S]9(n)V9(m)`` — signed/unsigned fixed-decimal with *n* integer and
      *m* fractional digits.
    Unrecognised format codes always return True (no constraint applied).

    Args:
        value: The string value extracted from the data file.
        fmt: COBOL-style format specifier (case-insensitive).

    Returns:
        True if the value conforms to *fmt*, or if *fmt* is unrecognised.
    """
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
    return True


def _validate_chunk_worker(
    chunk: pd.DataFrame,
    chunk_num: int,
    chunk_size: int,
    strict_fixed_width: bool,
    strict_fields: list[dict],
    strict_level: str,
) -> dict:
    """Validate a single DataFrame chunk and return an aggregated result dict.

    Designed to run in a subprocess via :class:`concurrent.futures.ProcessPoolExecutor`.
    Collects null counts, empty-string counts, duplicate row flags, and
    (optionally) strict field-level format/type violations.

    Args:
        chunk: The DataFrame slice to validate.
        chunk_num: 1-based chunk index, used to compute absolute row numbers.
        chunk_size: Configured rows-per-chunk (used to derive global row offset).
        strict_fixed_width: When True, enable format-code validation for
            fields that declare a ``format`` attribute.
        strict_fields: Field definitions from the mapping config; each dict
            may contain ``name``, ``data_type``, ``required``, and ``format``.
        strict_level: Strictness tier — ``'format'`` only checks format codes;
            ``'all'`` also checks data type compatibility.

    Returns:
        Dict with keys:
        - ``errors``: list of error message strings.
        - ``warnings``: list of warning message strings.
        - ``stats``: dict with ``duplicates`` count, ``nulls`` per column, and
          ``empty_strings`` per column.
        - ``rows``: number of rows processed in this chunk.
    """
    errors = []
    warnings = []
    stats = {'duplicates': 0, 'nulls': {}, 'empty_strings': {}}

    if chunk.empty:
        warnings.append(f"Chunk {chunk_num} is empty")
        return {'errors': errors, 'warnings': warnings, 'stats': stats, 'rows': 0}

    null_counts = chunk.isnull().sum()
    for col, count in null_counts.items():
        if count > 0:
            stats['nulls'][col] = int(count)

    for col in chunk.columns:
        if chunk[col].dtype == 'object':
            series = chunk[col]
            empty_mask = series.notna() & (series.astype(str).str.strip() == '')
            empty_count = int(empty_mask.sum())
            if empty_count > 0:
                stats['empty_strings'][col] = empty_count

    if strict_fields:
        row_base = (chunk_num - 1) * chunk_size
        for local_idx, row in chunk.reset_index(drop=True).iterrows():
            row_num = row_base + local_idx + 1
            # --- data-type checks ---
            for field in strict_fields:
                name = field.get('name')
                dtype = str(field.get('data_type') or '').lower()
                if not dtype or dtype == 'string' or name not in chunk.columns:
                    continue
                value = '' if pd.isna(row.get(name)) else str(row.get(name)).strip()
                if not value:
                    continue
                if dtype in {'integer', 'int'} and not _is_integer(value):
                    errors.append({
                        'severity': 'error',
                        'category': 'data_type',
                        'code': 'DT_INT_001',
                        'message': f"Field '{name}' expects integer but got '{value}'",
                        'row': row_num,
                        'field': name,
                    })
                elif dtype in {'float', 'decimal', 'number'} and not _is_float(value):
                    errors.append({
                        'severity': 'error',
                        'category': 'data_type',
                        'code': 'DT_FLT_001',
                        'message': f"Field '{name}' expects float but got '{value}'",
                        'row': row_num,
                        'field': name,
                    })
            # --- strict fixed-width checks ---
            if strict_fixed_width and strict_level in {'format', 'all'}:
                for field in strict_fields:
                    name = field.get('name')
                    if name not in chunk.columns:
                        continue
                    value = '' if pd.isna(row.get(name)) else str(row.get(name)).strip()

                    if field.get('required') and value == '':
                        errors.append({
                            'severity': 'error',
                            'category': 'strict_fixed_width',
                            'code': 'FW_REQ_001',
                            'message': f"Required field '{name}' is empty",
                            'row': row_num,
                            'field': name,
                        })
                        continue

                    if value and field.get('valid_values'):
                        allowed = {str(v).strip() for v in (field.get('valid_values') or [])}
                        if value not in allowed:
                            errors.append({
                                'severity': 'error',
                                'category': 'strict_fixed_width',
                                'code': 'FW_VAL_001',
                                'message': f"Field '{name}' has invalid value '{value}'",
                                'row': row_num,
                                'field': name,
                            })
                            continue

                    fmt = str(field.get('format') or '').upper()
                    if value and fmt and not _is_value_valid_for_format(value, fmt):
                        errors.append({
                            'severity': 'error',
                            'category': 'strict_fixed_width',
                            'code': 'FW_FMT_001',
                            'message': f"Field '{name}' has invalid format for value '{value}'",
                            'row': row_num,
                            'field': name,
                        })

    return {'errors': errors, 'warnings': warnings, 'stats': stats, 'rows': len(chunk)}


class ChunkedFileValidator:
    """Validate large files in chunks."""
    
    def __init__(self, file_path: str, delimiter: str = '|',
                 chunk_size: int = 100000, parser: Optional[ChunkedFileParser] = None,
                 rules_config_path: Optional[str] = None,
                 expected_row_length: Optional[int] = None,
                 strict_fixed_width: bool = False,
                 strict_level: str = 'format',
                 strict_fields: Optional[List[Dict[str, Any]]] = None,
                 workers: int = 1,
                 has_header: bool = True):
        """Initialize chunked validator.

        Args:
            file_path: Path to file to validate
            delimiter: Field delimiter
            chunk_size: Rows per chunk
            parser: Optional preconfigured chunked parser (e.g., fixed-width parser)
            rules_config_path: Optional path to business rules JSON config.
            expected_row_length: Expected number of fields per row for length checks.
            strict_fixed_width: Enable strict field-level validation.
            strict_level: Strictness level ('format' or 'all').
            strict_fields: Field definitions for strict validation.
            workers: Number of parallel worker processes (1 = sequential).
            has_header: Whether the data file contains a header row. Forwarded
                to the internal ChunkedFileParser when no external parser is
                supplied.
        """
        self.file_path = file_path
        self.delimiter = delimiter
        self.chunk_size = chunk_size
        self.has_header = has_header
        self.parser = parser
        self.logger = get_logger(__name__)
        self.memory_monitor = MemoryMonitor()
        self.rule_engine: Optional[RuleEngine] = None
        self._total_rows_for_rules = 0
        self.expected_row_length = expected_row_length
        self.strict_fixed_width = strict_fixed_width
        self.strict_level = (strict_level or 'format').lower()
        self.strict_fields = strict_fields or []
        self.workers = max(int(workers or 1), 1)

        if rules_config_path:
            try:
                with open(rules_config_path, 'r') as f:
                    rules_cfg = json.load(f)
                self.rule_engine = RuleEngine(rules_cfg)
            except Exception as e:
                self.logger.warning(f"Failed to load business rules: {e}")
    
    def validate(self, show_progress: bool = True) -> Dict[str, Any]:
        """Validate file in chunks.
        
        Args:
            show_progress: Show progress bar
            
        Returns:
            Validation results dictionary
        """
        self.logger.info(f"Starting chunked validation: {self.file_path}")
        self.memory_monitor.log_memory_usage("validation start")
        start_ts = time.time()

        errors = []
        warnings = []
        info = []
        
        # Validate structure first
        parser = self.parser or ChunkedFileParser(
            self.file_path, self.delimiter, self.chunk_size,
            has_header=self.has_header,
        )
        structure_result = parser.validate_structure()
        
        if not structure_result['valid']:
            return structure_result
        
        # Add structure warnings
        warnings.extend(structure_result.get('warnings', []))

        # Fixed-width row-length validation (captures row-level defects)
        if self.expected_row_length:
            mismatch_count, length_issues, scanned_rows = self._scan_fixed_width_row_lengths()
            errors.extend(length_issues)
            if mismatch_count > len(length_issues):
                warnings.append(
                    f"Found {mismatch_count:,} row-length mismatches; showing first {len(length_issues):,} details"
                )

        # Initialize statistics
        total_rows = 0
        total_nulls = {}
        total_empty_strings = {}
        duplicate_count = 0
        seen_rows = set()  # For duplicate detection (memory-limited)
        max_seen_rows = 100000  # Limit duplicate tracking

        business_violations: list[dict] = []
        
        # Parse and validate chunks
        progress = ProgressTracker(parser.count_rows(), "Validating") if show_progress else None
        
        try:
            parallel_enabled = self.workers > 1 and self.rule_engine is None
            if self.workers > 1 and self.rule_engine is not None:
                warnings.append("Parallel mode disabled because business rules are enabled; falling back to sequential validation")

            if parallel_enabled:
                max_in_flight = max(self.workers * 2, 2)
                pending: dict[Any, int] = {}

                with ProcessPoolExecutor(max_workers=self.workers) as pool:
                    for chunk_num, chunk in enumerate(parser.parse_chunks(), 1):
                        # Keep duplicate detection active in parallel mode on the coordinator thread
                        # (memory-limited to max_seen_rows, same semantics as sequential mode).
                        if len(seen_rows) < max_seen_rows:
                            for _, row in chunk.iterrows():
                                row_hash = hash(tuple(row.values))
                                if row_hash in seen_rows:
                                    duplicate_count += 1
                                else:
                                    seen_rows.add(row_hash)

                        fut = pool.submit(
                            _validate_chunk_worker,
                            chunk,
                            chunk_num,
                            self.chunk_size,
                            bool(self.strict_fixed_width),
                            list(self.strict_fields),
                            str(self.strict_level or 'format'),
                        )
                        pending[fut] = chunk_num

                        total_rows += len(chunk)
                        if progress:
                            progress.update(total_rows)

                        while len(pending) >= max_in_flight:
                            done, _ = wait(set(pending.keys()), return_when=FIRST_COMPLETED)
                            for d in done:
                                out = d.result()
                                pending.pop(d, None)
                                errors.extend(out.get('errors', []))
                                warnings.extend(out.get('warnings', []))
                                chunk_stats = out.get('stats', {})
                                for col, count in chunk_stats.get('nulls', {}).items():
                                    total_nulls[col] = total_nulls.get(col, 0) + count
                                for col, count in chunk_stats.get('empty_strings', {}).items():
                                    total_empty_strings[col] = total_empty_strings.get(col, 0) + count

                    while pending:
                        done, _ = wait(set(pending.keys()), return_when=FIRST_COMPLETED)
                        for d in done:
                            out = d.result()
                            pending.pop(d, None)
                            errors.extend(out.get('errors', []))
                            warnings.extend(out.get('warnings', []))
                            chunk_stats = out.get('stats', {})
                            for col, count in chunk_stats.get('nulls', {}).items():
                                total_nulls[col] = total_nulls.get(col, 0) + count
                            for col, count in chunk_stats.get('empty_strings', {}).items():
                                total_empty_strings[col] = total_empty_strings.get(col, 0) + count
            else:
                # Cross-row rules require map-reduce across all chunks to detect
                # violations that straddle chunk boundaries (issue #358).
                from src.validators.cross_row_validator import CrossRowValidator as _CRV
                _cross_row_validator = _CRV()
                # rule_id → {'rule': rule_dict, 'states': [partial_state, ...]}
                _cross_row_partial_states: dict = {}

                # When the file has no header row, supply field names from the
                # mapping so that named-field lookups in _validate_chunk work.
                col_names: Optional[List[str]] = None
                if not self.has_header and self.strict_fields:
                    col_names = [
                        f['name'] for f in self.strict_fields
                        if isinstance(f, dict) and f.get('name')
                    ] or None
                for chunk_num, chunk in enumerate(parser.parse_chunks(columns=col_names), 1):
                    # Validate chunk
                    chunk_errors, chunk_warnings, chunk_stats = self._validate_chunk(
                        chunk, chunk_num, seen_rows, max_seen_rows
                    )

                    errors.extend(chunk_errors)
                    warnings.extend(chunk_warnings)

                    # Update statistics
                    total_rows += len(chunk)
                    duplicate_count += chunk_stats['duplicates']

                    # Optional business-rule validation (map-reduce for cross-row rules).
                    if self.rule_engine is not None:
                        self.rule_engine.set_total_rows(total_rows)

                        # Field / cross-field rules: evaluate per chunk immediately.
                        chunk_violations = self.rule_engine.validate_non_cross_row(chunk)
                        for v in chunk_violations:
                            vdict = v.to_dict()
                            business_violations.append(vdict)
                            issue = {
                                'severity': v.severity,
                                'category': 'business_rule',
                                'message': v.message,
                                'row': v.row_number,
                                'field': v.field,
                                'rule_id': v.rule_id,
                                'rule_name': v.rule_name,
                            }
                            if v.severity == 'error':
                                errors.append(issue)
                            elif v.severity == 'warning':
                                warnings.append(issue)
                            else:
                                info.append(issue)

                        # Cross-row rules: collect partial state per chunk (map step).
                        for rule in self.rule_engine.cross_row_rules:
                            scoped_chunk = self.rule_engine._apply_condition(rule, chunk)
                            partial = _cross_row_validator.collect_partial_state(
                                rule, scoped_chunk
                            )
                            entry = _cross_row_partial_states.setdefault(
                                rule['id'], {'rule': rule, 'states': []}
                            )
                            entry['states'].append(partial)

                    # Aggregate null counts
                    for col, count in chunk_stats['nulls'].items():
                        total_nulls[col] = total_nulls.get(col, 0) + count

                    # Aggregate empty string counts
                    for col, count in chunk_stats['empty_strings'].items():
                        total_empty_strings[col] = total_empty_strings.get(col, 0) + count

                    if progress:
                        progress.update(total_rows)

                    # Periodic garbage collection
                    if chunk_num % 10 == 0:
                        self.memory_monitor.force_garbage_collection()

                # Cross-row rules: merge partial states and evaluate (reduce step).
                if self.rule_engine is not None:
                    for rule_id, data in _cross_row_partial_states.items():
                        rule = data['rule']
                        merged = _cross_row_validator.merge_partial_states(
                            rule, data['states']
                        )
                        cr_violations = _cross_row_validator.evaluate_merged_state(
                            rule, merged
                        )
                        for v in cr_violations:
                            vdict = v.to_dict()
                            business_violations.append(vdict)
                            issue = {
                                'severity': v.severity,
                                'category': 'business_rule',
                                'message': v.message,
                                'row': v.row_number,
                                'field': v.field,
                                'rule_id': v.rule_id,
                                'rule_name': v.rule_name,
                            }
                            if v.severity == 'error':
                                errors.append(issue)
                            elif v.severity == 'warning':
                                warnings.append(issue)
                            else:
                                info.append(issue)

            if progress:
                progress.finish()
            
            required_fields = {
                f.get('name') for f in (self.strict_fields or [])
                if isinstance(f, dict) and f.get('name') and f.get('required')
            }

            # Fail-safe: ensure required empty/null fields are represented as errors.
            if self.strict_fixed_width and self.strict_level in {'format', 'all'}:
                for field_name in sorted(required_fields):
                    miss_count = int(total_nulls.get(field_name, 0)) + int(total_empty_strings.get(field_name, 0))
                    if miss_count > 0:
                        errors.append({
                            'severity': 'error',
                            'category': 'strict_fixed_width',
                            'code': 'FW_REQ_001',
                            'message': f"Required field '{field_name}' has {miss_count:,} empty/null values",
                            'row': None,
                            'field': field_name,
                        })

            # Generate warnings for nulls and empty strings (exclude required fields in strict mode,
            # because they are already promoted to errors above).
            for col, count in total_nulls.items():
                if count > 0 and not (self.strict_fixed_width and col in required_fields):
                    pct = (count / total_rows) * 100
                    warnings.append(
                        f"Column '{col}' has {count:,} null values ({pct:.1f}%)"
                    )

            for col, count in total_empty_strings.items():
                if count > 0 and not (self.strict_fixed_width and col in required_fields):
                    pct = (count / total_rows) * 100
                    warnings.append(
                        f"Column '{col}' has {count:,} empty strings ({pct:.1f}%)"
                    )
            
            if duplicate_count > 0:
                warnings.append(
                    f"Found {duplicate_count:,} duplicate rows "
                    f"(limited to first {max_seen_rows:,} rows checked)"
                )
            
            self.logger.info(
                f"Validation complete: {total_rows:,} rows validated, "
                f"{len(errors)} errors, {len(warnings)} warnings"
            )
            self.memory_monitor.log_memory_usage("validation complete")
            
            elapsed = max(time.time() - start_ts, 0.0)
            rows_per_second = (total_rows / elapsed) if elapsed > 0 else 0.0

            business_stats = {
                'total_rules': len(self.rule_engine.rules) if self.rule_engine else 0,
                'enabled_rules': len(self.rule_engine.enabled_rules) if self.rule_engine else 0,
                'total_violations': len(business_violations),
            }

            return {
                'valid': len(errors) == 0,
                'errors': errors,
                'warnings': warnings,
                'info': info,
                'file_path': self.file_path,
                'total_rows': total_rows,
                'statistics': {
                    'null_counts': total_nulls,
                    'empty_string_counts': total_empty_strings,
                    'duplicate_count': duplicate_count,
                    'duplicate_check_limited': (False if parallel_enabled else len(seen_rows) >= max_seen_rows),
                    'parallel': parallel_enabled,
                    'workers': self.workers,
                    'elapsed_seconds': round(elapsed, 6),
                    'rows_per_second': round(rows_per_second, 2),
                    'chunk_size': self.chunk_size,
                },
                'business_rules': {
                    'enabled': self.rule_engine is not None,
                    'violations': business_violations,
                    'statistics': business_stats,
                }
            }
            
        except Exception as e:
            self.logger.error(f"Validation error: {e}")
            return {
                'valid': False,
                'errors': [f"Validation failed: {str(e)}"],
                'warnings': warnings,
                'file_path': self.file_path
            }
    
    def _scan_fixed_width_row_lengths(self, max_issue_details: int = 200) -> tuple[int, list[dict], int]:
        """Scan file line lengths and return mismatch diagnostics.

        Returns:
            (mismatch_count, sampled_issue_dicts, total_rows_scanned)
        """
        if not self.expected_row_length:
            return 0, [], 0

        mismatch_count = 0
        sampled: list[dict] = []
        total_rows = 0

        with open(self.file_path, 'r', encoding='utf-8', errors='replace') as fh:
            for row_num, line in enumerate(fh, start=1):
                total_rows = row_num
                actual_len = len(line.rstrip('\r\n'))
                if actual_len != self.expected_row_length:
                    mismatch_count += 1
                    if len(sampled) < max_issue_details:
                        sampled.append({
                            'severity': 'error',
                            'category': 'format',
                            'code': 'FW_LEN_001',
                            'message': (
                                f"Row {row_num} length mismatch: expected {self.expected_row_length}, got {actual_len}"
                            ),
                            'row': row_num,
                            'field': None,
                        })

        return mismatch_count, sampled, total_rows

    def _is_value_valid_for_format(self, value: str, fmt: str) -> bool:
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
        return True

    def _validate_chunk(self, chunk: pd.DataFrame, chunk_num: int,
                       seen_rows: set, max_seen_rows: int) -> tuple:
        """Validate a single chunk.
        
        Args:
            chunk: DataFrame chunk
            chunk_num: Chunk number
            seen_rows: Set of seen row hashes for duplicate detection
            max_seen_rows: Maximum rows to track for duplicates
            
        Returns:
            Tuple of (errors, warnings, statistics)
        """
        errors = []
        warnings = []
        stats = {
            'duplicates': 0,
            'nulls': {},
            'empty_strings': {}
        }
        
        # Check for empty chunk
        if chunk.empty:
            warnings.append(f"Chunk {chunk_num} is empty")
            return errors, warnings, stats
        
        # Check for duplicate rows (memory-limited)
        if len(seen_rows) < max_seen_rows:
            for idx, row in chunk.iterrows():
                row_hash = hash(tuple(row.values))
                if row_hash in seen_rows:
                    stats['duplicates'] += 1
                else:
                    seen_rows.add(row_hash)
        
        # Check for null values
        null_counts = chunk.isnull().sum()
        for col, count in null_counts.items():
            if count > 0:
                stats['nulls'][col] = int(count)
        
        # Check for empty/whitespace strings
        for col in chunk.columns:
            if chunk[col].dtype == 'object':
                series = chunk[col]
                empty_mask = series.notna() & (series.astype(str).str.strip() == '')
                empty_count = int(empty_mask.sum())
                if empty_count > 0:
                    stats['empty_strings'][col] = empty_count

        # Single-pass row loop: data-type checks and strict fixed-width checks combined.
        if self.strict_fields:
            row_base = (chunk_num - 1) * self.chunk_size
            for local_idx, row in chunk.reset_index(drop=True).iterrows():
                row_num = row_base + local_idx + 1
                # --- data-type checks ---
                for field in self.strict_fields:
                    name = field.get('name')
                    dtype = str(field.get('data_type') or '').lower()
                    if not dtype or dtype == 'string' or name not in chunk.columns:
                        continue
                    value = '' if pd.isna(row.get(name)) else str(row.get(name)).strip()
                    if not value:
                        continue
                    if dtype in {'integer', 'int'} and not _is_integer(value):
                        errors.append({
                            'severity': 'error',
                            'category': 'data_type',
                            'code': 'DT_INT_001',
                            'message': (
                                f"Field '{name}' expects integer but got '{value}'"
                            ),
                            'row': row_num,
                            'field': name,
                        })
                    elif dtype in {'float', 'decimal', 'number'} and not _is_float(value):
                        errors.append({
                            'severity': 'error',
                            'category': 'data_type',
                            'code': 'DT_FLT_001',
                            'message': (
                                f"Field '{name}' expects float but got '{value}'"
                            ),
                            'row': row_num,
                            'field': name,
                        })
                # --- strict fixed-width checks ---
                if self.strict_fixed_width and self.strict_level in {'format', 'all'}:
                    for field in self.strict_fields:
                        name = field.get('name')
                        if name not in chunk.columns:
                            continue
                        value = '' if pd.isna(row.get(name)) else str(row.get(name)).strip()

                        if field.get('required') and value == '':
                            errors.append({
                                'severity': 'error',
                                'category': 'strict_fixed_width',
                                'code': 'FW_REQ_001',
                                'message': f"Required field '{name}' is empty",
                                'row': row_num,
                                'field': name,
                            })
                            continue

                        if value and field.get('valid_values'):
                            allowed = {str(v).strip() for v in (field.get('valid_values') or [])}
                            if value not in allowed:
                                errors.append({
                                    'severity': 'error',
                                    'category': 'strict_fixed_width',
                                    'code': 'FW_VAL_001',
                                    'message': f"Field '{name}' has invalid value '{value}'",
                                    'row': row_num,
                                    'field': name,
                                })
                                continue

                        fmt = str(field.get('format') or '').upper()
                        if value and fmt and not self._is_value_valid_for_format(value, fmt):
                            errors.append({
                                'severity': 'error',
                                'category': 'strict_fixed_width',
                                'code': 'FW_FMT_001',
                                'message': f"Field '{name}' has invalid format for value '{value}'",
                                'row': row_num,
                                'field': name,
                            })

        return errors, warnings, stats
    
    def validate_with_schema(self, expected_columns: List[str],
                            required_columns: Optional[List[str]] = None,
                            show_progress: bool = True) -> Dict[str, Any]:
        """Validate file against expected schema.
        
        Args:
            expected_columns: List of expected column names
            required_columns: List of required column names
            show_progress: Show progress bar
            
        Returns:
            Validation results dictionary
        """
        required_columns = required_columns or expected_columns
        
        # First validate basic structure
        basic_result = self.validate(show_progress=show_progress)

        errors = list(basic_result.get('errors', []))
        warnings = list(basic_result.get('warnings', []))
        
        # Get actual columns from first chunk
        parser = self.parser or ChunkedFileParser(
            self.file_path, self.delimiter, self.chunk_size,
            has_header=self.has_header,
        )
        first_chunk = parser.parse_sample(n_rows=10)
        actual_columns = set(first_chunk.columns)
        
        # Check required columns
        expected_set = set(expected_columns)
        required_set = set(required_columns)
        
        missing_required = required_set - actual_columns
        if missing_required:
            errors.append(f"Missing required columns: {sorted(missing_required)}")
        
        # Check for unexpected columns
        unexpected = actual_columns - expected_set
        if unexpected:
            warnings.append(f"Unexpected columns: {sorted(unexpected)}")
        
        # Check for missing optional columns
        missing_optional = expected_set - required_set - actual_columns
        if missing_optional:
            warnings.append(f"Missing optional columns: {sorted(missing_optional)}")
        
        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
            'info': basic_result.get('info', []),
            'file_path': self.file_path,
            'total_rows': basic_result.get('total_rows', 0),
            'expected_columns': expected_columns,
            'actual_columns': list(actual_columns),
            'missing_required': list(missing_required),
            'unexpected': list(unexpected),
            'statistics': basic_result.get('statistics', {}),
            'business_rules': basic_result.get('business_rules', {'enabled': False, 'violations': [], 'statistics': {}}),
        }
