from __future__ import annotations

import sys
from pathlib import Path
import click


def run_parse_command(file, mapping, format, output, use_chunked, chunk_size, logger):
    """Parse file and display contents."""
    try:
        from src.parsers.format_detector import FormatDetector
        from src.parsers.pipe_delimited_parser import PipeDelimitedParser
        from src.parsers.fixed_width_parser import FixedWidthParser
        from src.parsers.chunked_parser import ChunkedFileParser, ChunkedFixedWidthParser
        import json

        mapping_config = None
        field_specs = []

        # If mapping provided, build field specs (fixed-width)
        if mapping:
            with open(mapping, 'r') as f:
                mapping_config = json.load(f)

            current_pos = 0
            for field in mapping_config.get('fields', []):
                field_name = field['name']
                field_length = field['length']
                field_specs.append((field_name, current_pos, current_pos + field_length))
                current_pos += field_length

        # Chunked path
        if use_chunked:
            if mapping and field_specs:
                parser = ChunkedFixedWidthParser(file, field_specs, chunk_size=chunk_size)
            else:
                delimiter = '|'
                if format == 'pipe':
                    delimiter = '|'
                elif format in ('csv', 'comma'):
                    delimiter = ','
                parser = ChunkedFileParser(file, delimiter=delimiter, chunk_size=chunk_size)

            total_rows = 0
            preview_df = None

            if output:
                Path(output).parent.mkdir(parents=True, exist_ok=True)
                first_chunk = True
                for chunk in parser.parse_chunks():
                    if preview_df is None:
                        preview_df = chunk.head(10)
                    chunk.to_csv(output, index=False, mode='w' if first_chunk else 'a', header=first_chunk)
                    first_chunk = False
                    total_rows += len(chunk)
                click.echo(f"Output written to: {output}")
                click.echo(f"Total rows: {total_rows}")
            else:
                for chunk in parser.parse_chunks():
                    if preview_df is None:
                        preview_df = chunk.head(10)
                    total_rows += len(chunk)

                if preview_df is not None:
                    click.echo(preview_df.to_string())
                click.echo(f"\nTotal rows: {total_rows}")

            return

        # Non-chunked path (existing behavior)
        if mapping and field_specs:
            parser = FixedWidthParser(file, field_specs)
        else:
            if not format:
                detector = FormatDetector()
                parser_class = detector.get_parser_class(file)
                parser = parser_class(file)
            else:
                if format == 'pipe':
                    parser = PipeDelimitedParser(file)
                elif format == 'fixed':
                    parser = FixedWidthParser(file, [])
                else:
                    click.echo(f"Unknown format: {format}")
                    sys.exit(1)

        df = parser.parse()
        logger.info(f"Parsed {len(df)} rows, {len(df.columns)} columns")

        if output:
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(output, index=False)
            click.echo(f"Output written to: {output}")
        else:
            click.echo(df.head(10).to_string())
            click.echo(f"\nTotal rows: {len(df)}")

    except Exception as e:
        logger.error(f"Error parsing file: {e}")
        sys.exit(1)

