"""Tests for Oracle expected file generation module."""

from src.pipeline.oracle_expected_generator import generate_expected_from_oracle


def test_oracle_expected_generator_dry_run_positive():
    manifest = {
        'schema': 'app_int',
        'jobs': [
            {'name': 'SRC_A_P327', 'query_file': 'a.sql', 'output_file': 'out/a.txt'}
        ]
    }
    out = generate_expected_from_oracle(manifest, dry_run=True)
    assert out['status'] == 'passed'
    assert out['jobs'][0]['status'] == 'dry_run'


def test_oracle_expected_generator_negative_no_jobs():
    out = generate_expected_from_oracle({'schema': 'app_int', 'jobs': []}, dry_run=True)
    assert out['status'] == 'failed'
