from __future__ import annotations

import json
import sys
from pathlib import Path
import click


def run_compare_command(file1, file2, keys, mapping, output, thresholds, detailed, chunk_size, progress, use_chunked, logger):
    """Compare two files and generate a diff report.

    Exit code contract (S8-1, #392):
        * ``0`` — files match (no rows in ``only_in_file1``, ``only_in_file2``,
          or ``rows_with_differences``) **or** all differences are within the
          configured ``--thresholds`` (i.e. evaluator returns ``passed=True``).
        * ``1`` — at least one difference is detected and (when ``--thresholds``
          is supplied) the threshold evaluator does not return ``passed=True``.

    "Difference" is defined as any row present only in file 1, only in file 2,
    or any row whose field-level comparison flagged a value mismatch. When
    ``--thresholds`` is supplied, the user-defined tolerances govern the
    pass/fail decision; when no thresholds are supplied, *any* difference fails
    the run so CI pipelines do not silently pass on broken comparisons.
    """
    try:
        from src.reports.renderers.comparison_renderer import HTMLReporter
        from src.validators.threshold import ThresholdEvaluator, ThresholdConfig
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
        
        # Evaluate thresholds. ``--thresholds`` accepts a path to a JSON file
        # whose top-level ``thresholds`` key holds the per-metric config.
        # (S8-1, #392) Replaced the previous ``ConfigLoader().load(thresholds)``
        # call, which mis-treated the path as an environment name and could
        # never locate user-supplied files.
        if thresholds:
            threshold_path = Path(thresholds)
            threshold_config = json.loads(threshold_path.read_text(encoding='utf-8'))
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

        # ------------------------------------------------------------------
        # Exit-code decision (S8-1, #392)
        # ------------------------------------------------------------------
        # Count any structural / value-level differences. We use the chunked-
        # safe count fields (already populated above) plus rows_with_differences.
        rows_with_differences = results.get(
            'rows_with_differences', len(results.get('differences', []))
        )
        has_any_difference = (
            only_in_file1 > 0
            or only_in_file2 > 0
            or rows_with_differences > 0
        )

        if thresholds:
            # User opted in to tolerance bands: defer to evaluator result.
            # 'passed' is True only when overall_result == PASS (warning/fail
            # both signal CI failure, matching the strict-gate convention used
            # by ``valdo validate`` and ``valdo detect-drift``).
            if not evaluation['passed']:
                sys.exit(1)
        else:
            # No thresholds supplied: any difference is a failure so CI gates
            # do not silently pass on broken comparisons.
            if has_any_difference:
                sys.exit(1)

    except SystemExit:
        # Preserve intentional non-zero exits set above; do not mask as error.
        raise
    except Exception as e:
        logger.error(f"Error comparing files: {e}")
        sys.exit(1)

