"""Main entry point for CM3 Batch Automations."""

import sys
import json
import os
import click
from src.utils.logger import setup_logger


@click.group()
@click.version_option(version='0.1.0')
def cli():
    """CM3 Batch Automations - File parsing, validation, and comparison tool."""
    pass


@cli.command()
@click.option('--file', '-f', required=True, help='File to detect format')
def detect(file):
    """Detect file format automatically."""
    logger = setup_logger('cm3-batch', log_to_file=False)
    
    try:
        from src.parsers.format_detector import FormatDetector
        
        detector = FormatDetector()
        result = detector.detect(file)
        
        logger.info(f"File: {file}")
        logger.info(f"Format: {result['format'].value}")
        logger.info(f"Confidence: {result['confidence']:.2%}")
        if 'delimiter' in result:
            logger.info(f"Delimiter: {result['delimiter']}")
        logger.info(f"Sample lines: {result['line_count']}")
        
        click.echo(f"\nDetected format: {result['format'].value}")
        click.echo(f"Confidence: {result['confidence']:.2%}")
        
    except Exception as e:
        logger.error(f"Error detecting format: {e}")
        sys.exit(1)


@cli.command()
@click.option('--file', '-f', required=True, help='File to parse')
@click.option('--mapping', '-m', help='Mapping configuration file')
@click.option('--format', '-t', help='File format (auto-detect if not specified)')
@click.option('--output', '-o', help='Output file (default: stdout)')
@click.option('--use-chunked', is_flag=True, help='Use chunked processing for large files')
@click.option('--chunk-size', default=100000, help='Chunk size for large files (default: 100000)')
def parse(file, mapping, format, output, use_chunked, chunk_size):
    """Parse file and display contents."""
    logger = setup_logger('cm3-batch', log_to_file=False)

    try:
        from src.commands.parse_command import run_parse_command
        run_parse_command(file, mapping, format, output, use_chunked, chunk_size, logger)
    except Exception as e:
        logger.error(f"Error parsing file: {e}")
        sys.exit(1)


@cli.command()
@click.option('--file', '-f', required=True, help='File to validate')
@click.option('--mapping', '-m', help='Mapping file for schema validation')
@click.option('--rules', '-r', type=click.Path(exists=True), help='Business rules configuration file (JSON)')
@click.option('--output', '-o', help='Output report file (.json or .html)')
@click.option('--detailed/--basic', default=True, help='Include detailed field analysis')
@click.option('--use-chunked', is_flag=True, help='Use chunked processing for large files')
@click.option('--chunk-size', default=100000, help='Chunk size for large files (default: 100000)')
@click.option('--progress/--no-progress', default=True, help='Show progress bar')
@click.option('--strict-fixed-width/--no-strict-fixed-width', default=False,
              help='Enable strict fixed-width field-level validation checks')
@click.option('--strict-level', type=click.Choice(['basic', 'format', 'all']), default='format',
              help='Strict fixed-width validation depth')
@click.option('--workers', default=1, type=int, show_default=True,
              help='Parallel worker processes for chunked validation (1 disables parallel mode)')
def validate(file, mapping, rules, output, detailed, use_chunked, chunk_size, progress,
             strict_fixed_width, strict_level, workers):
    """Validate file format and content."""
    logger = setup_logger('cm3-batch', log_to_file=False)

    try:
        from src.commands.validate_command import run_validate_command
        run_validate_command(
            file, mapping, rules, output, detailed, use_chunked, chunk_size, progress,
            strict_fixed_width, strict_level, workers, logger,
        )
    except Exception as e:
        logger.error(f"Error validating file: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)



@cli.command('convert-mappings')
@click.option('--input-dir', default='mappings/csv', type=click.Path(exists=True, file_okay=False),
              show_default=True, help='Directory containing mapping CSV/Excel templates')
@click.option('--output-dir', default='config/mappings', type=click.Path(file_okay=False),
              show_default=True, help='Directory to write mapping JSON files')
@click.option('--format', 'file_format', type=click.Choice(['pipe_delimited', 'fixed_width', 'csv', 'tsv']),
              help='Optional mapping source format override')
@click.option('--error-report-dir', default='reports/template_validation', type=click.Path(file_okay=False),
              show_default=True, help='Directory to write strict validation error reports')
def convert_mappings(input_dir, output_dir, file_format, error_report_dir):
    """Bulk convert mapping CSV/Excel templates to JSON mapping files."""
    logger = setup_logger('cm3-batch', log_to_file=False)

    try:
        from src.commands.convert_mappings_command import run_convert_mappings_command

        rc = run_convert_mappings_command(
            input_dir=input_dir,
            output_dir=output_dir,
            file_format=file_format,
            error_report_dir=error_report_dir,
            logger=logger,
        )
        if rc != 0:
            sys.exit(rc)
    except Exception as e:
        logger.error(f"Error converting mappings: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


@cli.command('convert-rules')
@click.option('--template', '-t', required=True, type=click.Path(exists=True),
              help='Excel or CSV template file')
@click.option('--output', '-o', required=True, type=click.Path(),
              help='Output JSON rules file')
@click.option('--sheet', '-s', help='Sheet name (for Excel files)')
def convert_rules(template, output, sheet):
    """Convert Excel/CSV template to JSON rules configuration."""
    logger = setup_logger('cm3-batch', log_to_file=False)
    
    try:
        from src.config.rules_template_converter import RulesTemplateConverter
        
        converter = RulesTemplateConverter()
        
        # Convert based on file type
        if template.endswith('.xlsx') or template.endswith('.xls'):
            logger.info(f"Converting Excel template: {template}")
            rules_config = converter.from_excel(template, sheet)
        elif template.endswith('.csv'):
            logger.info(f"Converting CSV template: {template}")
            rules_config = converter.from_csv(template)
        else:
            logger.error("Template must be .xlsx, .xls, or .csv file")
            sys.exit(1)
        
        # Save to JSON
        converter.save(output)
        
        # Display summary
        total_rules = len(rules_config['rules'])
        enabled_rules = len([r for r in rules_config['rules'] if r.get('enabled', True)])
        
        click.echo(f"\n✓ Rules configuration saved to: {output}")
        click.echo(f"  Total rules: {total_rules}")
        click.echo(f"  Enabled rules: {enabled_rules}")
        click.echo(f"  Disabled rules: {total_rules - enabled_rules}")
        
        # Show rule types breakdown
        rule_types = {}
        for rule in rules_config['rules']:
            rule_type = rule.get('type', 'unknown')
            rule_types[rule_type] = rule_types.get(rule_type, 0) + 1
        
        click.echo("\n  Rule types:")
        for rule_type, count in rule_types.items():
            click.echo(f"    - {rule_type}: {count}")
        
    except Exception as e:
        logger.error(f"Error converting template: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


@cli.command()
@click.option('--file1', '-f1', required=True, help='First file')
@click.option('--file2', '-f2', required=True, help='Second file')
@click.option('--keys', '-k', help='Key columns (comma-separated). If not provided, compares row-by-row.')
@click.option('--mapping', '-m', help='Mapping file (recommended for fixed-width comparison).')
@click.option('--output', '-o', help='Output HTML report file')
@click.option('--thresholds', '-t', help='Threshold configuration file')
@click.option('--detailed/--basic', default=True, help='Detailed field analysis')
@click.option('--chunk-size', default=100000, help='Chunk size for large files (default: 100000)')
@click.option('--progress/--no-progress', default=True, help='Show progress bar')
@click.option('--use-chunked', is_flag=True, help='Use chunked processing for large files')
def compare(file1, file2, keys, mapping, output, thresholds, detailed, chunk_size, progress, use_chunked):
    """Compare two files and generate report."""
    logger = setup_logger('cm3-batch', log_to_file=False)

    try:
        from src.commands.compare_command import run_compare_command
        run_compare_command(file1, file2, keys, mapping, output, thresholds, detailed, chunk_size, progress, use_chunked, logger)
    except Exception as e:
        logger.error(f"Error comparing files: {e}")
        sys.exit(1)


@cli.command()
def info():
    """Display system information and check dependencies."""
    logger = setup_logger('cm3-batch', log_to_file=False)
    
    click.echo("CM3 Batch Automations v0.1.0")
    click.echo(f"Python version: {sys.version}")
    click.echo(f"Working directory: {os.getcwd()}")
    
    # Check Oracle
    oracle_home = os.getenv("ORACLE_HOME")
    if oracle_home:
        click.echo(f"Oracle Home: {oracle_home}")
    else:
        click.echo(click.style("Warning: ORACLE_HOME not set", fg='yellow'))
    
    try:
        import oracledb
        click.echo(f"oracledb version: {oracledb.__version__}")
        click.echo(click.style("✓ Oracle connectivity available (thin mode)", fg='green'))
    except ImportError:
        click.echo(click.style("✗ oracledb not available", fg='red'))
    except Exception as e:
        click.echo(click.style(f"✗ Oracle client error: {e}", fg='red'))


@cli.command()
@click.option('--mapping', '-m', required=True, help='Mapping file to validate')
@click.option('--output', '-o', help='Write reconciliation report to file (.json for machine-readable output)')
@click.option('--fail-on-warnings', is_flag=True, help='Return non-zero exit code if warnings are found')
def reconcile(mapping, output, fail_on_warnings):
    """Reconcile mapping document with database schema."""
    logger = setup_logger('cm3-batch', log_to_file=False)

    try:
        import json
        from src.database.connection import OracleConnection
        from src.database.reconciliation import SchemaReconciler
        from src.config.loader import ConfigLoader
        from src.config.mapping_parser import MappingParser

        # Load mapping
        loader = ConfigLoader()
        mapping_dict = loader.load_mapping(mapping)

        parser = MappingParser()
        mapping_doc = parser.parse(mapping_dict)

        # Reconcile with database
        conn = OracleConnection.from_env()
        reconciler = SchemaReconciler(conn)

        click.echo(f"\nReconciling mapping: {mapping_doc.mapping_name}")
        click.echo(f"Target table: {mapping_doc.target.get('table_name', 'N/A')}")

        result = reconciler.reconcile_mapping(mapping_doc)

        if result['valid']:
            click.echo(click.style('\n✓ Mapping is valid', fg='green'))
        else:
            click.echo(click.style('\n✗ Mapping validation failed', fg='red'))
            for error in result['errors']:
                click.echo(click.style(f"  ERROR: {error}", fg='red'))

        if result['warnings']:
            click.echo(click.style('\nWarnings:', fg='yellow'))
            for warning in result['warnings']:
                click.echo(click.style(f"  {warning}", fg='yellow'))

        click.echo(f"\nMapped columns: {result.get('mapped_columns', 0)}")
        click.echo(f"Database columns: {result.get('database_columns', 0)}")

        if output:
            if output.lower().endswith('.json'):
                with open(output, 'w') as f:
                    json.dump(result, f, indent=2)
            else:
                report = reconciler.generate_reconciliation_report(mapping_doc)
                with open(output, 'w') as f:
                    f.write(report)
            click.echo(f"Report written to: {output}")

        if (not result['valid']) or (fail_on_warnings and result['warnings']):
            sys.exit(1)

    except Exception as e:
        logger.error(f"Error reconciling mapping: {e}")
        sys.exit(1)


@cli.command('reconcile-all')
@click.option('--mappings-dir', '-d', default='config/mappings', type=click.Path(exists=True, file_okay=False),
              help='Directory containing mapping files (default: config/mappings)')
@click.option('--pattern', default='*.json', help='Glob pattern for mapping files (default: *.json)')
@click.option('--output', '-o', help='Write aggregate reconciliation report (.json recommended)')
@click.option('--baseline', '-b', type=click.Path(exists=True),
              help='Baseline reconcile-all JSON report to compare drift against')
@click.option('--fail-on-warnings', is_flag=True, help='Return non-zero exit code if warnings are found')
@click.option('--fail-on-drift', is_flag=True, help='Return non-zero exit code if new errors/warnings appear vs baseline')
def reconcile_all(mappings_dir, pattern, output, baseline, fail_on_warnings, fail_on_drift):
    """Reconcile all mapping documents in a directory against database schema."""
    logger = setup_logger('cm3-batch', log_to_file=False)

    try:
        import json
        from pathlib import Path
        from src.database.connection import OracleConnection
        from src.database.reconciliation import SchemaReconciler
        from src.config.loader import ConfigLoader
        from src.config.mapping_parser import MappingParser

        loader = ConfigLoader()
        parser = MappingParser()
        conn = OracleConnection.from_env()
        reconciler = SchemaReconciler(conn)

        mapping_files = sorted(Path(mappings_dir).glob(pattern))
        if not mapping_files:
            click.echo(click.style(f"No mapping files found in {mappings_dir} matching '{pattern}'", fg='yellow'))
            return

        results = []
        total_errors = 0
        total_warnings = 0
        invalid_mappings = 0

        for mapping_file in mapping_files:
            click.echo(f"\nReconciling: {mapping_file}")
            try:
                mapping_dict = loader.load_mapping(str(mapping_file))
                mapping_doc = parser.parse(mapping_dict)
                result = reconciler.reconcile_mapping(mapping_doc)

                errors = result.get('error_count', len(result.get('errors', [])))
                warnings = result.get('warning_count', len(result.get('warnings', [])))
                total_errors += errors
                total_warnings += warnings

                if not result.get('valid', False):
                    invalid_mappings += 1
                    click.echo(click.style(f"  ✗ INVALID ({errors} errors, {warnings} warnings)", fg='red'))
                else:
                    status_color = 'yellow' if warnings else 'green'
                    status_text = f"  ✓ VALID ({warnings} warnings)" if warnings else "  ✓ VALID"
                    click.echo(click.style(status_text, fg=status_color))

                results.append({
                    'mapping_file': str(mapping_file),
                    'mapping_name': mapping_doc.mapping_name,
                    **result,
                })

            except Exception as file_error:
                invalid_mappings += 1
                total_errors += 1
                click.echo(click.style(f"  ✗ FAILED to process: {file_error}", fg='red'))
                results.append({
                    'mapping_file': str(mapping_file),
                    'valid': False,
                    'errors': [f"Failed to process mapping: {file_error}"],
                    'warnings': [],
                    'error_count': 1,
                    'warning_count': 0,
                })

        summary = {
            'total_mappings': len(mapping_files),
            'valid_mappings': len(mapping_files) - invalid_mappings,
            'invalid_mappings': invalid_mappings,
            'total_errors': total_errors,
            'total_warnings': total_warnings,
            'results': results,
        }

        click.echo("\n" + "=" * 60)
        click.echo("RECONCILE-ALL SUMMARY")
        click.echo("=" * 60)
        click.echo(f"Total mappings:  {summary['total_mappings']}")
        click.echo(f"Valid mappings:  {summary['valid_mappings']}")
        click.echo(f"Invalid mappings:{summary['invalid_mappings']}")
        click.echo(f"Total errors:    {summary['total_errors']}")
        click.echo(f"Total warnings:  {summary['total_warnings']}")

        drift = None
        if baseline:
            with open(baseline, 'r') as f:
                baseline_report = json.load(f)

            baseline_results = {
                r.get('mapping_file'): r
                for r in baseline_report.get('results', [])
                if r.get('mapping_file')
            }
            current_results = {
                r.get('mapping_file'): r
                for r in results
                if r.get('mapping_file')
            }

            baseline_files = set(baseline_results.keys())
            current_files = set(current_results.keys())

            added_files = sorted(current_files - baseline_files)
            removed_files = sorted(baseline_files - current_files)

            changed = []
            new_errors = 0
            new_warnings = 0

            for mf in sorted(current_files & baseline_files):
                old = baseline_results[mf]
                new = current_results[mf]
                old_e = old.get('error_count', len(old.get('errors', [])))
                old_w = old.get('warning_count', len(old.get('warnings', [])))
                new_e = new.get('error_count', len(new.get('errors', [])))
                new_w = new.get('warning_count', len(new.get('warnings', [])))

                delta_e = new_e - old_e
                delta_w = new_w - old_w
                if delta_e != 0 or delta_w != 0:
                    changed.append({
                        'mapping_file': mf,
                        'old_errors': old_e,
                        'new_errors': new_e,
                        'delta_errors': delta_e,
                        'old_warnings': old_w,
                        'new_warnings': new_w,
                        'delta_warnings': delta_w,
                    })
                    if delta_e > 0:
                        new_errors += delta_e
                    if delta_w > 0:
                        new_warnings += delta_w

            drift = {
                'baseline': baseline,
                'added_files': added_files,
                'removed_files': removed_files,
                'changed': changed,
                'new_errors': new_errors,
                'new_warnings': new_warnings,
            }

            click.echo("\nDRIFT SUMMARY")
            click.echo("-" * 60)
            click.echo(f"Added mappings:   {len(added_files)}")
            click.echo(f"Removed mappings: {len(removed_files)}")
            click.echo(f"Changed mappings: {len(changed)}")
            click.echo(f"New errors:       {new_errors}")
            click.echo(f"New warnings:     {new_warnings}")

        if drift is not None:
            summary['drift'] = drift

        if output:
            if output.lower().endswith('.json'):
                with open(output, 'w') as f:
                    json.dump(summary, f, indent=2)
            else:
                with open(output, 'w') as f:
                    f.write(json.dumps(summary, indent=2))
            click.echo(f"\nAggregate report written to: {output}")

        has_drift_regression = bool(drift and (drift.get('new_errors', 0) > 0 or drift.get('new_warnings', 0) > 0))

        if (
            summary['invalid_mappings'] > 0
            or (fail_on_warnings and summary['total_warnings'] > 0)
            or (fail_on_drift and has_drift_regression)
        ):
            sys.exit(1)

    except Exception as e:
        logger.error(f"Error reconciling mappings directory: {e}")
        sys.exit(1)


@cli.command()
@click.option('--table', '-t', help='Table name to extract')
@click.option('--query', '-q', help='SQL query to execute')
@click.option('--sql-file', '-s', type=click.Path(exists=True), help='Path to SQL file')
@click.option('--output', '-o', required=True, help='Output file path')
@click.option('--limit', '-l', type=int, help='Limit number of rows (only for --table)')
@click.option('--delimiter', '-d', default='|', help='Output delimiter (default: |)')
def extract(table, query, sql_file, output, limit, delimiter):
    """Extract data from Oracle database to file.
    
    Supports three modes:
    1. Table extraction: --table TABLENAME
    2. Direct query: --query "SELECT ..."
    3. SQL file: --sql-file path/to/query.sql
    """
    logger = setup_logger('cm3-batch', log_to_file=False)
    
    # Validate input options
    options_provided = sum([bool(table), bool(query), bool(sql_file)])
    if options_provided == 0:
        click.echo(click.style('Error: Must provide one of --table, --query, or --sql-file', fg='red'))
        sys.exit(1)
    elif options_provided > 1:
        click.echo(click.style('Error: Only one of --table, --query, or --sql-file can be specified', fg='red'))
        sys.exit(1)
    
    try:
        from src.database.connection import OracleConnection
        from src.database.extractor import DataExtractor
        
        conn = OracleConnection.from_env()
        extractor = DataExtractor(conn)
        
        # Determine extraction mode
        if sql_file:
            # Read SQL from file
            with open(sql_file, 'r') as f:
                sql_query = f.read().strip()
            click.echo(f"\nExecuting SQL from file: {sql_file}")
            stats = extractor.extract_to_file(output_file=output, query=sql_query, delimiter=delimiter)
            click.echo(f"Extracted {stats['total_rows']} rows to {output}")
            click.echo(f"Chunks written: {stats['chunks_written']}")
            
        elif query:
            # Use provided SQL query
            click.echo(f"\nExecuting custom query")
            stats = extractor.extract_to_file(output_file=output, query=query, delimiter=delimiter)
            click.echo(f"Extracted {stats['total_rows']} rows to {output}")
            click.echo(f"Chunks written: {stats['chunks_written']}")
            
        else:
            # Table extraction
            click.echo(f"\nExtracting from table: {table}")
            
            if limit:
                df = extractor.extract_table(table, limit=limit)
                df.to_csv(output, sep=delimiter, index=False, header=False)
                click.echo(f"Extracted {len(df)} rows to {output}")
            else:
                stats = extractor.extract_to_file(table_name=table, output_file=output, delimiter=delimiter)
                click.echo(f"Extracted {stats['total_rows']} rows to {output}")
                click.echo(f"Chunks written: {stats['chunks_written']}")
        
        click.echo(click.style('✓ Extraction complete', fg='green'))
        
    except Exception as e:
        logger.error(f"Error extracting data: {e}")
        sys.exit(1)


@cli.command('generate-oracle-expected')
@click.option('--manifest', 'manifest_path', required=True, type=click.Path(exists=True),
              help='Oracle expected-generation manifest JSON')
@click.option('--dry-run/--run', default=True,
              help='Dry-run by default. Use --run to execute Oracle extraction jobs')
@click.option('--output', '-o', help='Optional output JSON summary file')
def generate_oracle_expected(manifest_path, dry_run, output):
    """Generate expected target files from Oracle transformation SQL (cm3int)."""
    logger = setup_logger('cm3-batch', log_to_file=False)
    try:
        import json
        from src.pipeline.oracle_expected_generator import load_oracle_manifest, generate_expected_from_oracle

        manifest = load_oracle_manifest(manifest_path)
        summary = generate_expected_from_oracle(manifest, dry_run=dry_run)

        click.echo(f"Status: {summary.get('status')}")
        for j in summary.get('jobs', []):
            click.echo(f"- {j.get('name')}: {j.get('status')}")

        if output:
            with open(output, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2)
            click.echo(f"\n✓ Oracle expected summary written: {output}")

        if summary.get('status') == 'failed':
            sys.exit(1)
    except Exception as e:
        logger.error(f"Error generating oracle expected files: {e}")
        sys.exit(1)


@cli.command('run-pipeline')
@click.option('--config', 'config_path', required=True, type=click.Path(exists=True),
              help='Source-system pipeline profile JSON')
@click.option('--dry-run/--run', default=True,
              help='Dry-run by default. Use --run to execute configured stage commands')
@click.option('--output', '-o', help='Optional output JSON summary file')
@click.option('--summary-md', help='Optional output Markdown summary file')
def run_pipeline(config_path, dry_run, output, summary_md):
    """Run source-system orchestration profile (scaffold)."""
    logger = setup_logger('cm3-batch', log_to_file=False)
    try:
        from src.pipeline.runner import PipelineRunner
        from src.pipeline.run_summary_reporter import write_pipeline_summary_json, write_pipeline_summary_markdown

        runner = PipelineRunner(config_path)
        summary = runner.run(dry_run=dry_run)

        click.echo(f"Source: {summary.get('source_system')}")
        click.echo(f"Status: {summary.get('status')}")
        for step in summary.get('steps', []):
            click.echo(f"- {step.get('name')}: {step.get('status')} ({step.get('message', '')})")

        if output:
            write_pipeline_summary_json(summary, output)
            click.echo(f"\n✓ Pipeline summary written: {output}")

        if summary_md:
            write_pipeline_summary_markdown(summary, summary_md)
            click.echo(f"✓ Pipeline Markdown summary written: {summary_md}")

        if summary.get('status') == 'failed':
            sys.exit(1)
    except Exception as e:
        logger.error(f"Error running pipeline profile: {e}")
        sys.exit(1)


@cli.command('gx-checkpoint1')
@click.option('--targets', '-t', 'targets_csv', type=click.Path(exists=True), required=True,
              help='CSV file listing data targets (BA-friendly).')
@click.option('--expectations', '-e', 'expectations_csv', type=click.Path(exists=True), required=True,
              help='CSV file listing expectations (BA-friendly).')
@click.option('--output', '-o', 'output_json', type=click.Path(),
              help='Optional path to write run summary JSON.')
@click.option('--csv-output', type=click.Path(),
              help='Optional path to write flattened expectation results CSV.')
@click.option('--html-output', type=click.Path(),
              help='Optional path to write human-readable HTML summary.')
@click.option('--data-docs-dir', type=click.Path(),
              help='Optional Great Expectations data docs directory.')
def gx_checkpoint1(targets_csv, expectations_csv, output_json, csv_output, html_output, data_docs_dir):
    """Run Great Expectations Checkpoint 1 (schema/null/uniqueness/allowed values/range/row-count)."""
    logger = setup_logger('cm3-batch', log_to_file=False)

    try:
        from src.quality.gx_checkpoint1 import run_checkpoint_1

        summary = run_checkpoint_1(
            targets_csv=targets_csv,
            expectations_csv=expectations_csv,
            output_json=output_json,
            csv_output=csv_output,
            html_output=html_output,
            data_docs_dir=data_docs_dir,
        )

        if summary.get('success'):
            click.echo(click.style('✓ Great Expectations Checkpoint 1 passed', fg='green'))
        else:
            click.echo(click.style('✗ Great Expectations Checkpoint 1 failed', fg='red'))
            sys.exit(1)

        click.echo(f"Targets run: {summary.get('targets_run', 0)}")
        if output_json:
            click.echo(f"JSON summary written to: {output_json}")
        if csv_output:
            click.echo(f"CSV summary written to: {csv_output}")
        if html_output:
            click.echo(f"HTML summary written to: {html_output}")

    except Exception as e:
        logger.error(f"Error running Great Expectations Checkpoint 1: {e}")
        sys.exit(1)


@cli.command('convert-suite')
@click.option('--input', 'input_path', required=False, default=None,
              type=click.Path(), help='Path to Excel test suite file to convert')
@click.option('--output-dir', default='.', show_default=True,
              type=click.Path(), help='Directory to write the generated YAML file')
@click.option('--template', 'template_path', default=None,
              type=click.Path(), help='Write an empty Excel template to this path and exit')
def convert_suite(input_path, output_dir, template_path):
    """Convert an Excel test suite template to a YAML file for cm3-batch run-tests."""
    logger = setup_logger('cm3-batch', log_to_file=False)

    try:
        from src.config.suite_template_converter import SuiteTemplateConverter

        converter = SuiteTemplateConverter()

        if template_path:
            converter.create_template(template_path)
            click.echo(f"Template written to: {template_path}")
            return

        if not input_path:
            click.echo(click.style('Error: --input is required when --template is not specified', fg='red'))
            sys.exit(1)

        output_path = converter.convert(input_path, output_dir)
        click.echo(f"YAML test suite written to: {output_path}")

    except Exception as e:
        logger.error(f"Error converting test suite: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


@cli.command('watch')
@click.option('--dir', 'watch_dir', required=True, type=click.Path(),
              help='Directory to watch for .trigger files')
@click.option('--suites', 'suites_dir', required=True, type=click.Path(),
              help='Directory containing suite YAML files')
@click.option('--env', default='dev', show_default=True, help='Environment name')
@click.option('--output-dir', default='reports', show_default=True, type=click.Path(),
              help='Directory for test reports')
@click.option('--interval', default=30, show_default=True, type=int,
              help='Poll interval in seconds')
def watch(watch_dir, suites_dir, env, output_dir, interval):
    """Watch a directory for batch trigger files and run matching test suites."""
    from src.commands.watch_command import run_watch
    run_watch(watch_dir, suites_dir, env, output_dir, poll_interval=interval)


@cli.command('run-tests')
@click.option('--suite', '-s', required=True, help='Path to test suite YAML file')
@click.option('--params', '-p', default='', help='Parameters as key=value,key2=value2')
@click.option('--env', default='dev', help='Environment name (appears in reports)')
@click.option('--output-dir', '-o', default='reports', help='Directory for output reports')
@click.option('--dry-run', is_flag=True, help='Print resolved config without running tests')
def run_tests(suite, params, env, output_dir, dry_run):
    """Run a complete test suite defined in a YAML file."""
    from src.commands.run_tests_command import run_tests_command

    try:
        results = run_tests_command(
            suite_path=suite,
            params_str=params,
            env=env,
            output_dir=output_dir,
            dry_run=dry_run,
        )
    except Exception as e:
        click.echo(click.style(f"Error: {e}", fg='red'), err=True)
        sys.exit(1)

    if dry_run:
        return

    # Determine suite name from YAML (reload header for display).
    import yaml
    with open(suite, 'r') as f:
        raw = yaml.safe_load(f)
    suite_name = raw.get('name', suite)
    suite_env = env.upper()

    total = len(results)
    passed = sum(1 for r in results if r['status'] == 'PASS')
    failed = total - passed
    overall = 'PASSED' if failed == 0 else 'FAILED'

    click.echo(
        f"\nTest Suite: {suite_name} | Environment: {suite_env} | "
        f"Result: {overall} ({passed}/{total} passed)\n"
    )

    # Header row.
    click.echo(f"  {'Test':<32}{'Status':<10}{'Rows':>10}  {'Errors':>7}  {'Duration':>9}")

    for r in results:
        name = r['name'][:31]
        status = r['status']
        rows = f"{r['total_rows']:,}" if r['total_rows'] else '-'
        errors = str(r['error_count'])
        duration = f"{r['duration_seconds']}s"
        click.echo(f"  {name:<32}{status:<10}{rows:>10}  {errors:>7}  {duration:>9}")

    if results and failed > 0:
        sys.exit(1)


@cli.command('list-runs')
@click.option('--limit', default=20, show_default=True, type=int,
              help='Maximum number of runs to show (most recent first)')
def list_runs(limit):
    """List archived test suite runs (most recent first)."""
    from src.utils.archive import ArchiveManager

    archive = ArchiveManager()
    archive.purge_old_runs()
    runs = archive.list_runs()[:limit]

    if not runs:
        click.echo("No archived runs found.")
        return

    click.echo(f"{'RUN ID':<38} {'SUITE':<28} {'ENV':<8} {'TIMESTAMP':<22} STATUS")
    click.echo("-" * 106)
    for r in runs:
        click.echo(
            f"{r.get('run_id', ''):<38} "
            f"{r.get('suite_name', '')[:27]:<28} "
            f"{r.get('environment', ''):<8} "
            f"{r.get('timestamp', ''):<22} "
            f"{r.get('status', 'unknown')}"
        )


@cli.command('get-run')
@click.argument('run_id')
def get_run(run_id):
    """Retrieve archived files and manifest for a specific run."""
    from src.utils.archive import ArchiveManager

    archive = ArchiveManager()
    result = archive.get_run(run_id)

    if result is None:
        click.echo(click.style(f"Run '{run_id}' not found in archive.", fg='red'), err=True)
        raise SystemExit(1)

    click.echo("Manifest:")
    click.echo(json.dumps(result['manifest'], indent=2))
    click.echo("\nFiles:")
    for f in result['files']:
        click.echo(f"  {f}")


def main():
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
