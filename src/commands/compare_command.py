from __future__ import annotations

import sys
from pathlib import Path
import click


def run_compare_command(file1, file2, keys, mapping, output, thresholds, detailed, chunk_size, progress, use_chunked, logger):
    """Compare two files and generate report."""
    try:
        from src.reports.renderers.comparison_renderer import HTMLReporter
        from src.validators.threshold import ThresholdEvaluator, ThresholdConfig
        from src.config.loader import ConfigLoader
        from src.services.compare_service import run_compare_service
        
        if not keys:
            click.echo("No keys provided - using row-by-row comparison...")
        if use_chunked:
            click.echo(f"Using chunked processing (chunk size: {chunk_size:,})...")

        results = run_compare_service(
            file1=file1,
            file2=file2,
            keys=keys,
            mapping=mapping,
            detailed=detailed,
            chunk_size=chunk_size,
            progress=progress,
            use_chunked=use_chunked,
        )
        
        # Display summary
        click.echo(f"\nComparison Summary:")
        click.echo(f"  Total rows (File 1): {results['total_rows_file1']}")
        click.echo(f"  Total rows (File 2): {results['total_rows_file2']}")
        click.echo(f"  Matching rows: {results['matching_rows']}")
        
        # Use count fields if lists are empty (chunked processing)
        only_in_file1 = results.get('only_in_file1_count', len(results.get('only_in_file1', [])))
        only_in_file2 = results.get('only_in_file2_count', len(results.get('only_in_file2', [])))
        
        click.echo(f"  Only in File 1: {only_in_file1}")
        click.echo(f"  Only in File 2: {only_in_file2}")
        click.echo(f"  Rows with differences: {results.get('rows_with_differences', len(results['differences']))}")
        
        # Evaluate thresholds
        if thresholds:
            loader = ConfigLoader()
            threshold_config = loader.load(thresholds)
            threshold_dict = ThresholdConfig.from_dict(threshold_config.get('thresholds', {}))
            evaluator = ThresholdEvaluator(threshold_dict)
        else:
            evaluator = ThresholdEvaluator()
        
        evaluation = evaluator.evaluate(results)
        
        click.echo("\nThreshold Evaluation:")
        if evaluation['passed']:
            click.echo(click.style('  ✓ PASS', fg='green'))
        elif evaluation['overall_result'].value == 'warning':
            click.echo(click.style('  ⚠ WARNING', fg='yellow'))
        else:
            click.echo(click.style('  ✗ FAIL', fg='red'))
        
        # Show detailed field statistics if available
        if detailed and results.get('field_statistics'):
            stats = results['field_statistics']
            click.echo(f"\nField-Level Statistics:")
            click.echo(f"  Fields with differences: {stats['fields_with_differences']}")
            if stats['most_different_field']:
                click.echo(f"  Most different field: {stats['most_different_field']}")
        
        # Generate report
        if output:
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            reporter = HTMLReporter()
            reporter.generate(results, output)
            click.echo(f"\nReport generated: {output}")
        
    except Exception as e:
        logger.error(f"Error comparing files: {e}")
        sys.exit(1)

