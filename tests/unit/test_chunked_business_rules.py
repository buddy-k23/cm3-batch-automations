"""Tests for business-rule execution in chunked validation path."""

import json
import os
import tempfile

from src.parsers.chunked_validator import ChunkedFileValidator


def _write_temp(content: str, suffix: str) -> str:
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=suffix) as f:
        f.write(content)
        return f.name


def test_chunked_business_rules_positive_no_violations():
    data_path = _write_temp('status|score\nACTIVE|10\nINACTIVE|20\n', '.txt')
    rules = {
        'metadata': {'name': 't'},
        'rules': [
            {
                'id': 'BR10',
                'name': 'score required when active',
                'type': 'field_validation',
                'severity': 'error',
                'field': 'score',
                'operator': 'not_null',
                'when': 'status = ACTIVE',
                'enabled': True,
            }
        ],
    }
    rules_path = _write_temp(json.dumps(rules), '.json')

    try:
        v = ChunkedFileValidator(file_path=data_path, delimiter='|', chunk_size=1, rules_config_path=rules_path)
        out = v.validate(show_progress=False)
        assert out['business_rules']['enabled'] is True
        assert out['business_rules']['statistics']['total_violations'] == 0
    finally:
        os.unlink(data_path)
        os.unlink(rules_path)


def test_chunked_business_rules_negative_with_violation():
    data_path = _write_temp('status|score\nACTIVE|\nINACTIVE|20\n', '.txt')
    rules = {
        'metadata': {'name': 't'},
        'rules': [
            {
                'id': 'BR11',
                'name': 'score required when active',
                'type': 'field_validation',
                'severity': 'error',
                'field': 'score',
                'operator': 'not_null',
                'when': 'status = ACTIVE',
                'enabled': True,
            }
        ],
    }
    rules_path = _write_temp(json.dumps(rules), '.json')

    try:
        v = ChunkedFileValidator(file_path=data_path, delimiter='|', chunk_size=1, rules_config_path=rules_path)
        out = v.validate(show_progress=False)
        assert out['valid'] is False
        assert out['business_rules']['statistics']['total_violations'] >= 1
        assert any((isinstance(e, dict) and e.get('category') == 'business_rule') for e in out['errors'])
    finally:
        os.unlink(data_path)
        os.unlink(rules_path)
