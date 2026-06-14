"""Unit tests for strict fixed-width validation modes."""

import os
import tempfile

from src.parsers.fixed_width_parser import FixedWidthParser
from src.parsers.enhanced_validator import EnhancedFileValidator


def _make_mapping():
    return {
        'file_path': 'dummy_mapping.json',
        'fields': [
            {'name': 'ACCOUNT', 'position': 1, 'length': 4, 'required': True, 'format': '9(4)'},
            {'name': 'STATUS', 'position': 5, 'length': 1, 'required': False, 'valid_values': ['A', 'B']},
        ],
        'total_record_length': 5,
    }


def _make_pic_mapping():
    return {
        'file_path': 'dummy_pic_mapping.json',
        'fields': [
            {'name': 'SIGNED_NUM', 'position': 1, 'length': 6, 'required': True, 'format': 'S9(5)'},
            {'name': 'CHAR3', 'position': 7, 'length': 3, 'required': True, 'format': 'XXX'},
        ],
        'total_record_length': 9,
    }


def test_strict_basic_only_checks_length_and_required():
    # Row 2 has STATUS='Z' (invalid valid_values), but basic mode should ignore format/value checks.
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write('1234A\n')
        f.write('5678Z\n')
        temp_file = f.name

    try:
        parser = FixedWidthParser(temp_file, [('ACCOUNT', 0, 4), ('STATUS', 4, 5)])
        validator = EnhancedFileValidator(parser, _make_mapping())
        result = validator.validate(detailed=False, strict_fixed_width=True, strict_level='basic')

        assert result['valid'] is True
        assert result['strict_fixed_width']['enabled'] is True
        assert result['strict_fixed_width']['strict_level'] == 'basic'
        assert result['strict_fixed_width']['format_errors'] == 0
    finally:
        os.unlink(temp_file)


def test_strict_result_contains_invalid_row_numbers():
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write('1234A\n')
        f.write('5678Z\n')
        temp_file = f.name

    try:
        parser = FixedWidthParser(temp_file, [('ACCOUNT', 0, 4), ('STATUS', 4, 5)])
        validator = EnhancedFileValidator(parser, _make_mapping())
        result = validator.validate(detailed=False, strict_fixed_width=True, strict_level='format')

        strict = result['strict_fixed_width']
        assert strict['invalid_records'] == 1
        assert strict['invalid_row_numbers'] == [2]
    finally:
        os.unlink(temp_file)


def test_issue_code_summary_counts_strict_errors():
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write('1234A\n')
        f.write('5678Z\n')
        temp_file = f.name

    try:
        parser = FixedWidthParser(temp_file, [('ACCOUNT', 0, 4), ('STATUS', 4, 5)])
        validator = EnhancedFileValidator(parser, _make_mapping())
        result = validator.validate(detailed=False, strict_fixed_width=True, strict_level='format')

        summary = result.get('issue_code_summary', {})
        assert summary.get('FW_VAL_001', 0) == 1
    finally:
        os.unlink(temp_file)


def test_strict_format_checks_valid_values_for_non_empty_optional_fields():
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write('1234A\n')
        f.write('5678Z\n')
        temp_file = f.name

    try:
        parser = FixedWidthParser(temp_file, [('ACCOUNT', 0, 4), ('STATUS', 4, 5)])
        validator = EnhancedFileValidator(parser, _make_mapping())
        result = validator.validate(detailed=False, strict_fixed_width=True, strict_level='format')

        assert result['valid'] is False
        errs = result['errors']
        assert any(e.get('code') == 'FW_VAL_001' for e in errs)
    finally:
        os.unlink(temp_file)


def test_strict_format_allows_empty_optional_fields():
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write('1234 \n')
        temp_file = f.name

    try:
        parser = FixedWidthParser(temp_file, [('ACCOUNT', 0, 4), ('STATUS', 4, 5)])
        validator = EnhancedFileValidator(parser, _make_mapping())
        result = validator.validate(detailed=False, strict_fixed_width=True, strict_level='format')

        assert result['valid'] is True
    finally:
        os.unlink(temp_file)


def test_pic_formats_s9_and_xxx_supported():
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write('+12345ABC\n')
        temp_file = f.name

    try:
        parser = FixedWidthParser(temp_file, [('SIGNED_NUM', 0, 6), ('CHAR3', 6, 9)])
        validator = EnhancedFileValidator(parser, _make_pic_mapping())
        result = validator.validate(detailed=False, strict_fixed_width=True, strict_level='format')

        assert result['valid'] is True
    finally:
        os.unlink(temp_file)


def test_non_chunked_validate_detects_row_length_mismatch_without_strict_mode():
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write('1234A\n')
        f.write('999\n')  # malformed short row
        temp_file = f.name

    try:
        parser = FixedWidthParser(temp_file, [('ACCOUNT', 0, 4), ('STATUS', 4, 5)])
        validator = EnhancedFileValidator(parser, _make_mapping())
        result = validator.validate(detailed=False, strict_fixed_width=False)

        assert result['valid'] is False
        assert any(e.get('code') == 'FW_LEN_001' and e.get('row') == 2 for e in result['errors'])
    finally:
        os.unlink(temp_file)


def test_pic_formats_fail_on_invalid_values():
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write('A12345AB1\n')
        temp_file = f.name

    try:
        parser = FixedWidthParser(temp_file, [('SIGNED_NUM', 0, 6), ('CHAR3', 6, 9)])
        validator = EnhancedFileValidator(parser, _make_pic_mapping())
        result = validator.validate(detailed=False, strict_fixed_width=True, strict_level='format')

        assert result['valid'] is False
        assert any(e.get('code') == 'FW_FMT_001' for e in result['errors'])
    finally:
        os.unlink(temp_file)


def test_fixed_width_reports_line_length_and_impacted_fields():
    mapping = {
        'file_path': 'dummy_mapping.json',
        'fields': [
            {'name': 'A', 'position': 1, 'length': 2},
            {'name': 'B', 'position': 3, 'length': 2},
            {'name': 'C', 'position': 5, 'length': 2},
        ],
        'total_record_length': 6,
    }

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write('123456\n')   # valid
        f.write('1234\n')     # short, should impact C
        temp_file = f.name

    try:
        parser = FixedWidthParser(temp_file, [('A', 0, 2), ('B', 2, 4), ('C', 4, 6)])
        validator = EnhancedFileValidator(parser, mapping)
        result = validator.validate(detailed=False)

        assert result['valid'] is False
        codes = [e.get('code') for e in result['errors']]
        assert 'FW_LEN_001' in codes
        assert 'FW_LEN_002' in codes
        assert any(e.get('row') == 2 for e in result['errors'])
        assert any('Impacted/truncated fields' in e.get('message', '') for e in result['errors'])
    finally:
        os.unlink(temp_file)
