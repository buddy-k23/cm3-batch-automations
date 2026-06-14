"""Valid-values extraction from spec workbook cells.

Implements the CRITICAL rule from ``prompts/generate-rules-csv.md``:
mine enumerated valid values from the **Transformation** column first
(because that reflects what the source system actually sends), and only
fall back to the workbook's **Valid Values** column when the
transformation has no concrete values *and* no dynamic-marker phrases.

Pulled verbatim from the original ``generate_shaw_atoctran_csvs.py`` so
that ATOCTRAN regression CSVs remain byte-identical after the
generalization refactor.
"""

from __future__ import annotations

import re
from typing import List, Tuple


# Patterns that mean "this field is dynamic — no valid_values rule".
_DYNAMIC_MARKERS = (
    "leave blank",
    "pass as is",
    "pass through",
    "batch date",
    "current date",
    "populate with",
    "select ",      # "Select CONTACT-ID from..." — dynamic lookup
    "lookup",
    "fetched from",
    "if 1st",       # sequential counter: "if 1st account then '1'; if 2nd then '2'..."
    "if nth",       # "if nth account then 'n'" — n is a placeholder, not a literal value
    "equal to",     # "IF X is equal to 'Zero' Then..." — field comparison, not enumeration
)

# Quote characters we recognize and strip (ASCII + smart quotes).
_QUOTE_CHARS = "'\"\u2018\u2019\u201c\u201d"

# Phrases that indicate descriptive prose, not a value list.
_DESCRIPTIVE_PHRASES = (
    "must be", "is used", "represents", "control table",
    "refer to", "see ", "valid loc", "defined in",
)


def extract_valid_values(
    *,
    transform: str,
    workbook_valid_values: str,
    field_length: int,
) -> Tuple[str, str]:
    """Extract valid values from both spec columns and apply length filter.

    Per the prompt's CRITICAL rule, valid values are derived primarily
    from the Transformation column. BAs sometimes also put values in the
    workbook's Valid Values column; we union both sources here
    (transformation first, workbook fallback only when transformation
    yields no concrete values and no dynamic markers).

    Args:
        transform: Raw text from the ``Transformation Logic`` column.
        workbook_valid_values: Raw text from the target-side
            ``Valid Values`` column.
        field_length: The field's declared character length from the
            spec. Used to drop candidate values that cannot fit in the
            field. Pass 0 to disable the length filter.

    Returns:
        Tuple of ``(pipe_separated_values, note)``. ``note`` is
        non-empty when the extractor couldn't confidently parse a list
        and the BA should review the field manually (e.g. ambiguous
        prose, candidates dropped due to length).
    """
    notes: List[str] = []

    t = (transform or "").strip()
    t_lower = t.lower()

    transformation_values = (
        _extract_from_transformation(t) if t else []
    )
    workbook_values, wb_note = _parse_valid_values_column(
        workbook_valid_values
    )

    if transformation_values:
        candidates: List[str] = list(transformation_values)
    elif t and any(
        re.search(pat, t_lower) for pat in _DYNAMIC_MARKERS
    ):
        return "", ""
    else:
        if wb_note:
            notes.append(wb_note)
        candidates = list(workbook_values)

    seen: set[str] = set()
    accepted: List[str] = []
    rejected_length: List[str] = []
    for raw in candidates:
        v = raw.strip().strip(_QUOTE_CHARS).strip()
        if not v:
            continue
        if v in seen:
            continue
        if not _is_clean_code(v):
            continue
        if field_length > 0 and len(v) > field_length:
            rejected_length.append(v)
            seen.add(v)
            continue
        seen.add(v)
        accepted.append(v)

    if rejected_length:
        notes.append(
            f"dropped {len(rejected_length)} value(s) longer than "
            f"length={field_length}: {','.join(rejected_length[:3])}"
            f"{'...' if len(rejected_length) > 3 else ''}"
        )

    return "|".join(accepted), "; ".join(notes)


def _parse_valid_values_column(raw: str) -> Tuple[List[str], str]:
    """Parse the workbook's Valid Values column into a list of code candidates."""
    if not raw:
        return [], ""

    text = raw.strip()
    if not text:
        return [], ""

    lower = text.lower()
    if any(phrase in lower for phrase in _DESCRIPTIVE_PHRASES):
        return [], f"Valid Values column contains prose, not a list: {text[:50]!r}"

    parts: List[str] = []
    for sep in ("\n", "|", ","):
        if sep in text:
            parts = [p.strip() for p in text.split(sep) if p.strip()]
            if len(parts) >= 2:
                break
    if not parts:
        parts = [text]

    candidates: List[str] = []
    for part in parts:
        cleaned = re.sub(r"^[-*\u2022]\s*", "", part).strip()
        m = re.match(
            rf"^[{_QUOTE_CHARS}]?([^\s{_QUOTE_CHARS}]+)[{_QUOTE_CHARS}]?"
            r"\s*[-=:\u2013\u2014\ufffd]\s*\S",
            cleaned,
        )
        if m:
            cleaned = m.group(1)
        else:
            cleaned = cleaned.strip(_QUOTE_CHARS)
        if cleaned:
            candidates.append(cleaned)
    return candidates, ""


def _extract_from_transformation(t: str) -> List[str]:
    """Mine valid-value candidates from a Transformation cell."""
    found: List[str] = []

    # Pattern A: `Default to 'X'` / `Hard-Code to "X"` / `Default = USD`
    for m in re.finditer(
        r"(?:Default\s+to|Hard[-\s]?Code\s+to|Default\s*=)\s*"
        rf"[{_QUOTE_CHARS}]?([^,;\s'\"\u2018\u2019\u201c\u201d]+)"
        rf"[{_QUOTE_CHARS}]?",
        t,
        flags=re.IGNORECASE,
    ):
        found.append(m.group(1))

    # Pattern B: ``Valid Values - X or Y`` / ``Valid Values: X, Y, Z``.
    for m in re.finditer(
        r"valid\s+values?\s*[-:]\s*(.+?)(?=\.\s|$|\n|;|If\s|If\s)",
        t,
        flags=re.IGNORECASE,
    ):
        chunk = m.group(1)
        found.extend(_split_inline_list(chunk))

    # Pattern C: ``following hard-coded values: X, Y, Z``.
    for m in re.finditer(
        r"hard[-\s]?coded\s+values?\s*[:=]\s*(.+?)(?=\.\s|$|\n|;|For\s|If\s)",
        t,
        flags=re.IGNORECASE,
    ):
        chunk = m.group(1)
        found.extend(_split_inline_list(chunk))

    # Pattern D: ``with following ... values: X, Y, Z``.
    for m in re.finditer(
        r"with\s+(?:following|the\s+following)\s+(?:hard[-\s]?coded\s+)?"
        r"values?\s*[:=]?\s*(.+?)(?=\.\s|$|\n|;|For\s|If\s)",
        t,
        flags=re.IGNORECASE,
    ):
        chunk = m.group(1)
        found.extend(_split_inline_list(chunk))

    # Pattern E + F: IF/THEN/ELSE and repeated `set 'X'` constructs.
    tl = t.lower()

    # Guard: sequential-counter transformations ("if 1st account then '1';
    # if 2nd then '2'; ... if nth then 'n'") look like IF/THEN branches but
    # are not enumerations. Also guard field-comparison transformations
    # ("IF 'ORG' is equal to 'Zero' Then Populate 'M-CHARGE-OFF-AMT'") where
    # the THEN clause contains a field name, not a literal value. Detect both
    # by the presence of known non-enumeration markers and skip branch
    # extraction entirely for those cells.
    _SEQUENTIAL_MARKERS = ("if 1st", "if nth", "if 2nd", "if 3rd",
                           "is equal to", "equal to '")
    is_sequential_counter = any(m in tl for m in _SEQUENTIAL_MARKERS)

    has_branch = (
        not is_sequential_counter
        and ("if " in tl)
        and ("then " in tl or "else" in tl)
    )

    set_matches = re.findall(
        rf"\bset\s+[{_QUOTE_CHARS}]([^{_QUOTE_CHARS}]+)[{_QUOTE_CHARS}]",
        t,
        flags=re.IGNORECASE,
    )
    if len(set_matches) >= 2:
        for q in set_matches:
            q = q.strip()
            if q and len(q.split()) == 1:
                found.append(q)
    elif has_branch:
        result_clauses: List[str] = []
        for clause in re.split(r"[.;]\s*|\n", t):
            cl = clause.lower()
            if " then " in cl or "else " in cl or cl.startswith("else"):
                result_clauses.append(clause)
        if result_clauses:
            for clause in result_clauses:
                for q in re.findall(
                    rf"[{_QUOTE_CHARS}]([^{_QUOTE_CHARS}]+)"
                    rf"[{_QUOTE_CHARS}]",
                    clause,
                ):
                    q = q.strip()
                    if q and len(q.split()) == 1:
                        found.append(q)

    # Pattern G: leading bare-quoted code.
    leading = re.match(
        rf"^\s*[{_QUOTE_CHARS}]([^{_QUOTE_CHARS}\s]+)[{_QUOTE_CHARS}]\s*[-\u2013\u2014]",
        t,
    )
    if leading:
        found.append(leading.group(1))

    return found


def _split_inline_list(chunk: str) -> List[str]:
    """Split an inline value list like ``100030, 100040 & 200000`` into codes."""
    normalised = re.sub(
        r"\s+(?:or|and|&)\s+", ", ", chunk, flags=re.IGNORECASE
    )
    parts = re.split(r"[,;|]", normalised)
    out: List[str] = []
    for p in parts:
        p = p.strip().strip(_QUOTE_CHARS).strip(".:;,")
        if not p:
            continue
        if " " in p:
            continue
        out.append(p)
    return out


def _is_clean_code(value: str) -> bool:
    """A code is short-ish, has no whitespace, no sentence punctuation."""
    if not value or len(value) > 30:
        return False
    if any(c.isspace() for c in value):
        return False
    if value.endswith(".") or value.endswith(":"):
        return False
    if all(not c.isalnum() for c in value):
        return False
    # Reject single lowercase letters that are prose placeholders (e.g. 'n'
    # in "if nth account then 'n'"). Real codes are either uppercase, numeric,
    # or multi-character. Single lowercase letters like 'n', 'x', 'y' are
    # almost always variable names in transformation prose, not literal values.
    if len(value) == 1 and value.islower():
        return False
    return True
