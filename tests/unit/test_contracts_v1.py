"""Contract smoke tests for v1 schemas/docs."""

import json
from pathlib import Path


def test_validation_result_contract_files_exist():
    assert Path('docs/contracts/validation_result_v1.md').exists()
    schema_path = Path('contracts/validation_result_v1.schema.json')
    assert schema_path.exists()

    schema = json.loads(schema_path.read_text())
    required = set(schema.get('required', []))
    assert {'valid', 'errors', 'warnings', 'total_rows'}.issubset(required)


def test_business_rules_contract_files_exist():
    assert Path('docs/contracts/business_rules_v1.md').exists()
    schema_path = Path('contracts/business_rules_v1.schema.json')
    assert schema_path.exists()

    schema = json.loads(schema_path.read_text())
    required = set(schema.get('required', []))
    assert {'metadata', 'rules'}.issubset(required)


def test_sample_business_rules_matches_min_required_shape():
    cfg = json.loads(Path('config/rules/p327_business_rules.json').read_text())
    assert 'metadata' in cfg
    assert 'rules' in cfg
    assert isinstance(cfg['rules'], list)
    if cfg['rules']:
        first = cfg['rules'][0]
        for k in ['id', 'name', 'type', 'severity', 'enabled']:
            assert k in first
