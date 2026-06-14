"""Tests for source profile validator."""

from src.pipeline.profile_validator import validate_source_profile


def test_profile_validator_positive():
    profile = {
        'source_system': 'SRC_A',
        'stages': {
            'ingest': {'enabled': True},
            'sqlloader': {'enabled': True, 'log_file': 'x.log', 'max_rejected': 0, 'max_discarded': 0},
            'java_batch': {'enabled': True},
            'output_validation': {'enabled': True, 'targets': [{'file': 'a.txt', 'mapping': 'm.json'}]},
        }
    }
    assert validate_source_profile(profile) == []


def test_profile_validator_negative_missing_target_mapping():
    profile = {
        'source_system': 'SRC_A',
        'stages': {
            'ingest': {'enabled': True},
            'sqlloader': {'enabled': False},
            'java_batch': {'enabled': True},
            'output_validation': {'enabled': True, 'targets': [{'file': 'a.txt'}]},
        }
    }
    issues = validate_source_profile(profile)
    assert any('mapping is required' in i for i in issues)
