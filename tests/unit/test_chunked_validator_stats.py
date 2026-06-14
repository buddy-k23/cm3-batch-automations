"""Tests for chunked validator performance statistics."""

import os
import tempfile

from src.parsers.chunked_validator import ChunkedFileValidator


def test_chunked_validator_returns_processing_stats():
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write('a|b|c\n')
        f.write('d|e|f\n')
        f.write('g|h|i\n')
        temp_file = f.name

    try:
        validator = ChunkedFileValidator(file_path=temp_file, delimiter='|', chunk_size=2)
        result = validator.validate(show_progress=False)

        stats = result.get('statistics', {})
        assert 'elapsed_seconds' in stats
        assert 'rows_per_second' in stats
        assert stats.get('chunk_size') == 2
        assert stats.get('elapsed_seconds', 0) >= 0
    finally:
        os.unlink(temp_file)


def test_chunked_validator_detects_fixed_width_length_mismatches():
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write('12345\n')
        f.write('1234\n')  # short row
        temp_file = f.name

    try:
        validator = ChunkedFileValidator(
            file_path=temp_file,
            delimiter='|',
            chunk_size=2,
            expected_row_length=5,
        )
        result = validator.validate(show_progress=False)

        assert result['valid'] is False
        assert any(
            isinstance(e, dict) and e.get('code') == 'FW_LEN_001' and e.get('row') == 2
            for e in result.get('errors', [])
        )
    finally:
        os.unlink(temp_file)


def test_chunked_strict_fixed_width_detects_required_empty_as_error():
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write(' A\n')
        temp_file = f.name

    try:
        from src.parsers.chunked_parser import ChunkedFixedWidthParser

        parser = ChunkedFixedWidthParser(temp_file, [('LOCATION-CODE', 0, 1), ('X', 1, 2)], chunk_size=10)
        validator = ChunkedFileValidator(
            file_path=temp_file,
            parser=parser,
            chunk_size=10,
            strict_fixed_width=True,
            strict_level='format',
            strict_fields=[{'name': 'LOCATION-CODE', 'required': True}],
        )

        result = validator.validate(show_progress=False)
        assert result['valid'] is False
        assert any(
            isinstance(e, dict) and e.get('code') == 'FW_REQ_001' and e.get('field') == 'LOCATION-CODE'
            for e in result.get('errors', [])
        )
    finally:
        os.unlink(temp_file)


def test_chunked_strict_fixed_width_detects_field_format_errors():
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write('A\n')
        temp_file = f.name

    try:
        from src.parsers.chunked_parser import ChunkedFixedWidthParser

        parser = ChunkedFixedWidthParser(temp_file, [('NUM', 0, 1)], chunk_size=10)
        validator = ChunkedFileValidator(
            file_path=temp_file,
            parser=parser,
            chunk_size=10,
            strict_fixed_width=True,
            strict_level='format',
            strict_fields=[{'name': 'NUM', 'required': True, 'format': '9(1)'}],
        )

        result = validator.validate(show_progress=False)
        assert result['valid'] is False
        assert any(
            isinstance(e, dict) and e.get('code') == 'FW_FMT_001' and e.get('field') == 'NUM'
            for e in result.get('errors', [])
        )
    finally:
        os.unlink(temp_file)


def test_chunked_validator_parallel_mode_reports_workers_and_keeps_duplicate_detection():
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write('c1|c2\n')
        f.write('1|A\n')
        f.write('1|A\n')
        f.write('3|C\n')
        temp_file = f.name

    try:
        validator = ChunkedFileValidator(file_path=temp_file, delimiter='|', chunk_size=1, workers=2)
        result = validator.validate(show_progress=False)

        stats = result.get('statistics', {})
        assert stats.get('parallel') is True
        assert stats.get('workers') == 2
        assert stats.get('duplicate_count', 0) >= 1
        assert not any('Duplicate row detection is disabled in parallel mode' in str(w) for w in result.get('warnings', []))
    finally:
        os.unlink(temp_file)


def test_validate_with_schema_passes_show_progress_flag(monkeypatch):
    validator = ChunkedFileValidator(file_path='dummy.txt', delimiter='|', chunk_size=2)

    called = {'show_progress': None}

    def fake_validate(show_progress=True):
        called['show_progress'] = show_progress
        return {
            'valid': True,
            'errors': [],
            'warnings': [],
            'info': [],
            'file_path': 'dummy.txt',
            'total_rows': 0,
            'statistics': {},
            'business_rules': {'enabled': False, 'violations': [], 'statistics': {}},
        }

    class _DummyParser:
        def parse_sample(self, n_rows=10):
            import pandas as pd
            return pd.DataFrame(columns=['a'])

    monkeypatch.setattr(validator, 'validate', fake_validate)
    monkeypatch.setattr(validator, 'parser', _DummyParser())

    validator.validate_with_schema(expected_columns=['a'], required_columns=['a'], show_progress=True)
    assert called['show_progress'] is True
