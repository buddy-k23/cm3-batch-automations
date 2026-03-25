"""Tests for chunked validation result adapter."""

from pathlib import Path

from src.reports.adapters.result_adapter_chunked import adapt_chunked_validation_result


def test_adapt_chunked_validation_result_includes_required_sections():
    chunked_result = {
        'valid': True,
        'timestamp': '2026-02-18T23:00:00',
        'total_rows': 10,
        'actual_columns': ['a', 'b'],
        'errors': [],
        'warnings': [],
        'info': [],
        'statistics': {
            'null_counts': {'a': 1, 'b': 0},
            'empty_string_counts': {'a': 2, 'b': 1},
            'duplicate_count': 1,
            'chunk_size': 500,
            'elapsed_seconds': 0.25,
            'rows_per_second': 40.0,
        }
    }

    sample_file = Path('reports') / 'tmp_adapter_sample.txt'
    sample_file.parent.mkdir(parents=True, exist_ok=True)
    sample_file.write_text('abc\n', encoding='utf-8')

    model = adapt_chunked_validation_result(chunked_result, file_path=str(sample_file), mapping='map.json')

    assert 'quality_metrics' in model
    assert 'field_analysis' in model
    assert 'date_analysis' in model
    assert 'business_rules' in model
    assert model['appendix']['validation_config']['mode'] == 'chunked'
    assert model['appendix']['validation_config']['chunk_size'] == 500
    assert model['appendix']['validation_config']['rows_per_second'] == 40.0

    metadata = model['file_metadata']
    assert metadata['file_name'] == 'tmp_adapter_sample.txt'
    assert metadata['format'] == 'fixedwidth'
    assert metadata['size_bytes'] > 0
    assert metadata['size_mb'] >= 0
    assert metadata['modified_time'] != 'Unknown'


def test_adapt_chunked_validation_result_populates_affected_rows_summary():
    chunked_result = {
        'valid': False,
        'timestamp': '2026-02-18T23:00:00',
        'total_rows': 10,
        'actual_columns': ['a'],
        'errors': [
            {'message': 'bad row', 'row': 3, 'severity': 'error'},
            {'message': 'another bad row', 'row': 5, 'severity': 'error'},
        ],
        'warnings': [
            {'message': 'warn row', 'row': 3, 'severity': 'warning'}
        ],
        'info': [],
        'statistics': {'null_counts': {}, 'empty_string_counts': {}, 'duplicate_count': 0},
    }

    model = adapt_chunked_validation_result(chunked_result, file_path='missing.txt', mapping=None)
    summary = model['appendix']['affected_rows']
    assert summary['total_affected_rows'] == 2
    assert summary['affected_row_pct'] == 20.0
    assert summary['top_problematic_rows'][0]['row_number'] == 3
    assert summary['top_problematic_rows'][0]['issue_count'] == 2
    assert summary['top_problematic_rows'][1]['row_number'] == 5
