"""Adapter to normalize chunked validation output for ValidationReporter."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, Any
import json
from collections import Counter, defaultdict


def adapt_chunked_validation_result(result: Dict[str, Any], file_path: str, mapping: str | None = None) -> Dict[str, Any]:
    """Convert chunked validator result into ValidationReporter-compatible model."""
    p = Path(file_path)
    exists = p.exists()

    if exists:
        stat = p.stat()
        size_bytes = int(stat.st_size)
        size_mb = round(stat.st_size / (1024 * 1024), 2)
        modified_time = datetime.fromtimestamp(stat.st_mtime).isoformat()
    else:
        size_bytes = 0
        size_mb = 0.0
        modified_time = 'Unknown'

    suffix = p.suffix.lower()
    if suffix == '.csv':
        file_format = 'csv'
    elif suffix in {'.txt', '.dat'}:
        file_format = 'fixedwidth'
    else:
        file_format = 'unknown'

    file_metadata = {
        'file_path': str(p),
        'file_name': p.name,
        'exists': exists,
        'size_bytes': size_bytes,
        'size_mb': size_mb,
        'format': file_format,
        'modified_time': modified_time,
    }

    total_rows = result.get('total_rows', 0)
    actual_columns = result.get('actual_columns', []) or []
    if not actual_columns and mapping:
        try:
            mp = Path(mapping)
            if mp.exists():
                cfg = json.loads(mp.read_text(encoding='utf-8'))
                actual_columns = [f.get('name') for f in cfg.get('fields', []) if isinstance(f, dict) and f.get('name')]
        except Exception:
            pass
    total_columns = len(actual_columns)
    null_cells = sum(result.get('statistics', {}).get('null_counts', {}).values())
    total_cells = total_rows * total_columns if total_rows and total_columns else 0
    completeness_pct = round(((total_cells - null_cells) / total_cells * 100), 2) if total_cells else 0
    duplicate_rows = result.get('statistics', {}).get('duplicate_count', 0)
    unique_rows = max(total_rows - duplicate_rows, 0)
    uniqueness_pct = round((unique_rows / total_rows * 100), 2) if total_rows else 0
    quality_score = round((completeness_pct * 0.6 + uniqueness_pct * 0.4), 2)

    quality_metrics = {
        'total_rows': total_rows,
        'total_columns': total_columns,
        'total_cells': total_cells,
        'null_cells': null_cells,
        'completeness_pct': completeness_pct,
        'unique_rows': unique_rows,
        'duplicate_rows': duplicate_rows,
        'uniqueness_pct': uniqueness_pct,
        'quality_score': quality_score,
    }

    empty_counts = result.get('statistics', {}).get('empty_string_counts', {})
    field_analysis = {}
    for col in actual_columns:
        empty_count = int(empty_counts.get(col, 0))
        fill_rate = round(((total_rows - empty_count) / total_rows * 100), 2) if total_rows else 0
        field_analysis[col] = {
            'inferred_type': 'string',
            'fill_rate_pct': fill_rate,
            'unique_count': 0,
        }

    date_analysis = {
        'chunked_mode_note': {
            'earliest_date': 'N/A',
            'latest_date': 'N/A',
            'date_range_days': 0,
            'invalid_date_count': 0,
            'invalid_date_pct': 0,
            'future_date_count': 0,
            'future_date_pct': 0,
            'detected_formats': ['Not computed in chunked mode']
        }
    }

    all_issues = []
    for issue in (result.get('errors', []) or []) + (result.get('warnings', []) or []):
        if isinstance(issue, dict):
            all_issues.append(issue)
        else:
            all_issues.append({'message': str(issue)})

    row_counter: Counter[int] = Counter()
    row_issue_samples: dict[int, list[str]] = defaultdict(list)
    for issue in all_issues:
        row = issue.get('row')
        if row is not None and str(row).isdigit():
            row_num = int(row)
            row_counter[row_num] += 1
            msg = str(issue.get('message', '')).strip()
            if msg and len(row_issue_samples[row_num]) < 3:
                row_issue_samples[row_num].append(msg)

    affected_rows = sorted(row_counter.keys())
    top_problematic_rows = [
        {
            'row_number': row_num,
            'issue_count': count,
            'issues': row_issue_samples.get(row_num, []),
        }
        for row_num, count in row_counter.most_common(20)
    ]

    mapping_details = {'mapping_file': mapping}
    if mapping:
        try:
            mp = Path(mapping)
            if mp.exists():
                cfg = json.loads(mp.read_text(encoding='utf-8'))
                fields = cfg.get('fields', []) if isinstance(cfg, dict) else []
                required_fields = [f.get('name') for f in fields if isinstance(f, dict) and f.get('required')]
                total_width = None
                if fields and all(isinstance(f, dict) and f.get('length') is not None for f in fields):
                    total_width = sum(int(f.get('length', 0)) for f in fields)
                mapping_details = {
                    'mapping_file': mapping,
                    'total_fields': len(fields),
                    'required_field_count': len(required_fields),
                    'required_fields': required_fields,
                    'total_width': total_width,
                }
        except Exception:
            pass

    return {
        'valid': result.get('valid', False),
        'timestamp': result.get('timestamp') or datetime.now().isoformat(),
        'file_metadata': file_metadata,
        'errors': [
            {'message': e, 'severity': 'error', 'category': 'chunked'} if isinstance(e, str) else e
            for e in result.get('errors', [])
        ],
        'warnings': [
            {'message': w, 'severity': 'warning', 'category': 'chunked'} if isinstance(w, str) else w
            for w in result.get('warnings', [])
        ],
        'info': result.get('info', []),
        'error_count': len(result.get('errors', [])),
        'warning_count': len(result.get('warnings', [])),
        'info_count': len(result.get('info', [])),
        'quality_metrics': quality_metrics,
        'duplicate_analysis': {
            'total_rows': total_rows,
            'unique_rows': unique_rows,
            'duplicate_rows': duplicate_rows,
            'duplicate_pct': round((duplicate_rows / total_rows * 100), 2) if total_rows else 0,
            'top_duplicate_counts': []
        },
        'field_analysis': field_analysis,
        'date_analysis': date_analysis,
        'data_profile': {
            'row_count': total_rows,
            'column_count': total_columns,
            'columns': actual_columns,
        },
        'appendix': {
            'validation_config': {
                'mode': 'chunked',
                'mapping_file': mapping,
                'validation_timestamp': result.get('timestamp'),
                'validator_version': '1.0.0',
                'chunk_size': result.get('statistics', {}).get('chunk_size'),
                'elapsed_seconds': result.get('statistics', {}).get('elapsed_seconds'),
                'rows_per_second': result.get('statistics', {}).get('rows_per_second')
            },
            'mapping_details': mapping_details,
            'affected_rows': {
                'total_affected_rows': len(affected_rows),
                'affected_row_pct': round((len(affected_rows) / total_rows * 100), 2) if total_rows else 0,
                'top_problematic_rows': top_problematic_rows,
            }
        },
        'business_rules': result.get('business_rules', {
            'enabled': False,
            'violations': [],
            'statistics': {},
            'error': 'Not executed in chunked mode'
        })
    }
