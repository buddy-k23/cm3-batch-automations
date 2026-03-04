from __future__ import annotations

import json
from typing import Any, List

import pandas as pd

from src.comparators.chunked_comparator import ChunkedFileComparator
from src.comparators.file_comparator import FileComparator
from src.parsers.fixed_width_parser import FixedWidthParser
from src.parsers.format_detector import FormatDetector


def _build_fixed_width_specs(cfg: dict[str, Any]) -> list[tuple[str, int, int]]:
    field_specs = []
    current_pos = 0
    for field in cfg.get('fields', []):
        name = field['name']
        length = int(field['length'])
        if field.get('position') is not None:
            start = int(field['position']) - 1
        else:
            start = current_pos
        end = start + length
        field_specs.append((name, start, end))
        current_pos = end
    return field_specs


def _check_structure_compatibility(
    df1: pd.DataFrame,
    df2: pd.DataFrame,
    mapping_config: dict[str, Any] | None = None,
) -> List[dict[str, Any]]:
    """Check that two DataFrames are structurally compatible for comparison.

    Checks are performed in order and short-circuit on the first blocking issue:

    1. Column count mismatch — returns immediately with a single error.
    2. Missing column names — reports columns present in one file but absent in the other.
    3. Column order mismatch — only when ``mapping_config`` supplies a ``fields`` list.

    Args:
        df1: Parsed DataFrame for the first file.
        df2: Parsed DataFrame for the second file.
        mapping_config: Optional mapping dict containing a ``fields`` key whose
            items each have a ``name`` entry that defines the expected column order.

    Returns:
        A list of error dicts.  An empty list means the files are compatible.
        Each dict contains at minimum a ``"type"`` key; additional keys depend on
        the error type:

        - ``column_count_mismatch``: ``file1_count``, ``file2_count``
        - ``missing_columns``: ``columns`` (list of names), ``in_file`` (``"file1"`` or ``"file2"``)
        - ``column_order_mismatch``: ``expected_columns``, ``file1_columns``, ``file2_columns``
    """
    cols1 = list(df1.columns)
    cols2 = list(df2.columns)

    # 1. Column count mismatch — short-circuit immediately.
    if len(cols1) != len(cols2):
        return [{"type": "column_count_mismatch", "file1_count": len(cols1), "file2_count": len(cols2)}]

    errors: List[dict[str, Any]] = []

    # 2. Missing column names.
    set1, set2 = set(cols1), set(cols2)
    missing_in_file2 = sorted(set1 - set2)
    missing_in_file1 = sorted(set2 - set1)
    if missing_in_file2:
        errors.append({"type": "missing_columns", "columns": missing_in_file2, "in_file": "file2"})
    if missing_in_file1:
        errors.append({"type": "missing_columns", "columns": missing_in_file1, "in_file": "file1"})

    if errors:
        return errors

    # 3. Column order mismatch — only when the mapping supplies an ordered fields list
    #    AND the files' columns are the same named columns as the mapping expects
    #    (i.e. the names match but the sequence differs).  When the files carry
    #    unnamed/integer columns the files have not yet been renamed, so there is
    #    nothing meaningful to check against the mapping order.
    if mapping_config and mapping_config.get("fields"):
        expected = [f["name"] for f in mapping_config["fields"] if "name" in f]
        expected_set = set(expected)
        if expected and set(cols1) == expected_set and (cols1 != expected or cols2 != expected):
            errors.append({
                "type": "column_order_mismatch",
                "expected_columns": expected,
                "file1_columns": cols1,
                "file2_columns": cols2,
            })

    return errors


def run_compare_service(
    file1: str,
    file2: str,
    keys: str | None = None,
    mapping: str | None = None,
    detailed: bool = True,
    chunk_size: int = 100000,
    progress: bool = False,
    use_chunked: bool = False,
) -> dict[str, Any]:
    """Shared compare workflow used by CLI and API."""
    key_columns = [k.strip() for k in keys.split(',')] if keys else None

    if use_chunked:
        if not key_columns:
            raise ValueError("Row-by-row comparison is not supported with chunked processing; provide keys.")

        delimiter = ','
        if file1.endswith('.txt') or file1.endswith('.dat'):
            delimiter = '|'

        comparator = ChunkedFileComparator(
            file1, file2, key_columns,
            delimiter=delimiter,
            chunk_size=chunk_size,
        )
        return comparator.compare(detailed=detailed, show_progress=progress)

    detector = FormatDetector()
    mapping_config = None
    if mapping:
        with open(mapping, 'r', encoding='utf-8') as f:
            mapping_config = json.load(f)

    parser1_class = detector.get_parser_class(file1)
    if parser1_class == FixedWidthParser:
        if not (mapping_config and mapping_config.get('fields')):
            raise ValueError("fixed-width compare requires mapping with fields/length metadata")
        parser1 = FixedWidthParser(file1, _build_fixed_width_specs(mapping_config))
    else:
        parser1 = parser1_class(file1)

    parser2_class = detector.get_parser_class(file2)
    if parser2_class == FixedWidthParser:
        if not (mapping_config and mapping_config.get('fields')):
            raise ValueError("fixed-width compare requires mapping with fields/length metadata")
        parser2 = FixedWidthParser(file2, _build_fixed_width_specs(mapping_config))
    else:
        parser2 = parser2_class(file2)

    df1 = parser1.parse()
    df2 = parser2.parse()

    # Phase 1: structure compatibility check
    structure_errors = _check_structure_compatibility(df1, df2, mapping_config)
    if structure_errors:
        return {
            "structure_compatible": False,
            "structure_errors": structure_errors,
            "total_rows_file1": len(df1),
            "total_rows_file2": len(df2),
            "matching_rows": 0,
            "only_in_file1": 0,
            "only_in_file2": 0,
            "differences": 0,
            "valid": False,
        }

    if key_columns and any(k not in df1.columns for k in key_columns):
        try:
            import pandas as pd

            # Header-derived fallback for delimited files.
            df1h = pd.read_csv(file1, sep='|', dtype=str, keep_default_na=False, header=0)
            df2h = pd.read_csv(file2, sep='|', dtype=str, keep_default_na=False, header=0)
            if all(k in df1h.columns for k in key_columns) and all(k in df2h.columns for k in key_columns):
                df1, df2 = df1h, df2h
            elif mapping_config and mapping_config.get('fields'):
                # Mapping-derived fallback for files without header rows.
                names = [f.get('name') for f in mapping_config.get('fields', []) if f.get('name')]
                if names:
                    df1m = pd.read_csv(file1, sep='|', dtype=str, keep_default_na=False, header=None, names=names)
                    df2m = pd.read_csv(file2, sep='|', dtype=str, keep_default_na=False, header=None, names=names)
                    if all(k in df1m.columns for k in key_columns) and all(k in df2m.columns for k in key_columns):
                        df1, df2 = df1m, df2m
        except Exception:
            pass

    comparator = FileComparator(df1, df2, key_columns)
    result = comparator.compare(detailed=detailed)
    result["structure_compatible"] = True
    return result
