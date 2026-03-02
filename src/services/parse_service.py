from __future__ import annotations

from pathlib import Path
from typing import Any

from src.config.universal_mapping_parser import UniversalMappingParser
from src.parsers.fixed_width_parser import FixedWidthParser
from src.parsers.pipe_delimited_parser import PipeDelimitedParser


def run_parse_service(file_path: str, mapping_path: str, output_dir: str) -> dict[str, Any]:
    """Shared parse workflow used by API/CLI surfaces.

    Args:
        file_path: Absolute or repo-relative path to the data file.
        mapping_path: Absolute or repo-relative path to the mapping JSON.
        output_dir: Directory where the parsed CSV output is written.

    Returns:
        Dict with keys: rows_parsed, columns, preview (first 10 rows), output_file.

    Raises:
        FileNotFoundError: If mapping_path does not exist.
    """
    mapping_file = Path(mapping_path)
    if not mapping_file.exists():
        raise FileNotFoundError(f"Mapping not found: {mapping_path}")

    parser_obj = UniversalMappingParser(mapping_path=str(mapping_file))

    if parser_obj.get_format() == "fixed_width":
        parser = FixedWidthParser(file_path, parser_obj.get_field_positions())
    else:
        parser = PipeDelimitedParser(file_path)

    df = parser.parse()
    # Normalize columns to string keys for API contracts.
    df.columns = [str(c) for c in df.columns]

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    output_file = out_dir / f"parsed_{Path(file_path).name}.csv"
    df.to_csv(output_file, index=False)

    return {
        "rows_parsed": len(df),
        "columns": len(df.columns),
        "preview": df.head(10).to_dict("records"),
        "output_file": str(output_file),
    }
