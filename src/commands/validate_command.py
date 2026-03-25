from __future__ import annotations

import json
import sys
from pathlib import Path
import click


def _build_fixed_width_field_specs(fields):
    """Build (name, start, end) specs from mapping fields with validation."""
    field_specs = []
    current_pos = 0

    for idx, field in enumerate(fields or []):
        field_name = field.get('name') or f"field_{idx + 1}"

        if 'length' not in field or field.get('length') in (None, ''):
            raise click.ClickException(
                f"Invalid mapping: fixed-width field '{field_name}' is missing required 'length'."
            )

        try:
            field_length = int(field.get('length'))
        except Exception:
            raise click.ClickException(
                f"Invalid mapping: fixed-width field '{field_name}' has non-numeric length: {field.get('length')}"
            )

        if field_length <= 0:
            raise click.ClickException(
                f"Invalid mapping: fixed-width field '{field_name}' has non-positive length: {field_length}"
            )

        if field.get('position') is not None and field.get('position') != '':
            try:
                start = int(field.get('position')) - 1
            except Exception:
                raise click.ClickException(
                    f"Invalid mapping: fixed-width field '{field_name}' has non-numeric position: {field.get('position')}"
                )
        else:
            start = current_pos

        if start < 0:
            raise click.ClickException(
                f"Invalid mapping: fixed-width field '{field_name}' resolves to negative start offset ({start})."
            )

        end = start + field_length
        field_specs.append((field_name, start, end))
        current_pos = end

    return field_specs


def _json_default(obj):
    try:
        import numpy as np
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
    except Exception:
        pass
    return str(obj)


def run_validate_command(
    file,
    mapping,
    rules,
    output,
    detailed,
    use_chunked,
    chunk_size,
    progress,
    strict_fixed_width=False,
    strict_level='format',
    workers=1,
    logger=None,
    suppress_pii=True,
):
    from src.parsers.format_detector import FormatDetector
    from src.parsers.enhanced_validator import EnhancedFileValidator
    from src.parsers.fixed_width_parser import FixedWidthParser
    from src.parsers.chunked_validator import ChunkedFileValidator
    from src.parsers.chunked_parser import ChunkedFixedWidthParser
    from src.reports.renderers.validation_renderer import ValidationReporter
    from src.reports.adapters.result_adapter_chunked import adapt_chunked_validation_result

    mapping_config = None
    if mapping:
        with open(mapping, 'r') as f:
            mapping_config = json.load(f)
        mapping_config['file_path'] = mapping

    # Chunked path
    if use_chunked:
        if mapping_config and mapping_config.get('fields'):
            parser_class = FixedWidthParser
        else:
            detector = FormatDetector()
            try:
                parser_class = detector.get_parser_class(file)
            except Exception:
                if mapping_config and mapping_config.get('fields'):
                    parser_class = FixedWidthParser
                else:
                    raise

        chunk_parser = None
        delimiter = '|'
        if parser_class == FixedWidthParser and mapping_config and 'fields' in mapping_config:
            field_specs = _build_fixed_width_field_specs(mapping_config.get('fields', []))
            chunk_parser = ChunkedFixedWidthParser(file, field_specs, chunk_size=chunk_size)
        elif parser_class == FixedWidthParser and mapping_config and 'mappings' in mapping_config:
            click.echo(click.style('Chunked fixed-width validation requires mapping with fields/position/length metadata.', fg='red'))
            sys.exit(1)

        expected_row_length = None
        if mapping_config and mapping_config.get('fields'):
            try:
                expected_row_length = sum(int(f.get('length', 0)) for f in mapping_config.get('fields', []))
            except Exception:
                expected_row_length = None

        strict_fields = mapping_config.get('fields', []) if (mapping_config and mapping_config.get('fields')) else []

        chunked_validator = ChunkedFileValidator(
            file_path=file,
            delimiter=delimiter,
            chunk_size=chunk_size,
            parser=chunk_parser,
            rules_config_path=rules,
            expected_row_length=expected_row_length,
            strict_fixed_width=bool(strict_fixed_width),
            strict_level=str(strict_level or 'format'),
            strict_fields=strict_fields,
            workers=int(workers or 1),
        )

        if mapping_config:
            if 'fields' in mapping_config:
                expected_columns = [f['name'] for f in mapping_config['fields']]
                required_columns = [f['name'] for f in mapping_config['fields'] if f.get('required', False)]
            elif 'mappings' in mapping_config:
                expected_columns = [m['source_column'] for m in mapping_config['mappings']]
                required_columns = [m['source_column'] for m in mapping_config['mappings'] if m.get('required', False)]
            else:
                expected_columns = []
                required_columns = []

            if expected_columns:
                result = chunked_validator.validate_with_schema(
                    expected_columns=expected_columns,
                    required_columns=required_columns if required_columns else expected_columns,
                    show_progress=progress,
                )
            else:
                result = chunked_validator.validate(show_progress=progress)
        else:
            result = chunked_validator.validate(show_progress=progress)

        if result['valid']:
            click.echo(click.style('✓ File is valid (chunked)', fg='green'))
        else:
            click.echo(click.style('✗ File validation failed (chunked)', fg='red'))

        click.echo(f"\nTotal Rows: {result.get('total_rows', 0):,}")

        if result.get('errors'):
            click.echo(click.style(f"\nErrors ({len(result['errors'])}):", fg='red'))
            for error in result['errors'][:5]:
                msg = error.get('message', str(error)) if isinstance(error, dict) else str(error)
                click.echo(click.style(f"  • {msg}", fg='red'))

        if result.get('warnings'):
            click.echo(click.style(f"\nWarnings ({len(result['warnings'])}):", fg='yellow'))
            for warning in result['warnings'][:5]:
                msg = warning.get('message', str(warning)) if isinstance(warning, dict) else str(warning)
                click.echo(click.style(f"  • {msg}", fg='yellow'))

        if output:
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            if output.lower().endswith('.json'):
                with open(output, 'w') as f:
                    json.dump(result, f, indent=2, default=_json_default)
                click.echo(f"\n✓ Chunked validation JSON report generated: {output}")
            elif output.lower().endswith('.html') or output.lower().endswith('.htm'):
                reporter = ValidationReporter()
                adapted = adapt_chunked_validation_result(result, file_path=file, mapping=mapping)
                reporter.generate(adapted, output, suppress_pii=suppress_pii)
                click.echo(f"\n✓ Chunked validation HTML report generated: {output}")
            else:
                click.echo(click.style("\nUnsupported output type for chunked validation. Use .json or .html", fg='yellow'))

        if not result['valid']:
            sys.exit(1)
        return

    # Non-chunked path
    detector = FormatDetector()
    try:
        parser_class = detector.get_parser_class(file)
    except Exception:
        if mapping_config and mapping_config.get('fields'):
            parser_class = FixedWidthParser
        else:
            raise

    if mapping_config and parser_class == FixedWidthParser:
        field_specs = _build_fixed_width_field_specs(mapping_config.get('fields', []))
        parser = FixedWidthParser(file, field_specs)
    else:
        parser = parser_class(file)

    validator = EnhancedFileValidator(parser, mapping_config, rules)
    result = validator.validate(
        detailed=detailed,
        strict_fixed_width=strict_fixed_width,
        strict_level=strict_level,
    )

    if result['valid']:
        click.echo(click.style('✓ File is valid', fg='green'))
    else:
        click.echo(click.style('✗ File validation failed', fg='red'))

    quality_score = result.get('quality_metrics', {}).get('quality_score', 0)
    click.echo(f"\nData Quality Score: {quality_score}%")

    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        if output.lower().endswith('.json'):
            with open(output, 'w') as f:
                json.dump(result, f, indent=2, default=_json_default)
            click.echo(f"\n✓ Validation JSON report generated: {output}")
        elif output.lower().endswith('.html') or output.lower().endswith('.htm'):
            reporter = ValidationReporter()
            reporter.generate(result, output, suppress_pii=suppress_pii)
            click.echo(f"\n✓ Validation HTML report generated: {output}")
        else:
            click.echo(click.style("\nUnsupported output type for validation. Use .json or .html", fg='yellow'))

    if not result['valid']:
        sys.exit(1)
