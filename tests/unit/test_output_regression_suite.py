"""Tests for output regression suite module."""

from src.pipeline.output_regression_suite import run_output_regression_suite


def test_output_regression_suite_dry_run_positive():
    cfg = {
        'targets': [
            {
                'name': 'p327',
                'file': 'out/p327.txt',
                'mapping': 'config/mappings/p327.json',
                'rules': 'config/rules/p327.json',
                'strict_fixed_width': True,
                'strict_level': 'all',
            }
        ]
    }
    out = run_output_regression_suite(cfg, dry_run=True)
    assert out['status'] == 'passed'
    assert out['targets'][0]['status'] == 'dry_run'


def test_output_regression_suite_negative_missing_mapping():
    cfg = {'targets': [{'name': 'p327', 'file': 'out/p327.txt'}]}
    out = run_output_regression_suite(cfg, dry_run=True)
    assert out['status'] == 'failed'
