"""Tests for RuleEngine `when` condition support."""

import pandas as pd

from src.validators.rule_engine import RuleEngine


def test_when_condition_equals_filters_rows():
    rules_config = {
        'rules': [
            {
                'id': 'BRC1',
                'name': 'State required for ACTIVE',
                'description': 'state required when status active',
                'type': 'field_validation',
                'severity': 'error',
                'field': 'state_code',
                'operator': 'not_null',
                'when': 'status = ACTIVE',
                'enabled': True,
            }
        ]
    }

    df = pd.DataFrame([
        {'status': 'ACTIVE', 'state_code': ''},
        {'status': 'INACTIVE', 'state_code': ''},
    ])

    engine = RuleEngine(rules_config)
    violations = engine.validate(df)

    assert len(violations) == 1
    assert violations[0].rule_id == 'BRC1'
    assert violations[0].row_number == 1
    assert violations[0].issue_code == 'BR_BRC1_FIELD'


def test_when_condition_in_filters_rows():
    rules_config = {
        'rules': [
            {
                'id': 'BRC2',
                'name': 'Status in list must have score',
                'description': 'score required for active/suspended',
                'type': 'field_validation',
                'severity': 'error',
                'field': 'score',
                'operator': 'not_null',
                'when': 'status in (ACTIVE,SUSPENDED)',
                'enabled': True,
            }
        ]
    }

    df = pd.DataFrame([
        {'status': 'ACTIVE', 'score': ''},
        {'status': 'SUSPENDED', 'score': '10'},
        {'status': 'INACTIVE', 'score': ''},
    ])

    engine = RuleEngine(rules_config)
    violations = engine.validate(df)

    assert len(violations) == 1
    assert violations[0].row_number == 1
    assert violations[0].issue_code == 'BR_BRC2_FIELD'
