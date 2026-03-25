"""Parity-style checks for report model adapters (standard vs chunked)."""

import pytest

from src.reports.adapters.result_adapter_chunked import adapt_chunked_validation_result
from src.reports.adapters.result_adapter_standard import adapt_standard_validation_result


@pytest.mark.parametrize(
    "mode,input_result,adapter",
    [
        (
            "chunked",
            {
                'valid': True,
                'timestamp': '2026-02-18T23:00:00',
                'total_rows': 5,
                'actual_columns': ['a', 'b'],
                'errors': [],
                'warnings': [],
                'statistics': {
                    'null_counts': {'a': 1},
                    'empty_string_counts': {'a': 2, 'b': 0},
                    'duplicate_count': 0,
                    'chunk_size': 100,
                    'elapsed_seconds': 0.2,
                    'rows_per_second': 25.0,
                },
            },
            lambda r: adapt_chunked_validation_result(r, file_path='data/sample.txt', mapping='map.json'),
        ),
        (
            "standard",
            {
                'valid': True,
                'errors': [],
                'warnings': [],
                'quality_metrics': {'quality_score': 100},
                'field_analysis': {'a': {'fill_rate_pct': 100}},
                'date_analysis': {},
                'business_rules': {'enabled': False, 'violations': [], 'statistics': {}},
                'appendix': {'validation_config': {'mode': 'standard'}},
            },
            adapt_standard_validation_result,
        ),
    ],
)
def test_report_parity_required_sections(mode, input_result, adapter):
    model = adapter(input_result)

    assert 'errors' in model
    assert 'warnings' in model
    assert 'quality_metrics' in model
    assert 'field_analysis' in model
    assert 'date_analysis' in model
    assert 'business_rules' in model
    assert 'appendix' in model


def test_chunked_adapter_handles_sparse_negative_case():
    # Negative case: sparse chunked payload should not crash and should still render model skeleton
    sparse = {'valid': False, 'errors': ['bad'], 'warnings': [], 'total_rows': 0}
    model = adapt_chunked_validation_result(sparse, file_path='data/empty.txt', mapping=None)

    assert model['valid'] is False
    assert model['error_count'] == 1
    assert model['appendix']['validation_config']['mode'] == 'chunked'
    assert model['quality_metrics']['total_rows'] == 0
