"""Unit tests for schema reconciliation."""

from src.database.reconciliation import SchemaReconciler
from src.config.mapping_parser import MappingParser


class DummyConnection:
    """Simple placeholder connection for reconciler construction."""
    pass


def _build_mapping(mapping_overrides=None):
    mapping_dict = {
        'mapping_name': 'recon_test',
        'version': '1.0.0',
        'description': 'Reconciliation test mapping',
        'source': {'type': 'file', 'format': 'pipe_delimited'},
        'target': {'type': 'database', 'table_name': 'TEST_TABLE'},
        'mappings': [
            {
                'source_column': 'name',
                'target_column': 'NAME',
                'data_type': 'string',
                'required': True,
                'transformations': [],
                'validation_rules': [
                    {'type': 'max_length', 'parameters': {'length': 50}}
                ],
            }
        ],
        'key_columns': ['name'],
    }

    if mapping_overrides:
        mapping_dict.update(mapping_overrides)

    return MappingParser().parse(mapping_dict)


def test_reconcile_missing_table_returns_error():
    reconciler = SchemaReconciler(DummyConnection())
    mapping = _build_mapping()

    reconciler._table_exists = lambda _table, _owner=None: False

    result = reconciler.reconcile_mapping(mapping)

    assert result['valid'] is False
    assert result['error_count'] == 1
    assert 'Target table does not exist' in result['errors'][0]


def test_reconcile_type_mismatch_returns_warning():
    reconciler = SchemaReconciler(DummyConnection())
    mapping = _build_mapping()

    reconciler._table_exists = lambda _table, _owner=None: True
    reconciler._get_table_columns = lambda _table, _owner=None: ['NAME']
    reconciler._get_required_columns = lambda _table, _owner=None: set()
    reconciler._get_column_details = lambda _table, _owner=None: {
        'NAME': {
            'data_type': 'NUMBER',
            'data_length': 10,
            'data_precision': 10,
            'data_scale': 0,
            'nullable': 'N',
        }
    }

    result = reconciler.reconcile_mapping(mapping)

    assert result['valid'] is True
    assert result['warning_count'] >= 1
    assert any('Type mismatch for NAME' in w for w in result['warnings'])


def test_reconcile_max_length_exceeds_db_length_warns():
    reconciler = SchemaReconciler(DummyConnection())
    mapping = _build_mapping()

    reconciler._table_exists = lambda _table, _owner=None: True
    reconciler._get_table_columns = lambda _table, _owner=None: ['NAME']
    reconciler._get_required_columns = lambda _table, _owner=None: set()
    reconciler._get_column_details = lambda _table, _owner=None: {
        'NAME': {
            'data_type': 'VARCHAR2',
            'data_length': 20,
            'data_precision': None,
            'data_scale': None,
            'nullable': 'N',
        }
    }

    result = reconciler.reconcile_mapping(mapping)

    assert result['valid'] is True
    assert any('validation max_length (50) exceeds database length (20)' in w for w in result['warnings'])


def test_reconcile_owner_qualified_table_reference_is_supported():
    reconciler = SchemaReconciler(DummyConnection())
    mapping = _build_mapping({'target': {'type': 'database', 'table_name': 'APP.TEST_TABLE'}})

    called = {}

    def _table_exists(table, owner=None):
        called['table'] = table
        called['owner'] = owner
        return True

    reconciler._table_exists = _table_exists
    reconciler._get_table_columns = lambda _table, _owner=None: ['NAME']
    reconciler._get_required_columns = lambda _table, _owner=None: set()
    reconciler._get_column_details = lambda _table, _owner=None: {
        'NAME': {
            'data_type': 'VARCHAR2',
            'data_length': 100,
            'data_precision': None,
            'data_scale': None,
            'nullable': 'N',
        }
    }
    reconciler._get_pk_unique_constraint_columns = lambda _table, _owner=None: []

    result = reconciler.reconcile_mapping(mapping)

    assert result['valid'] is True
    assert called['table'] == 'TEST_TABLE'
    assert called['owner'] == 'APP'


def test_reconcile_reports_unmapped_required_columns():
    reconciler = SchemaReconciler(DummyConnection())
    mapping = _build_mapping()

    reconciler._table_exists = lambda _table, _owner=None: True
    reconciler._get_table_columns = lambda _table, _owner=None: ['NAME', 'ID']
    reconciler._get_required_columns = lambda _table, _owner=None: {'NAME', 'ID'}
    reconciler._get_column_details = lambda _table, _owner=None: {
        'NAME': {
            'data_type': 'VARCHAR2',
            'data_length': 100,
            'data_precision': None,
            'data_scale': None,
            'nullable': 'N',
        },
        'ID': {
            'data_type': 'NUMBER',
            'data_length': 22,
            'data_precision': 22,
            'data_scale': 0,
            'nullable': 'N',
        },
    }

    result = reconciler.reconcile_mapping(mapping)

    assert result['valid'] is True
    assert 'ID' in result['unmapped_required']
    assert any('Required database columns not in mapping' in w for w in result['warnings'])


def test_reconcile_warns_when_mapping_keys_do_not_match_pk_unique():
    reconciler = SchemaReconciler(DummyConnection())
    mapping = _build_mapping({'key_columns': ['name']})

    reconciler._table_exists = lambda _table, _owner=None: True
    reconciler._get_table_columns = lambda _table, _owner=None: ['NAME']
    reconciler._get_required_columns = lambda _table, _owner=None: set()
    reconciler._get_column_details = lambda _table, _owner=None: {
        'NAME': {
            'data_type': 'VARCHAR2',
            'data_length': 100,
            'data_precision': None,
            'data_scale': None,
            'nullable': 'N',
        }
    }
    # PK/unique defined on a different column set
    reconciler._get_pk_unique_constraint_columns = lambda _table, _owner=None: [{'ID'}]

    result = reconciler.reconcile_mapping(mapping)

    assert any('do not exactly match any database PK/UNIQUE constraint' in w for w in result['warnings'])
