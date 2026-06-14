"""Fixed-width column blanker used as the L3 tolerance pre-processor.

Why this exists
---------------
The L3 gate compares the Java-generated output against the pinned golden
baseline. Some fields (timestamps, sequence numbers, batch IDs) are
expected to drift run-over-run and must be ignored. The prompt's
``<deliverables>`` §1 declares those fields per-output-file under
``tolerance.ignore_fields`` in the source YAML.

Valdo's ``compare`` CLI has **no** ``--ignore-fields`` flag today, so
this module is the gap-filler called out in §6(f) of the prompt:

> deliver a thin pre-processor that strips/masks those columns from both
> files into a temp dir before invoking ``valdo compare``.

The wrapper script invokes this module on the Java output and the
baseline, writes the two redacted files into ``work_root/l3_redacted/``,
and points ``valdo compare`` at the pair.

Approach
--------
* **Fixed-width only** is supported in this milestone. The mappings under
  ``config/mappings/`` carry ``position`` (1-indexed ordinal) and ``length``
  per field; offsets are computed by summing prior lengths in position
  order. Delimited formats are out of scope here (and not used by the
  current SRC_A profile).
* **Multi-record-type files** are supported via the ``discriminator_field``
  column in the source YAML. Each line of the file is classified by the
  discriminator's value and the per-record-type field table is consulted.
* **Blanking, not stripping**: ignored byte ranges are overwritten with
  ASCII spaces. This preserves line length (fixed-width semantics) and
  keeps the rest of the line byte-identical so any genuine drift in
  surrounding fields still surfaces in the diff.
* **Newline-preserving**: the original line ending (``\\n`` or ``\\r\\n``)
  is preserved byte-for-byte.
* **Streaming**: lines are processed one at a time; large outputs do not
  load the file into memory.

This module never raises on per-line surprises (short lines, unknown
discriminator values). It logs them to stderr — never to a JSONL the
wrapper owns — and continues. The wrapper's failure-sink writes are
driven by the downstream Valdo compare result, not by this preprocessor.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Reserved transaction_type used when a mapping declares a single record type
# without an explicit discriminator value.
_DEFAULT_RECORD_TYPE = "default"


class IgnoreFieldFilterError(RuntimeError):
    """Raised for mapping-/config-level errors that prevent any work."""


@dataclass
class _FieldSpec:
    """One field's offset/length, derived from the mapping JSON."""

    name: str
    start: int  # 0-indexed byte offset within the line
    length: int

    @property
    def end(self) -> int:  # exclusive
        return self.start + self.length


@dataclass
class _RecordTypeTable:
    """Computed offset/length table for one record type within a mapping."""

    record_type: str
    fields: List[_FieldSpec]

    def find(self, name: str) -> Optional[_FieldSpec]:
        for f in self.fields:
            if f.name == name:
                return f
        return None


@dataclass
class _DiscriminatorSpec:
    """How to extract the record-type identifier from each line.

    ``position`` is 1-indexed (matches the mapping JSON convention).
    """

    name: str
    start: int  # 0-indexed
    length: int

    def value_for(self, line: str) -> str:
        return line[self.start : self.start + self.length]


@dataclass
class FilterPlan:
    """Pre-computed plan for one (mapping, ignore_fields) pair.

    The plan is reusable across many files and is what tests target.
    """

    tables: Dict[str, _RecordTypeTable] = field(default_factory=dict)
    discriminator: Optional[_DiscriminatorSpec] = None
    # Byte ranges to blank per record type, sorted ascending. Each tuple is
    # ``(start_offset, length)`` — what the blanking step actually consumes.
    blank_ranges: Dict[str, List[Tuple[int, int]]] = field(default_factory=dict)
    # Field names actually located (for diagnostics).
    located_fields: List[str] = field(default_factory=list)
    # Field names requested but not found in any record type (logged once).
    missing_fields: List[str] = field(default_factory=list)

    def is_noop(self) -> bool:
        """True when no blanking would happen for any record type."""
        return not any(self.blank_ranges.values())


# --------------------------------------------------------------------------- #
# Plan builder
# --------------------------------------------------------------------------- #


def build_filter_plan(
    mapping: Dict[str, Any],
    ignore_fields: Iterable[str],
    *,
    discriminator_field: Optional[str] = None,
) -> FilterPlan:
    """Compute a :class:`FilterPlan` from a parsed mapping JSON.

    Args:
        mapping: The parsed mapping JSON (top-level dict).
        ignore_fields: Field names to redact. Case-sensitive, matches the
            ``name`` key in mapping fields.
        discriminator_field: Optional name of the discriminator field for
            multi-record files. When provided, blanking is computed
            per-record-type. When absent, all fields are treated as
            ``record_type="default"``.

    Returns:
        A reusable :class:`FilterPlan`.

    Raises:
        IgnoreFieldFilterError: If the mapping declares a non-fixed-width
            format. (Delimited mappings need a different strategy and are
            out of scope for this milestone.)
    """
    fmt = mapping.get("format_type")
    if fmt and fmt != "fixed_width":
        raise IgnoreFieldFilterError(
            f"unsupported mapping format_type={fmt!r}; only fixed_width is "
            "supported by the ignore-field filter today."
        )

    fields = mapping.get("fields") or []
    if not isinstance(fields, list):
        raise IgnoreFieldFilterError(
            "mapping 'fields' must be a list"
        )

    # Group fields by transaction_type, preserving order by ``position``.
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for f in fields:
        if not isinstance(f, dict):
            continue
        rtype = str(f.get("transaction_type") or _DEFAULT_RECORD_TYPE)
        by_type.setdefault(rtype, []).append(f)

    # Compute offset tables per record type.
    tables: Dict[str, _RecordTypeTable] = {}
    for rtype, group in by_type.items():
        # Sort by position; treat missing position as the entry order index+1.
        positioned = sorted(
            enumerate(group), key=lambda x: int(x[1].get("position") or x[0] + 1)
        )
        offset = 0
        specs: List[_FieldSpec] = []
        for _, fdef in positioned:
            name = fdef.get("name")
            length = fdef.get("length")
            if not isinstance(name, str) or name == "":
                continue
            try:
                length_i = int(length)
            except (TypeError, ValueError):
                continue
            if length_i <= 0:
                continue
            specs.append(_FieldSpec(name=name, start=offset, length=length_i))
            offset += length_i
        tables[rtype] = _RecordTypeTable(record_type=rtype, fields=specs)

    # Compute the discriminator extractor, if any.
    discriminator: Optional[_DiscriminatorSpec] = None
    if discriminator_field:
        # The discriminator field must live in every record type at the same
        # byte offset for a well-formed multi-record mapping. We take the
        # first table's spec and verify the others agree.
        first_spec: Optional[_FieldSpec] = None
        first_rtype: Optional[str] = None
        for rtype, tbl in tables.items():
            spec = tbl.find(discriminator_field)
            if spec is None:
                continue
            if first_spec is None:
                first_spec, first_rtype = spec, rtype
                continue
            if spec.start != first_spec.start or spec.length != first_spec.length:
                raise IgnoreFieldFilterError(
                    f"discriminator field {discriminator_field!r} has a different "
                    f"offset/length in record_type={rtype!r} than in "
                    f"record_type={first_rtype!r}; mapping is inconsistent."
                )
        if first_spec is None:
            raise IgnoreFieldFilterError(
                f"discriminator field {discriminator_field!r} not present in mapping"
            )
        discriminator = _DiscriminatorSpec(
            name=first_spec.name,
            start=first_spec.start,
            length=first_spec.length,
        )

    # Compute blank ranges per record type.
    blank_ranges: Dict[str, List[Tuple[int, int]]] = {}
    located: List[str] = []
    missing: List[str] = []
    ignore_list = list(dict.fromkeys(ignore_fields))  # dedupe, preserve order
    for rtype, tbl in tables.items():
        ranges: List[Tuple[int, int]] = []
        for fname in ignore_list:
            spec = tbl.find(fname)
            if spec is None:
                continue
            ranges.append((spec.start, spec.length))
            located.append(f"{rtype}.{fname}")
        # Sort + (no need to merge — fields in a mapping never overlap).
        ranges.sort()
        blank_ranges[rtype] = ranges
    # Missing if a requested field appears in NO record type.
    for fname in ignore_list:
        if not any(
            tbl.find(fname) is not None for tbl in tables.values()
        ):
            missing.append(fname)

    return FilterPlan(
        tables=tables,
        discriminator=discriminator,
        blank_ranges=blank_ranges,
        located_fields=located,
        missing_fields=missing,
    )


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #


def blank_line(
    line: str,
    *,
    record_type: str,
    plan: FilterPlan,
) -> str:
    """Return ``line`` with every byte range for ``record_type`` blanked.

    The line's trailing newline (``\\n`` or ``\\r\\n``) is preserved exactly.
    Lines shorter than a blank range are tolerated: the range is clipped to
    the available length.
    """
    # Split off line ending.
    ending = ""
    body = line
    if body.endswith("\r\n"):
        ending = "\r\n"
        body = body[:-2]
    elif body.endswith("\n"):
        ending = "\n"
        body = body[:-1]

    ranges = plan.blank_ranges.get(record_type) or plan.blank_ranges.get(
        _DEFAULT_RECORD_TYPE, []
    )
    if not ranges:
        return body + ending

    # Mutable buffer is more efficient than repeated slicing on long lines.
    buf = list(body)
    n = len(buf)
    for start, length in ranges:
        if start >= n:
            continue
        end = min(start + length, n)
        for i in range(start, end):
            buf[i] = " "
    return "".join(buf) + ending


def apply_filter_to_file(
    *,
    src_file: Path,
    dst_file: Path,
    plan: FilterPlan,
) -> Dict[str, int]:
    """Stream ``src_file`` through ``plan`` and write to ``dst_file``.

    Args:
        src_file: Input file. Read in text mode (UTF-8, errors="replace").
        dst_file: Output file. Parent directory is created if needed.
        plan: Pre-computed :class:`FilterPlan`.

    Returns:
        Dict with counters: ``lines``, ``lines_blanked``, ``unknown_record_types``.
    """
    src_file = Path(src_file)
    dst_file = Path(dst_file)
    dst_file.parent.mkdir(parents=True, exist_ok=True)

    counters = {"lines": 0, "lines_blanked": 0, "unknown_record_types": 0}
    seen_unknown: set = set()
    has_blank = any(plan.blank_ranges.values())

    with src_file.open("r", encoding="utf-8", errors="replace", newline="") as fin, \
         dst_file.open("w", encoding="utf-8", newline="") as fout:
        for line in fin:
            counters["lines"] += 1
            if not has_blank:
                fout.write(line)
                continue

            record_type = _DEFAULT_RECORD_TYPE
            if plan.discriminator is not None:
                record_type = plan.discriminator.value_for(line).strip() or _DEFAULT_RECORD_TYPE
                if record_type not in plan.blank_ranges:
                    if record_type not in seen_unknown:
                        seen_unknown.add(record_type)
                        sys.stderr.write(
                            f"ignore_field_filter: unknown record_type "
                            f"{record_type!r} on line {counters['lines']} of "
                            f"{src_file.name}; leaving line unchanged\n"
                        )
                    counters["unknown_record_types"] += 1
                    fout.write(line)
                    continue

            new_line = blank_line(line, record_type=record_type, plan=plan)
            fout.write(new_line)
            counters["lines_blanked"] += 1

    return counters


# --------------------------------------------------------------------------- #
# Convenience: build a plan from disk
# --------------------------------------------------------------------------- #


def load_mapping(mapping_path: Path) -> Dict[str, Any]:
    """Read a mapping JSON from disk. Used by the wrapper script."""
    mapping_path = Path(mapping_path)
    if not mapping_path.is_file():
        raise IgnoreFieldFilterError(f"mapping not found: {mapping_path}")
    try:
        return json.loads(mapping_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IgnoreFieldFilterError(
            f"failed to parse mapping {mapping_path}: {exc}"
        ) from exc


def filter_pair(
    *,
    mapping_path: Path,
    ignore_fields: Iterable[str],
    discriminator_field: Optional[str],
    file_a: Path,
    file_b: Path,
    out_a: Path,
    out_b: Path,
) -> Dict[str, Any]:
    """High-level helper used by ``run_e2e_source.sh`` for the L3 phase.

    Builds the plan once from ``mapping_path`` and applies it to both
    files. Returns a small summary dict useful for JSONL logging.
    """
    mapping = load_mapping(mapping_path)
    plan = build_filter_plan(
        mapping, ignore_fields,
        discriminator_field=discriminator_field,
    )
    summary: Dict[str, Any] = {
        "mapping_path": str(mapping_path),
        "ignore_fields_requested": list(ignore_fields),
        "fields_located": plan.located_fields,
        "fields_missing": plan.missing_fields,
        "noop": plan.is_noop(),
        "file_a": apply_filter_to_file(
            src_file=file_a, dst_file=out_a, plan=plan
        ),
        "file_b": apply_filter_to_file(
            src_file=file_b, dst_file=out_b, plan=plan
        ),
    }
    return summary
