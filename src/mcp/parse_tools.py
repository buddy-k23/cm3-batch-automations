"""Parse/inspect MCP tool implementation (S22-1, #438).

Wires the ``parse_file`` MCP tool onto the existing Valdo parser layer — the
same :class:`~src.parsers.format_detector.FormatDetector`,
:class:`~src.parsers.pipe_delimited_parser.PipeDelimitedParser`, and
:class:`~src.parsers.fixed_width_parser.FixedWidthParser` that the
``valdo parse`` CLI command
(:func:`src.commands.parse_command.run_parse_command`) drives. Like the S21
action tools (``mask_file`` / ``detect_drift`` / ``extract_table``) this module
is a thin adapter — no parsing logic lives here; the format detection, the
delimited / fixed-width DataFrame construction, and the mapping-driven
field-spec layout all stay in the parser layer. The tool registration itself
stays in :mod:`src.mcp.server`.

The tool lets an agent peek at a batch file's contents — its column layout and
a sample of rows — without loading the whole file into the agent's context. The
response is a BOUNDED preview: the column names, the first ``limit`` parsed rows
(default :data:`DEFAULT_PREVIEW_LIMIT`, capped at :data:`MAX_PREVIEW_LIMIT`), the
true total row count, and a ``truncated`` flag. An unbounded file is therefore
never dumped over the MCP response.

ToolError is reserved for caller-fixable problems: a missing/blank ``file``
argument, a mapping file that cannot be found or parsed, an unknown ``format``,
a non-positive ``limit``, or a parse failure. The response carries no secrets.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp.exceptions import ToolError

__all__ = [
    "parse_file_payload",
    "PARSE_FILE_DESCRIPTION",
    "DEFAULT_PREVIEW_LIMIT",
    "MAX_PREVIEW_LIMIT",
]

logger = logging.getLogger(__name__)

# Preview bounds. The default keeps a parse peek cheap; the hard cap stops a
# caller from asking the tool to dump an unbounded file back over MCP.
DEFAULT_PREVIEW_LIMIT = 10
MAX_PREVIEW_LIMIT = 100

# Explicit delimited formats the tool accepts (mirrors the CLI's --format).
_DELIMITERS = {"pipe": "|", "csv": ",", "comma": ",", "tsv": "\t"}


def _build_field_specs(mapping_config: Dict[str, Any]) -> List[tuple]:
    """Build fixed-width (name, start, end) specs from a mapping JSON.

    Mirrors the field-spec construction in
    :func:`src.commands.parse_command.run_parse_command` so the MCP path and
    the CLI path lay fixed-width columns out identically.

    Args:
        mapping_config: The parsed mapping JSON; its ``fields`` list carries a
            ``name`` and a ``length`` per field, in declared order.

    Returns:
        A list of ``(name, start_pos, end_pos)`` tuples, contiguous from 0.
    """
    field_specs: List[tuple] = []
    current_pos = 0
    for field in mapping_config.get("fields", []) or []:
        field_name = field["name"]
        field_length = field["length"]
        field_specs.append((field_name, current_pos, current_pos + field_length))
        current_pos += field_length
    return field_specs


def parse_file_payload(
    file: str,
    mapping: Optional[str] = None,
    format: Optional[str] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Parse a batch file and return a bounded preview of its contents.

    Thin adapter over the Valdo parser layer (the same code path
    :func:`src.commands.parse_command.run_parse_command` drives). When a
    *mapping* is supplied the file is parsed as fixed-width using the mapping's
    field layout; otherwise an explicit *format* (``pipe`` | ``csv`` | ``tsv``)
    or — when neither is given — format auto-detection selects the parser. No
    parsing logic lives here; the DataFrame construction stays in the parser
    classes. The *file* is never modified.

    The returned preview is BOUNDED: at most *limit* rows (default
    :data:`DEFAULT_PREVIEW_LIMIT`, capped at :data:`MAX_PREVIEW_LIMIT`) are
    materialised into the response, even though ``row_count`` reflects the true
    total. An unbounded file is therefore never dumped back over MCP.

    Args:
        file: Path to the batch file to inspect. Never modified.
        mapping: Optional path to a mapping JSON. When supplied, the file is
            parsed as fixed-width using the mapping's ``fields`` layout (each
            field's ``name`` + ``length``). Mutually informative with *format*;
            when both are absent the format is auto-detected.
        format: Optional explicit delimited format — ``pipe`` | ``csv`` |
            ``tsv`` (``comma`` is accepted as an alias of ``csv``). Ignored when
            *mapping* is supplied. When omitted (and no mapping) the format is
            auto-detected from the file's content/extension.
        limit: Optional preview row cap. Defaults to
            :data:`DEFAULT_PREVIEW_LIMIT`; values above
            :data:`MAX_PREVIEW_LIMIT` are clamped down. Must be a positive
            integer.

    Returns:
        A bounded preview dict with:

        - ``columns``: the parsed column names (list of str).
        - ``rows``: up to *limit* rows, each a ``{column: value}`` dict (all
          values rendered as strings — the parsers read with ``dtype=str``).
        - ``row_count``: the true total number of parsed data rows (int).
        - ``preview_count``: the number of rows actually returned in ``rows``.
        - ``truncated``: ``True`` when ``row_count`` exceeds ``preview_count``.
        - ``format``: how the file was parsed (``fixed-width`` | the resolved
          delimited format | ``auto``).

    Raises:
        ToolError: For caller-fixable problems: *file* missing/blank, the
            mapping file missing or unparseable, an unknown *format*, a
            non-positive *limit*, a missing input file, or a parse failure.
    """
    if not file or not isinstance(file, str):
        raise ToolError("file is required and must be a non-empty string")

    # Resolve the preview cap (caller-fixable validation before any I/O).
    if limit is None:
        effective_limit = DEFAULT_PREVIEW_LIMIT
    else:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ToolError("limit must be a positive integer")
        effective_limit = min(limit, MAX_PREVIEW_LIMIT)

    if not Path(file).exists():
        raise ToolError(f"File not found: {file}")

    # Lazy imports keep heavy parser deps out of module import time and mirror
    # the CLI command's import sites.
    from src.parsers.format_detector import FormatDetector
    from src.parsers.fixed_width_parser import FixedWidthParser
    from src.parsers.pipe_delimited_parser import PipeDelimitedParser

    # --- Mapping-driven fixed-width path -------------------------------------
    if mapping:
        mapping_path = Path(mapping)
        if not mapping_path.exists():
            raise ToolError(f"Mapping file not found: {mapping}")
        try:
            mapping_config = json.loads(mapping_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ToolError(f"Failed to load mapping file: {exc}") from exc

        field_specs = _build_field_specs(mapping_config)
        parser = FixedWidthParser(file, field_specs)
        resolved_format = "fixed-width"
    else:
        # --- Delimited / auto-detect path ------------------------------------
        if format:
            fmt = format.lower()
            if fmt == "fixed":
                parser = FixedWidthParser(file, [])
                resolved_format = "fixed-width"
            elif fmt in _DELIMITERS:
                parser = PipeDelimitedParser(file, delimiter=_DELIMITERS[fmt])
                resolved_format = "csv" if fmt == "comma" else fmt
            else:
                raise ToolError(
                    f"Unknown format: {format!r}. "
                    "Expected one of: pipe, csv, tsv, fixed."
                )
        else:
            try:
                parser_class = FormatDetector().get_parser_class(file)
            except (ValueError, FileNotFoundError) as exc:
                raise ToolError(str(exc)) from exc
            parser = parser_class(file)
            resolved_format = "auto"

    # --- Parse + bound the preview -------------------------------------------
    try:
        df = parser.parse()
    except (ValueError, FileNotFoundError) as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface infra failures as ToolError
        logger.exception("parse_file tool failed for file=%r", file)
        raise ToolError(f"Parse failed: {exc}") from exc

    row_count = int(len(df))
    columns = [str(c) for c in df.columns]
    # head() bounds the materialised slice; to_dict keeps values as the
    # dtype=str the parsers already produce. NaN-safe via fillna("").
    head = df.head(effective_limit).fillna("")
    rows = [
        {str(k): ("" if v is None else str(v)) for k, v in record.items()}
        for record in head.to_dict(orient="records")
    ]

    return {
        "columns": columns,
        "rows": rows,
        "row_count": row_count,
        "preview_count": len(rows),
        "truncated": row_count > len(rows),
        "format": resolved_format,
    }


PARSE_FILE_DESCRIPTION = (
    "Parse a batch file and return a BOUNDED preview of its contents — the "
    "column layout plus a sample of rows — so an agent can inspect a file "
    "without loading the whole thing. Accepts the input file path, an optional "
    "mapping JSON path (when supplied the file is parsed as fixed-width using "
    "the mapping's 'fields' layout of name + length), an optional explicit "
    "delimited format (pipe | csv | tsv; ignored when a mapping is given; "
    "auto-detected when both are omitted), and an optional positive-integer "
    "preview row limit (default 10, capped at 100). Wraps the existing Valdo "
    "parser layer — the same code path the 'valdo parse' CLI uses; the input "
    "file is never modified. Returns 'columns' (parsed column names), 'rows' "
    "(up to 'limit' rows as {column: value} dicts), 'row_count' (the TRUE total "
    "row count, which may exceed the rows returned), 'preview_count', "
    "'truncated' (bool), and 'format' (how the file was parsed). The preview is "
    "always bounded — an unbounded file is never dumped over the response. "
    "Raises a tool error only for caller-fixable problems: a missing/blank file "
    "argument, a mapping file that cannot be found or parsed, an unknown "
    "format, a non-positive limit, a missing input file, or a parse failure."
)
