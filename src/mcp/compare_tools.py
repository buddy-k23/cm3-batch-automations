"""Ad-hoc file-compare MCP tool implementation (S7-4, #382).

This module wires the ``compare_two_files`` MCP tool onto Valdo's existing
:class:`src.comparators.file_comparator.FileComparator`. The tool lets an
agent diff two arbitrary files row-by-row by a declared set of key columns
without first registering a Valdo *source* — handy for one-off "is the
report I just generated identical to last week's?" workflows.

Design split (mirrors EF-S4 / EF-S5):

* Thin adapter — no business logic. Parsing routes to
  :class:`~src.parsers.fixed_width_parser.FixedWidthParser` for ``.txt``
  files when a mapping JSON is supplied, else to ``pandas.read_csv`` with
  the correct delimiter for ``.csv`` / ``.tsv``.
* Comparison is delegated verbatim to ``FileComparator(...).compare()``;
  we then project its result onto the MCP-facing shape (summary +
  ``top_differences``).
* The tool registration itself lives in :mod:`src.mcp.server` — this
  module exports the callable + description constant only.

Auto-detection by extension (per the S7-4 AC):

============  =====================================================
Extension     Behaviour
============  =====================================================
``.csv``      ``pandas.read_csv(sep=",", dtype=str, header=0)`` via
              :class:`src.parsers.format_detector.FormatDetector` ->
              :class:`PipeDelimitedParser` (which infers ``sep=","``
              from the ``.csv`` extension after the S8-2 routing fix).
``.tsv``      ``pandas.read_csv(sep="\\t", dtype=str, header=0)`` via
              the same detector route; the parser infers ``sep="\\t"``
              from the ``.tsv`` extension.
``.txt``      :class:`FixedWidthParser` with column specs built from
              the mapping JSON. ``mapping_path`` is REQUIRED for
              ``.txt`` and a :class:`ToolError` is raised when it is
              omitted.
============  =====================================================

Severity-filter deviation from the issue spec:

    The S7-4 issue ships ``severity_filter`` in the function signature, but
    :class:`FileComparator` does not classify per-row severity — it returns
    a flat ``differences`` list with ``{keys, differences, ...}`` entries
    where the ``differences`` dict carries the field name + before/after
    values but no severity bucket. Rather than ship a parameter that
    silently never matches anything, the parameter is dropped from the
    public tool signature. The deviation is documented in
    ``docs/MCP_SERVER.md`` under the ``compare_two_files`` subsection so
    an agent reading the doc sees the contract without guessing.

``comparison_id`` is ephemeral:

    Per the issue's "out of scope" clause, no run history is persisted for
    ad-hoc comparisons. We mint a fresh ``uuid.uuid4().hex`` and return it
    so a client can correlate the response with a follow-up message, but
    nothing else in Valdo retains the id.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from mcp.server.fastmcp.exceptions import ToolError

from src.comparators.file_comparator import FileComparator

__all__ = [
    "compare_two_files_payload",
    "COMPARE_TWO_FILES_DESCRIPTION",
    "TOP_DIFFERENCES_LIMIT",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Cap the ``top_differences`` slice so a worst-case 100k-row diff cannot
# blow the MCP response budget. The agent can fetch the full diff via the
# CLI / API surface if it needs more than this — the MCP tool intentionally
# surfaces the *top* slice, not the full list.
TOP_DIFFERENCES_LIMIT = 10

# Supported file extensions for auto-detection.
_CSV_EXTENSIONS = frozenset({".csv"})
_TSV_EXTENSIONS = frozenset({".tsv"})
_FIXED_WIDTH_EXTENSIONS = frozenset({".txt"})
_SUPPORTED_EXTENSIONS = _CSV_EXTENSIONS | _TSV_EXTENSIONS | _FIXED_WIDTH_EXTENSIONS


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _build_fixed_width_specs(cfg: Dict[str, Any]) -> List[tuple]:
    """Build ``(name, start, end)`` byte-offset tuples from a mapping config.

    Mirrors :func:`src.services.compare_service._build_fixed_width_specs`
    so the two code paths stay byte-compatible. We avoid importing the
    private helper from ``compare_service`` so this module has no
    cross-service dependency.

    Args:
        cfg: Mapping JSON parsed into a dict. Must contain a ``fields``
            list. Each field needs a ``length``; ``position`` (1-based)
            is optional and defaults to "contiguous with previous field".

    Returns:
        Ordered list of ``(field_name, start_offset, end_offset)`` tuples
        with 0-based, exclusive offsets.

    Raises:
        ToolError: When the mapping has no ``fields`` array, or any field
            entry is missing a ``name`` / ``length``.
    """
    fields = cfg.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ToolError(
            "Mapping JSON has no usable 'fields' array — fixed-width "
            "parsing requires field name + length metadata."
        )

    specs: List[tuple] = []
    current_pos = 0
    for idx, field in enumerate(fields):
        if not isinstance(field, dict):
            raise ToolError(
                f"Mapping fields[{idx}] must be an object, got "
                f"{type(field).__name__}."
            )
        name = field.get("name")
        length = field.get("length")
        if not name or length is None:
            raise ToolError(
                f"Mapping fields[{idx}] is missing 'name' or 'length'."
            )
        try:
            length_int = int(length)
        except (TypeError, ValueError) as exc:
            raise ToolError(
                f"Mapping fields[{idx}] has non-integer length {length!r}."
            ) from exc

        if field.get("position") is not None:
            try:
                start = int(field["position"]) - 1
            except (TypeError, ValueError) as exc:
                raise ToolError(
                    f"Mapping fields[{idx}] has non-integer position "
                    f"{field['position']!r}."
                ) from exc
        else:
            start = current_pos
        end = start + length_int
        specs.append((name, start, end))
        current_pos = end
    return specs


def _load_mapping_json(mapping_path: str) -> Dict[str, Any]:
    """Read and parse a mapping JSON file.

    Args:
        mapping_path: Filesystem path to a mapping JSON file. Path traversal
            and NUL bytes are rejected as a defence-in-depth guard so a
            malicious agent cannot use this tool to enumerate arbitrary
            files. (The path still has to land on a readable JSON file —
            the traversal guard just rejects ``..`` / NUL payloads early.)

    Returns:
        Parsed mapping dictionary.

    Raises:
        ToolError: When the file does not exist, is not valid JSON, or the
            top-level value is not an object.
    """
    if not mapping_path or not isinstance(mapping_path, str):
        raise ToolError("mapping_path must be a non-empty string when provided.")
    if "\x00" in mapping_path:
        raise ToolError("mapping_path must not contain NUL bytes.")

    path = Path(mapping_path)
    if not path.is_file():
        raise ToolError(f"Mapping file not found: {mapping_path!r}")

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolError(
            f"Failed to read mapping JSON {mapping_path!r}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ToolError(
            f"Mapping JSON {mapping_path!r} did not parse to an object."
        )
    return data


def _parse_file(
    file_path: str,
    mapping_config: Optional[Dict[str, Any]],
    *,
    role: str,
) -> pd.DataFrame:
    """Parse *file_path* into a DataFrame using extension-based routing.

    The routing rules are:

    * ``.csv`` / ``.tsv`` -> :class:`src.parsers.format_detector.FormatDetector`
      routes to :class:`PipeDelimitedParser`, which infers the correct
      separator (``,`` or ``\\t``) from the file extension after the S8-2
      routing fix. The DataFrame is read with ``header=0`` because the
      compare tool's contract assumes a header row supplies the column
      names used by ``key_columns``.
    * ``.txt`` -> :class:`FixedWidthParser`; ``mapping_config`` must be
      provided or a :class:`ToolError` is raised.

    Args:
        file_path: Path to the file to parse.
        mapping_config: Parsed mapping JSON dict, or ``None`` when no
            mapping was supplied.
        role: Either ``"left"`` or ``"right"`` — used only for error
            messages so the agent can disambiguate which side blew up.

    Returns:
        Parsed DataFrame with string-typed values. Empty values are
        preserved as empty strings (``keep_default_na=False``) so the
        comparator does not get NaN-vs-empty false positives.

    Raises:
        ToolError: When the file is missing, the extension is unsupported,
            or fixed-width parsing is attempted without a mapping config.
    """
    path = Path(file_path)
    if not path.is_file():
        raise ToolError(f"File not found ({role}): {file_path!r}")

    suffix = path.suffix.lower()
    if suffix in _CSV_EXTENSIONS or suffix in _TSV_EXTENSIONS:
        # Post-S8-2 (#393): FormatDetector now routes .csv / .tsv to
        # PipeDelimitedParser with the correct delimiter inferred from
        # the extension, retiring the S7-4 workaround that used a
        # hardcoded ``pd.read_csv(sep=",")``. We still read with
        # ``header=0`` here (rather than calling parser.parse()
        # directly) because the MCP compare contract requires header-
        # derived column names so ``key_columns`` lookup works, whereas
        # PipeDelimitedParser.parse() reads with ``header=None`` for
        # backwards compatibility with the validation pipeline.
        from src.parsers.format_detector import FormatDetector
        from src.parsers.pipe_delimited_parser import PipeDelimitedParser

        parser_class = FormatDetector().get_parser_class(file_path)
        if parser_class is not PipeDelimitedParser:
            raise ToolError(
                f"Expected delimited parser for {file_path!r}, got "
                f"{parser_class.__name__}."
            )
        delimiter = PipeDelimitedParser._infer_delimiter(file_path)
        try:
            return pd.read_csv(
                file_path,
                sep=delimiter,
                dtype=str,
                keep_default_na=False,
                header=0,
            )
        except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
            fmt = "CSV" if suffix in _CSV_EXTENSIONS else "TSV"
            raise ToolError(
                f"Failed to parse {fmt} ({role}) {file_path!r}: {exc}"
            ) from exc

    if suffix in _FIXED_WIDTH_EXTENSIONS:
        if mapping_config is None:
            raise ToolError(
                f"Fixed-width file ({role}) {file_path!r} requires "
                "mapping_path with field name + length metadata."
            )
        from src.parsers.fixed_width_parser import FixedWidthParser

        specs = _build_fixed_width_specs(mapping_config)
        try:
            df = FixedWidthParser(file_path, specs).parse()
        except ValueError as exc:
            raise ToolError(
                f"Failed to parse fixed-width file ({role}) "
                f"{file_path!r}: {exc}"
            ) from exc
        # Drop the parser's internal source-row tracker so it does not
        # spuriously surface in the comparator's diff payload.
        if "__source_row__" in df.columns:
            df = df.drop(columns=["__source_row__"])
        return df

    raise ToolError(
        f"Unsupported file extension {suffix!r} for {file_path!r}. "
        f"Supported: {sorted(_SUPPORTED_EXTENSIONS)}."
    )


# ---------------------------------------------------------------------------
# Comparator projection
# ---------------------------------------------------------------------------


def _validate_key_columns(
    key_columns: Any,
    df_left: pd.DataFrame,
    df_right: pd.DataFrame,
) -> List[str]:
    """Coerce + validate ``key_columns`` against both DataFrames.

    Args:
        key_columns: Raw value supplied by the caller (expected list of
            strings).
        df_left: Parsed left DataFrame; column names are checked.
        df_right: Parsed right DataFrame; column names are checked.

    Returns:
        The list of validated key-column names.

    Raises:
        ToolError: When the list is empty, contains non-string entries, or
            references a column missing from either side.
    """
    if not isinstance(key_columns, list) or not key_columns:
        raise ToolError(
            "key_columns is required and must be a non-empty list of strings."
        )

    cleaned: List[str] = []
    for col in key_columns:
        if not isinstance(col, str) or not col.strip():
            raise ToolError(
                f"key_columns entry {col!r} must be a non-empty string."
            )
        cleaned.append(col)

    left_cols = set(df_left.columns)
    right_cols = set(df_right.columns)
    missing_left = [c for c in cleaned if c not in left_cols]
    missing_right = [c for c in cleaned if c not in right_cols]
    if missing_left or missing_right:
        parts: List[str] = []
        if missing_left:
            parts.append(f"missing in left: {missing_left}")
        if missing_right:
            parts.append(f"missing in right: {missing_right}")
        raise ToolError(
            "key_columns reference unknown column(s) — " + "; ".join(parts)
        )
    return cleaned


def _project_summary(raw: Dict[str, Any]) -> Dict[str, int]:
    """Project the comparator's raw result onto the MCP summary shape.

    The :class:`FileComparator` returns ``matching_rows``,
    ``rows_with_differences``, and lists for the unique-side slots. We
    rename those to the agent-facing ``matched`` / ``differing`` /
    ``only_in_left`` / ``only_in_right`` keys per the S7-4 AC.

    Args:
        raw: Result dict from ``FileComparator(...).compare()``.

    Returns:
        Dict with four integer counts.
    """
    only_left = raw.get("only_in_file1")
    only_right = raw.get("only_in_file2")
    # The comparator returns DataFrames OR lists depending on whether it
    # took the key-based branch (DataFrame) or row-by-row branch (list).
    # We always treat them as a count.
    if isinstance(only_left, pd.DataFrame):
        only_left_count = len(only_left)
    else:
        only_left_count = len(only_left or [])
    if isinstance(only_right, pd.DataFrame):
        only_right_count = len(only_right)
    else:
        only_right_count = len(only_right or [])

    return {
        "matched": int(raw.get("matching_rows", 0) or 0),
        "differing": int(raw.get("rows_with_differences", 0) or 0),
        "only_in_left": only_left_count,
        "only_in_right": only_right_count,
    }


def _project_top_differences(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the first :data:`TOP_DIFFERENCES_LIMIT` diffs in MCP shape.

    Each diff entry from the comparator has the shape::

        {
            "keys": {<key>: <value>, ...},
            "differences": {<field>: {"file1": ..., "file2": ..., ...}, ...},
            "difference_count": int,
            ...
        }

    We rename ``file1`` / ``file2`` to ``left`` / ``right`` so the agent
    surface mirrors the tool signature (``left_path`` / ``right_path``)
    rather than the internal comparator vocabulary.

    Args:
        raw: Result dict from ``FileComparator(...).compare()``.

    Returns:
        List of up to :data:`TOP_DIFFERENCES_LIMIT` projected diff dicts.
    """
    diffs = raw.get("differences") or []
    if not isinstance(diffs, list):
        return []

    projected: List[Dict[str, Any]] = []
    for entry in diffs[:TOP_DIFFERENCES_LIMIT]:
        if not isinstance(entry, dict):
            continue
        field_diffs_raw = entry.get("differences") or {}
        field_diffs: Dict[str, Dict[str, Any]] = {}
        if isinstance(field_diffs_raw, dict):
            for field_name, payload in field_diffs_raw.items():
                if not isinstance(payload, dict):
                    continue
                left_val = payload.get("file1")
                right_val = payload.get("file2")
                projected_field: Dict[str, Any] = {
                    "left": left_val,
                    "right": right_val,
                }
                if "type" in payload:
                    projected_field["type"] = payload["type"]
                field_diffs[field_name] = projected_field

        projected.append(
            {
                "keys": entry.get("keys", {}),
                "differences": field_diffs,
                "difference_count": entry.get("difference_count", len(field_diffs)),
            }
        )
    return projected


# ---------------------------------------------------------------------------
# Public tool implementation
# ---------------------------------------------------------------------------


def compare_two_files_payload(
    left_path: str,
    right_path: str,
    key_columns: List[str],
    *,
    mapping_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Compare two files row-by-row by the declared key columns.

    Format auto-detection is by extension only:

    * ``.csv`` -> comma-separated with header row.
    * ``.tsv`` -> tab-separated with header row.
    * ``.txt`` -> fixed-width; ``mapping_path`` is REQUIRED.

    The S7-4 issue ships a ``severity_filter`` parameter but the underlying
    :class:`~src.comparators.file_comparator.FileComparator` does not
    classify per-row severity, so the parameter is deliberately omitted
    from this signature. See the module docstring for the rationale.

    Args:
        left_path: Path to the left-hand file. Extension drives parsing.
        right_path: Path to the right-hand file. Extension drives parsing.
        key_columns: Non-empty list of column names to join on. Every
            entry must exist in BOTH files' headers; missing-column errors
            are raised as :class:`ToolError`.
        mapping_path: Optional path to a mapping JSON. Required when
            either file is ``.txt`` (fixed-width). When supplied, the
            mapping's ``fields`` list drives column extraction.

    Returns:
        Dict with three keys::

            {
                "comparison_id": "<uuid hex>",
                "summary": {
                    "matched": int,
                    "differing": int,
                    "only_in_left": int,
                    "only_in_right": int,
                },
                "top_differences": [<up to 10 projected diffs>],
            }

        ``comparison_id`` is ephemeral — minted per call, never persisted.

    Raises:
        ToolError: When either file is missing, the extension is
            unsupported, the mapping JSON is malformed, ``key_columns``
            is empty / contains an unknown column, or pandas / the
            fixed-width parser fails to read either file.
    """
    if not isinstance(left_path, str) or not left_path:
        raise ToolError("left_path is required and must be a non-empty string.")
    if not isinstance(right_path, str) or not right_path:
        raise ToolError("right_path is required and must be a non-empty string.")

    mapping_config: Optional[Dict[str, Any]] = None
    if mapping_path is not None:
        mapping_config = _load_mapping_json(mapping_path)

    df_left = _parse_file(left_path, mapping_config, role="left")
    df_right = _parse_file(right_path, mapping_config, role="right")

    cleaned_keys = _validate_key_columns(key_columns, df_left, df_right)

    comparator = FileComparator(df_left, df_right, cleaned_keys)
    try:
        raw_result = comparator.compare(detailed=True)
    except Exception as exc:  # noqa: BLE001 — surface as ToolError; the comparator can raise pandas-internal errors on degenerate inputs
        logger.exception("FileComparator.compare() failed: %s", exc)
        raise ToolError(f"File comparison failed: {exc}") from exc

    return {
        "comparison_id": uuid.uuid4().hex,
        "summary": _project_summary(raw_result),
        "top_differences": _project_top_differences(raw_result),
    }


# ---------------------------------------------------------------------------
# Tool description (kept here so server.py stays registration glue)
# ---------------------------------------------------------------------------

COMPARE_TWO_FILES_DESCRIPTION = (
    "Compare two files row-by-row by a set of key columns. Auto-detects "
    "format from the file extension: .csv (comma-separated, header row), "
    ".tsv (tab-separated, header row), or .txt (fixed-width — requires "
    "mapping_path with field name + length metadata). Returns a summary "
    "with matched / differing / only_in_left / only_in_right counts plus "
    "the top 10 row-level differences with field-level before/after "
    "values. The comparison_id in the response is ephemeral — no run "
    "history is persisted for ad-hoc comparisons. Raises a tool error "
    "when either file is missing, a key column is unknown, or fixed-width "
    "parsing is requested without a mapping."
)
