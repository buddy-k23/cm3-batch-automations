"""Threshold-based pass/fail criteria for comparisons."""

from typing import Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum


class ThresholdResult(Enum):
    """Result of threshold evaluation."""
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"


@dataclass
class Threshold:
    """Represents a threshold configuration."""
    name: str
    metric: str
    max_value: Optional[float] = None
    max_percent: Optional[float] = None
    warning_value: Optional[float] = None
    warning_percent: Optional[float] = None
    enabled: bool = True


class ThresholdEvaluator:
    """Evaluates comparison results against thresholds."""

    def __init__(self, thresholds: Optional[Dict[str, Threshold]] = None):
        """Initialize threshold evaluator.
        
        Args:
            thresholds: Dictionary of threshold configurations
        """
        self.thresholds = thresholds or self._get_default_thresholds()

    def _get_default_thresholds(self) -> Dict[str, Threshold]:
        """Get default threshold configurations.
        
        Returns:
            Dictionary of default thresholds
        """
        return {
            'missing_rows': Threshold(
                name='Missing Rows',
                metric='missing_rows',
                max_value=10,
                max_percent=1.0,
                warning_value=5,
                warning_percent=0.5
            ),
            'extra_rows': Threshold(
                name='Extra Rows',
                metric='extra_rows',
                max_value=10,
                max_percent=1.0,
                warning_value=5,
                warning_percent=0.5
            ),
            'different_rows': Threshold(
                name='Different Rows',
                metric='different_rows',
                max_value=20,
                max_percent=2.0,
                warning_value=10,
                warning_percent=1.0
            ),
            'field_differences': Threshold(
                name='Field Differences',
                metric='field_differences',
                max_value=50,
                max_percent=5.0,
                warning_value=25,
                warning_percent=2.5
            ),
        }

    def evaluate(self, comparison_results: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate comparison results against thresholds.
        
        Args:
            comparison_results: Results from FileComparator
            
        Returns:
            Evaluation results with pass/fail status
        """
        total_rows = comparison_results['total_rows_file1']
        
        # Calculate metrics
        metrics = {
            'missing_rows': len(comparison_results['only_in_file1']),
            'extra_rows': len(comparison_results['only_in_file2']),
            'different_rows': comparison_results.get('rows_with_differences', 
                                                     len(comparison_results['differences'])),
        }
        
        # Calculate field differences if available
        if 'field_statistics' in comparison_results and comparison_results['field_statistics']:
            total_field_diffs = sum(
                comparison_results['field_statistics'].get('field_difference_counts', {}).values()
            )
            metrics['field_differences'] = total_field_diffs
        
        # Evaluate each threshold
        evaluations = {}
        overall_result = ThresholdResult.PASS
        
        for threshold_key, threshold in self.thresholds.items():
            if not threshold.enabled:
                continue
            
            metric_value = metrics.get(threshold.metric, 0)
            evaluation = self._evaluate_threshold(metric_value, total_rows, threshold)
            evaluations[threshold_key] = evaluation
            
            # Update overall result
            if evaluation['result'] == ThresholdResult.FAIL:
                overall_result = ThresholdResult.FAIL
            elif evaluation['result'] == ThresholdResult.WARNING and overall_result == ThresholdResult.PASS:
                overall_result = ThresholdResult.WARNING
        
        return {
            'overall_result': overall_result,
            'passed': overall_result == ThresholdResult.PASS,
            'metrics': metrics,
            'evaluations': evaluations,
            'total_rows': total_rows,
        }

    def _evaluate_threshold(self, value: float, total: int, threshold: Threshold) -> Dict[str, Any]:
        """Evaluate a single threshold.
        
        Args:
            value: Actual metric value
            total: Total number of rows
            threshold: Threshold configuration
            
        Returns:
            Evaluation result
        """
        # Keep percent as ratio (0.0-1.0) to align with threshold config semantics.
        percent = (value / total) if total > 0 else 0
        
        # Check fail conditions
        if threshold.max_value is not None and value > threshold.max_value:
            result = ThresholdResult.FAIL
            reason = f"Value {value} exceeds max {threshold.max_value}"
        elif threshold.max_percent is not None and percent > threshold.max_percent:
            result = ThresholdResult.FAIL
            reason = f"Percent {percent * 100:.2f}% exceeds max {threshold.max_percent * 100:.2f}%"
        # Check warning conditions
        elif threshold.warning_value is not None and value > threshold.warning_value:
            result = ThresholdResult.WARNING
            reason = f"Value {value} exceeds warning {threshold.warning_value}"
        elif threshold.warning_percent is not None and percent > threshold.warning_percent:
            result = ThresholdResult.WARNING
            reason = f"Percent {percent * 100:.2f}% exceeds warning {threshold.warning_percent * 100:.2f}%"
        else:
            result = ThresholdResult.PASS
            reason = "Within acceptable limits"
        
        return {
            'result': result,
            'value': value,
            'percent': percent,
            'threshold': threshold,
            'reason': reason,
        }

    def generate_report(self, evaluation_results: Dict[str, Any]) -> str:
        """Generate human-readable threshold evaluation report.
        
        Args:
            evaluation_results: Results from evaluate()
            
        Returns:
            Formatted report string
        """
        report = []
        report.append("=" * 70)
        report.append("THRESHOLD EVALUATION REPORT")
        report.append("=" * 70)
        
        overall = evaluation_results['overall_result']
        if overall == ThresholdResult.PASS:
            report.append("✓ OVERALL: PASS")
        elif overall == ThresholdResult.WARNING:
            report.append("⚠ OVERALL: WARNING")
        else:
            report.append("✗ OVERALL: FAIL")
        
        report.append("")
        report.append(f"Total Rows: {evaluation_results['total_rows']}")
        report.append("")
        
        report.append("METRICS:")
        for metric, value in evaluation_results['metrics'].items():
            report.append(f"  {metric}: {value}")
        report.append("")
        
        report.append("THRESHOLD EVALUATIONS:")
        for threshold_key, evaluation in evaluation_results['evaluations'].items():
            result = evaluation['result']
            symbol = "✓" if result == ThresholdResult.PASS else "⚠" if result == ThresholdResult.WARNING else "✗"
            
            report.append(f"  {symbol} {evaluation['threshold'].name}:")
            report.append(f"      Value: {evaluation['value']} ({evaluation['percent']:.2f}%)")
            report.append(f"      Status: {result.value.upper()}")
            report.append(f"      Reason: {evaluation['reason']}")
        
        report.append("=" * 70)
        
        return "\n".join(report)


class ThresholdConfig:
    """Manages threshold configurations."""

    @staticmethod
    def from_dict(config: Dict[str, Any]) -> Dict[str, Threshold]:
        """Create thresholds from configuration dictionary.
        
        Args:
            config: Configuration dictionary
            
        Returns:
            Dictionary of Threshold objects
        """
        thresholds = {}
        
        for key, threshold_config in config.items():
            thresholds[key] = Threshold(
                name=threshold_config.get('name', key),
                metric=threshold_config.get('metric', key),
                max_value=threshold_config.get('max_value'),
                max_percent=threshold_config.get('max_percent'),
                warning_value=threshold_config.get('warning_value'),
                warning_percent=threshold_config.get('warning_percent'),
                enabled=threshold_config.get('enabled', True),
            )
        
        return thresholds

    @staticmethod
    def to_dict(thresholds: Dict[str, Threshold]) -> Dict[str, Any]:
        """Convert thresholds to dictionary.
        
        Args:
            thresholds: Dictionary of Threshold objects
            
        Returns:
            Configuration dictionary
        """
        config = {}
        
        for key, threshold in thresholds.items():
            config[key] = {
                'name': threshold.name,
                'metric': threshold.metric,
                'max_value': threshold.max_value,
                'max_percent': threshold.max_percent,
                'warning_value': threshold.warning_value,
                'warning_percent': threshold.warning_percent,
                'enabled': threshold.enabled,
            }
        
        return config
