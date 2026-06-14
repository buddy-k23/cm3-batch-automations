"""Unit tests for threshold evaluator."""

import pytest
from src.validators.threshold import (
    ThresholdEvaluator,
    Threshold,
    ThresholdResult,
    ThresholdConfig,
)


class TestThresholdEvaluator:
    """Test ThresholdEvaluator class."""

    def test_evaluate_pass(self):
        """Test evaluation that passes all thresholds."""
        comparison_results = {
            'total_rows_file1': 1000,
            'total_rows_file2': 1000,
            'only_in_file1': [],
            'only_in_file2': [],
            'differences': [],
            'rows_with_differences': 0,
            'field_statistics': {},
        }
        
        evaluator = ThresholdEvaluator()
        result = evaluator.evaluate(comparison_results)
        
        assert result['passed'] is True
        assert result['overall_result'] == ThresholdResult.PASS

    def test_evaluate_fail_missing_rows(self):
        """Test evaluation that fails on missing rows."""
        comparison_results = {
            'total_rows_file1': 100,
            'total_rows_file2': 100,
            'only_in_file1': [{'id': i} for i in range(15)],  # 15 missing rows
            'only_in_file2': [],
            'differences': [],
            'rows_with_differences': 0,
            'field_statistics': {},
        }
        
        evaluator = ThresholdEvaluator()
        result = evaluator.evaluate(comparison_results)
        
        assert result['passed'] is False
        assert result['overall_result'] == ThresholdResult.FAIL
        assert result['metrics']['missing_rows'] == 15

    def test_evaluate_warning(self):
        """Test evaluation that triggers warning."""
        comparison_results = {
            'total_rows_file1': 100,
            'total_rows_file2': 100,
            'only_in_file1': [{'id': i} for i in range(7)],  # 7 missing (warning level)
            'only_in_file2': [],
            'differences': [],
            'rows_with_differences': 0,
            'field_statistics': {},
        }
        
        evaluator = ThresholdEvaluator()
        result = evaluator.evaluate(comparison_results)
        
        assert result['overall_result'] == ThresholdResult.WARNING
        assert result['metrics']['missing_rows'] == 7

    def test_custom_thresholds(self):
        """Test with custom threshold configuration."""
        custom_thresholds = {
            'missing_rows': Threshold(
                name='Missing Rows',
                metric='missing_rows',
                max_value=5,
                max_percent=0.5,
            )
        }
        
        comparison_results = {
            'total_rows_file1': 100,
            'total_rows_file2': 100,
            'only_in_file1': [{'id': i} for i in range(6)],  # 6 missing
            'only_in_file2': [],
            'differences': [],
            'rows_with_differences': 0,
        }
        
        evaluator = ThresholdEvaluator(custom_thresholds)
        result = evaluator.evaluate(comparison_results)
        
        assert result['passed'] is False

    def test_generate_report(self):
        """Test report generation."""
        comparison_results = {
            'total_rows_file1': 100,
            'total_rows_file2': 100,
            'only_in_file1': [],
            'only_in_file2': [],
            'differences': [],
            'rows_with_differences': 0,
            'field_statistics': {},
        }
        
        evaluator = ThresholdEvaluator()
        evaluation = evaluator.evaluate(comparison_results)
        report = evaluator.generate_report(evaluation)
        
        assert "THRESHOLD EVALUATION REPORT" in report
        assert "PASS" in report


class TestThresholdConfig:
    """Test ThresholdConfig class."""

    def test_from_dict(self):
        """Test creating thresholds from dictionary."""
        config = {
            'test_threshold': {
                'name': 'Test Threshold',
                'metric': 'test_metric',
                'max_value': 10,
                'max_percent': 1.0,
            }
        }
        
        thresholds = ThresholdConfig.from_dict(config)
        
        assert 'test_threshold' in thresholds
        assert thresholds['test_threshold'].name == 'Test Threshold'
        assert thresholds['test_threshold'].max_value == 10

    def test_to_dict(self):
        """Test converting thresholds to dictionary."""
        thresholds = {
            'test': Threshold(
                name='Test',
                metric='test_metric',
                max_value=10,
            )
        }
        
        config = ThresholdConfig.to_dict(thresholds)
        
        assert 'test' in config
        assert config['test']['name'] == 'Test'
        assert config['test']['max_value'] == 10
