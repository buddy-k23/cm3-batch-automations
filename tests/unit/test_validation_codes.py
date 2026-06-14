"""Tests for validation issue codes and summaries."""

import os
import tempfile

from src.parsers.pipe_delimited_parser import PipeDelimitedParser
from src.parsers.enhanced_validator import EnhancedFileValidator


def test_missing_required_schema_field_has_code_and_summary():
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write('a|b\n')
        f.write('c|d\n')
        temp_file = f.name

    try:
        parser = PipeDelimitedParser(temp_file, columns=['col1', 'col2'])
        mapping_config = {
            'fields': [
                {'name': 'col1', 'required': True},
                {'name': 'col3', 'required': True},
            ]
        }

        validator = EnhancedFileValidator(parser, mapping_config=mapping_config)
        result = validator.validate(detailed=False)

        assert result['valid'] is False
        assert any(e.get('code') == 'VAL_SCHEMA_MISSING_FIELD' for e in result['errors'])
        assert result['issue_code_summary'].get('VAL_SCHEMA_MISSING_FIELD', 0) >= 1
    finally:
        os.unlink(temp_file)
