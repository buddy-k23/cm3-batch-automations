"""Parser for newline-delimited JSON (NDJSON) files (ADR 0018, S19-1, #395).

The parser treats the input as **newline-delimited JSON**: one
``json.loads(line)`` per non-blank line (empty/whitespace-only lines are
skipped, matching the multi-record reader contract per ADR 0014). Each
mapped field's ``json_path`` is resolved against the per-record dict with
``jsonpath-ng`` (pure Python) and written into a flat DataFrame column —
the shape downstream of :meth:`JsonParser.parse` is identical to the
fixed-width / delimited case: one column per mapped field, plus a leading
``__source_row__`` carrying the 1-indexed *physical* source line number.

Per ADR 0018 §3–§4:

- A ``json_path`` ending in ``[*]`` (array-of-objects) collapses to an
  integer **count** column named ``<field>_count`` — the load-bearing
  input to :meth:`~src.validators.field_validator.FieldValidator.validate_json_array_length`.
- Scalar paths preserve JSON's three states so the engine can tell them
  apart: a resolved value is written verbatim; a *present-but-null* value
  is written as Python ``None``; an *absent* path (the key did not resolve)
  is written as :data:`pandas.NA`. This absent-vs-null distinction is what
  :meth:`~src.validators.field_validator.FieldValidator.validate_nested_required`
  keys off.

Plain ``.json`` (a single top-level array of objects) is **not** supported
in v1 — convert to NDJSON first (``jq -c '.[]' in.json > in.ndjson``). The
format detector surfaces this with a clear message.
"""

import json
from typing import Any, Dict, List

import pandas as pd
from jsonpath_ng import parse as jsonpath_parse

from .base_parser import BaseParser


class JsonParser(BaseParser):
    """Parser for NDJSON files driven by per-field JSONPath selectors."""

    def __init__(self, file_path: str, fields: List[Dict[str, Any]]):
        """Initialize the NDJSON parser.

        Args:
            file_path: Path to the ``.ndjson`` / ``.jsonl`` file.
            fields: The mapping's flat field list. Each entry is a dict
                carrying at least a ``name`` and a ``json_path`` (the
                JSONPath selector emitted by
                :meth:`~src.config.template_converter.TemplateConverter.from_json_template`).
                Fields without a ``json_path`` are skipped — they cannot be
                located in a JSON record.
        """
        super().__init__(file_path)
        self.fields = fields or []
        # Pre-compile each field's JSONPath once. Paths ending in ``[*]``
        # are flagged as array paths so the resolved value collapses to a
        # count column instead of a scalar.
        self._compiled: List[Dict[str, Any]] = []
        for field in self.fields:
            json_path = field.get("json_path")
            if not json_path:
                continue
            name = field["name"]
            is_array = json_path.rstrip().endswith("[*]")
            self._compiled.append({
                "name": name,
                "column": f"{name}_count" if is_array else name,
                "is_array": is_array,
                "expr": jsonpath_parse(json_path),
            })

    def parse(self) -> pd.DataFrame:
        """Parse the NDJSON file into a flat DataFrame.

        Returns:
            DataFrame with one column per mapped field (array fields become
            a ``<field>_count`` integer column), plus a leading
            ``__source_row__`` column holding the 1-indexed physical source
            line number of each record. Scalar values are written verbatim;
            present-null resolves to ``None``; an absent path resolves to
            :data:`pandas.NA`.

        Raises:
            ValueError: If any non-blank line is not valid JSON. The message
                is line-addressable (e.g. ``"... on line 7"``) so error
                reports stay anchored to the source file, matching the
                ``__source_row__`` convention.
        """
        source_rows: List[int] = []
        rows: List[Dict[str, Any]] = []

        try:
            with open(self.file_path, "r", encoding="utf-8") as handle:
                for line_no, line in enumerate(handle, start=1):
                    if not line.strip():
                        # Skip empty / whitespace-only lines (ADR 0014).
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            f"Malformed JSON on line {line_no}: {exc.msg}"
                        ) from exc
                    source_rows.append(line_no)
                    rows.append(self._resolve_record(record))
        except ValueError:
            raise
        except Exception as exc:  # pragma: no cover - I/O errors
            raise ValueError(f"Failed to parse NDJSON file: {exc}") from exc

        columns = ["__source_row__"] + [c["column"] for c in self._compiled]
        if not rows:
            # Preserve the column contract even for an empty file so
            # downstream validators see the expected columns.
            return pd.DataFrame(columns=columns)

        df = pd.DataFrame(rows, columns=[c["column"] for c in self._compiled])
        df.insert(0, "__source_row__", source_rows)
        return df

    def _resolve_record(self, record: Any) -> Dict[str, Any]:
        """Resolve every compiled JSONPath against one parsed record.

        Args:
            record: The Python object produced by ``json.loads`` for a
                single NDJSON line.

        Returns:
            A flat dict keyed by output column name. Array paths yield an
            integer match count; scalar paths yield the resolved value,
            ``None`` for present-null, or :data:`pandas.NA` when absent.
        """
        resolved: Dict[str, Any] = {}
        for spec in self._compiled:
            matches = spec["expr"].find(record)
            if spec["is_array"]:
                # ``[*]`` -> number of array elements (0 when empty/absent).
                resolved[spec["column"]] = len(matches)
            elif not matches:
                # Path did not resolve: key is absent. pd.NA is the sentinel
                # validate_nested_required keys off (distinct from present-null).
                resolved[spec["column"]] = pd.NA
            else:
                # Present (possibly with a JSON null -> Python None).
                resolved[spec["column"]] = matches[0].value
        return resolved

    def validate_format(self) -> bool:
        """Validate that the first non-blank line is a JSON object.

        Returns:
            ``True`` when the first non-blank line parses as a JSON object
            (``dict``). Returns ``False`` if the file cannot be read, is
            empty, or the first record is not a JSON object (e.g. a bare
            array, scalar, or non-JSON text).
        """
        try:
            with open(self.file_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    return isinstance(json.loads(line), dict)
            return False
        except Exception:
            return False
