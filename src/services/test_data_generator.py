"""Synthetic test-data generation from mapping field definitions.

Provides pure functions for generating field values, rows, and complete files.
No file I/O — callers are responsible for writing output.

Functions:
    generate_field_value: Single field value from a field definition dict.
    generate_row: One row as {field_name: value} for all fields.
    generate_file: Multiple rows from a full mapping dict.
    inject_errors: Inject controlled errors into generated rows.
    generate_multi_record_file: Multi-record file from a multi-record YAML config dict.
"""
from __future__ import annotations

import copy
import json
import random
import re
import string
from datetime import datetime, timedelta

_DATE_ANCHOR = datetime(2026, 1, 15)  # Fixed reference date — keeps generated dates deterministic across runs
_DATE_RANGE_DAYS = 730  # +/- 2 years from anchor


def _get_rules(field_def: dict) -> list:
    """Return the rules list from either 'validation_rules' or 'rules' key.

    Args:
        field_def: A field definition dict from a mapping JSON.

    Returns:
        List of rule dicts (may be empty).
    """
    return field_def.get("validation_rules") or field_def.get("rules") or []


def _has_rule(field_def: dict, *rule_names: str) -> bool:
    """Check whether any rule in the field matches one of the given names.

    Checks both ``rule["type"]`` (real mappings) and ``rule["check"]``
    (test-fixture shorthand).

    Args:
        field_def: A field definition dict.
        rule_names: One or more rule type strings to match.

    Returns:
        True if at least one matching rule is found.
    """
    for rule in _get_rules(field_def):
        if rule.get("type") in rule_names or rule.get("check") in rule_names:
            return True
    return False


def _get_date_format(field_def: dict) -> str:
    """Extract the Python strftime format string from a field definition.

    Checks ``validation_rules`` (type=date_format with parameters.format)
    and ``rules`` shorthand (check=date_format with format key, using
    YYYYMMDD-style tokens converted to strftime).

    Args:
        field_def: A field definition dict.

    Returns:
        A strftime-compatible format string. Defaults to ``'%Y%m%d'``.
    """
    for rule in _get_rules(field_def):
        rule_type = rule.get("type") or rule.get("check") or ""
        if rule_type == "date_format":
            params = rule.get("parameters") or {}
            if params.get("format"):
                return params["format"]
            fmt = rule.get("format", "")
            if fmt:
                return _convert_date_token(fmt)
    return "%Y%m%d"


def _convert_date_token(token: str) -> str:
    """Convert YYYYMMDD-style token to strftime format.

    Args:
        token: Date format token (e.g. 'YYYYMMDD', 'YYYY-MM-DD', 'CCYYMMDD').

    Returns:
        Equivalent strftime string.
    """
    result = token
    result = result.replace("CCYY", "%Y").replace("YYYY", "%Y")
    result = result.replace("YY", "%y")
    result = result.replace("MM", "%m")
    result = result.replace("DD", "%d")
    return result


def _pad(value: str, length: int | None, data_type: str = "string") -> str:
    """Pad or truncate a value to the target length.

    Numeric types are right-justified with zero padding.
    String types are left-justified with space padding.

    Args:
        value: The raw value string.
        length: Target character width, or None for delimited fields.
        data_type: The field's data_type ('string', 'decimal', 'integer', 'date').

    Returns:
        Padded/truncated string, or original value if length is None.
    """
    if length is None:
        return value
    if data_type in ("decimal", "integer", "numeric"):
        return value[:length].rjust(length, "0")
    return value[:length].ljust(length)


def generate_field_value(field_def: dict, rng: random.Random) -> str:
    """Generate a synthetic value for a single mapping field definition.

    Priority order:
    1. ``default_value`` set -> use it (padded to length)
    2. ``valid_values`` non-empty -> ``rng.choice(valid_values)`` (padded)
    3. date type or ``date_format`` rule -> random date +/-2 years in declared format
    4. numeric type (decimal/integer) -> random integer, zero-padded to length
    5. ``not_null`` / ``not_empty`` rule -> random alphanumeric (at least 1 char)
    6. else -> random alphanumeric padded to length

    Args:
        field_def: One field dict from a mapping JSON.
        rng: Seeded Random instance for reproducibility.

    Returns:
        String value, padded/truncated to field length where applicable.
    """
    length = field_def.get("length")
    if length is not None:
        length = int(length)
    data_type = (field_def.get("data_type") or "string").lower()

    # 1. default_value
    default = field_def.get("default_value")
    if default is not None and str(default) != "":
        return _pad(str(default), length, data_type)

    # 2. valid_values
    valid_values = field_def.get("valid_values") or []
    if valid_values:
        chosen = str(rng.choice(valid_values))
        return _pad(chosen, length, data_type)

    # 3. date type
    is_date = data_type == "date" or _has_rule(field_def, "date_format")
    if is_date:
        fmt = _get_date_format(field_def)
        offset_days = rng.randint(-_DATE_RANGE_DAYS, _DATE_RANGE_DAYS)
        dt = _DATE_ANCHOR + timedelta(days=offset_days)
        val = dt.strftime(fmt)
        return _pad(val, length, "string")

    # 4. numeric type — generate value guaranteed to fit in length digits, then zero-pad
    if data_type in ("decimal", "integer", "numeric"):
        # Respect COBOL picture clause digit count when present (e.g. '9(12)' or 'S9(12)').
        # The digit count in the format may differ from the field length byte-width.
        fmt_str = str(field_def.get("format") or "").upper()
        m_pic = re.fullmatch(r"[S+]?9\((\d+)\)", fmt_str)
        if m_pic:
            digit_count = int(m_pic.group(1))
        else:
            digit_count = length or 8
        max_val = 10 ** digit_count - 1
        val = str(rng.randint(0, max_val)).rjust(digit_count, "0")
        # Then pad the full-width value to the declared field length
        if length is not None:
            val = val.rjust(length, "0")
        return val

    # 5. not_empty / not_null
    if _has_rule(field_def, "not_null", "not_empty"):
        effective_len = length or 10
        chars = string.ascii_uppercase + string.digits
        val_len = rng.randint(1, max(1, effective_len))
        val = "".join(rng.choice(chars) for _ in range(val_len))
        return _pad(val, length, data_type)

    # 6. fallback: random alphanumeric
    effective_len = length or 10
    chars = string.ascii_uppercase + string.digits + " "
    val = "".join(rng.choice(chars) for _ in range(effective_len))
    return _pad(val, length, data_type)


def generate_row(fields: list, rng: random.Random) -> dict:
    """Generate one row as {field_name: value} for all fields in a mapping.

    Args:
        fields: List of field definition dicts from mapping JSON.
        rng: Seeded Random instance.

    Returns:
        Dict mapping field name to generated string value.

    Raises:
        KeyError: If any field dict is missing the 'name' key.
    """
    return {f["name"]: generate_field_value(f, rng) for f in fields}


def generate_file(mapping: dict, row_count: int, seed: int = 42) -> list:
    """Generate *row_count* rows using field definitions from *mapping*.

    Args:
        mapping: Full mapping dict (must contain 'fields' key).
        row_count: Number of rows to generate.
        seed: Random seed for reproducibility.

    Returns:
        List of dicts, each mapping field name to string value.
    """
    rng = random.Random(seed)
    fields = mapping.get("fields", [])
    return [generate_row(fields, rng) for _ in range(row_count)]


# ---------------------------------------------------------------------------
# Error injection
# ---------------------------------------------------------------------------

_VALID_ERROR_TYPES = frozenset({
    "blank_required", "invalid_date", "duplicate_key",
    "invalid_value", "wrong_length",
})


def _find_first_field(fields: list, predicate) -> dict | None:
    """Return the first field dict matching predicate, or None.

    Args:
        fields: List of field definition dicts.
        predicate: Callable taking a field dict, returning bool.

    Returns:
        First matching field dict, or None if no match.
    """
    for f in fields:
        if predicate(f):
            return f
    return None


def _pick_rows(rng: random.Random, total: int, count: int, exclude: set | None = None) -> list:
    """Pick *count* unique row indices from [0, total), excluding *exclude*.

    Args:
        rng: Random instance.
        total: Number of rows available.
        count: Number of indices to pick.
        exclude: Set of indices to skip (default empty).

    Returns:
        List of selected row indices.
    """
    excluded = exclude or set()
    available = [i for i in range(total) if i not in excluded]
    return rng.sample(available, min(count, len(available)))


def inject_errors(
    rows: list,
    error_spec: dict,
    fields: list,
    rng: random.Random,
) -> list:
    """Inject controlled errors into previously generated rows.

    Modifies a deep copy of *rows* and returns it. The input list is unchanged.

    Supported error types:

    - ``blank_required``: Blanks the first required/not_null/not_empty field in N rows.
    - ``invalid_date``: Replaces the first date field with ``"99999999"`` in N rows.
    - ``duplicate_key``: Copies row 0's first required field value into N other rows.
    - ``invalid_value``: Replaces the first valid_values-constrained field with ``"ZZZZ"`` in N rows.
    - ``wrong_length``: Appends ``"X"`` to the first field in N rows (corrupts record width).

    Args:
        rows: List of {field_name: value} dicts to inject errors into.
        error_spec: Dict mapping error type name to row count.
        fields: Field definition list from the mapping.
        rng: Seeded Random instance for reproducible index selection.

    Returns:
        New list of rows with errors injected.

    Raises:
        ValueError: If an unrecognised error type is in *error_spec*.
    """
    rows = copy.deepcopy(rows)

    unknown = set(error_spec) - _VALID_ERROR_TYPES
    if unknown:
        raise ValueError("Unknown error injection type: " + repr(sorted(unknown)))

    total = len(rows)
    used: set = set()

    # blank_required
    count = error_spec.get("blank_required", 0)
    if count:
        target = _find_first_field(
            fields,
            lambda f: _has_rule(f, "not_null", "not_empty") or f.get("required"),
        )
        if target:
            length = target.get("length")
            blank = " " * int(length) if length else ""
            for idx in _pick_rows(rng, total, count, used):
                rows[idx][target["name"]] = blank
                used.add(idx)

    # invalid_date
    count = error_spec.get("invalid_date", 0)
    if count:
        target = _find_first_field(
            fields,
            lambda f: (f.get("data_type") or "").lower() == "date" or _has_rule(f, "date_format"),
        )
        if target:
            length = target.get("length")
            bad = "99999999"
            if length:
                bad = bad[:int(length)].ljust(int(length))
            for idx in _pick_rows(rng, total, count, used):
                rows[idx][target["name"]] = bad
                used.add(idx)

    # duplicate_key
    count = error_spec.get("duplicate_key", 0)
    if count and total > 1:
        target = _find_first_field(
            fields,
            lambda f: f.get("required") or _has_rule(f, "not_null", "not_empty"),
        )
        if target:
            source_val = rows[0][target["name"]]
            for idx in _pick_rows(rng, total, count, {0} | used):
                rows[idx][target["name"]] = source_val
                used.add(idx)

    # invalid_value
    count = error_spec.get("invalid_value", 0)
    if count:
        target = _find_first_field(fields, lambda f: bool(f.get("valid_values")))
        if target:
            length = target.get("length")
            bad = "ZZZZ"
            if length:
                bad = bad[:int(length)].ljust(int(length))
            for idx in _pick_rows(rng, total, count, used):
                rows[idx][target["name"]] = bad
                used.add(idx)

    # wrong_length
    count = error_spec.get("wrong_length", 0)
    if count and fields:
        first = fields[0]
        for idx in _pick_rows(rng, total, count, used):
            rows[idx][first["name"]] += "X"
            used.add(idx)

    return rows


# ---------------------------------------------------------------------------
# Multi-record generation
# ---------------------------------------------------------------------------


def generate_multi_record_file(
    config: dict,
    detail_rows: int,
    seed: int = 42,
) -> list:
    """Generate a multi-record file: 1 header + N detail rows + 1 trailer.

    Each record type's mapping JSON is loaded from the path specified in
    the config's ``record_types[key]["mapping"]`` key.

    Record ordering:
    - Record types whose key contains ``"header"`` -> emitted first (exactly once each)
    - All other non-trailer types -> emitted ``detail_rows`` times each
    - Record types whose key contains ``"trailer"`` -> emitted last (exactly once each)

    Discriminator values are forced from ``record_types[key]["match"]`` regardless
    of what the generator would produce for the first field.

    Args:
        config: Parsed multi-record YAML config dict. Must contain
            ``"record_types"`` with each entry having a ``"mapping"`` path
            and a ``"match"`` discriminator value.
        detail_rows: Number of detail-type rows to generate.
        seed: Random seed for reproducibility.

    Returns:
        List of ``(record_type_key, row_dict, fields_list)`` tuples in file
        order: ``[("header", {...}, [...]), ("detail", {...}, [...]), ...,
        ("trailer", {...}, [...])]``.

    Note:
        Multi-record output is always fixed-width. All record-type mappings referenced
        by the config are expected to have ``source.format == "fixed_width"``.
    """
    rng = random.Random(seed)
    record_types = config.get("record_types", {})

    header_keys = [k for k in record_types if "header" in k.lower()]
    trailer_keys = [k for k in record_types if "trailer" in k.lower()]
    detail_keys = [k for k in record_types if k not in header_keys and k not in trailer_keys]

    def _load_mapping(rt_config: dict) -> dict:
        with open(rt_config["mapping"], "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _make_typed_row(rt_key: str, rt_config: dict) -> tuple:
        mapping = _load_mapping(rt_config)
        fields = mapping.get("fields", [])
        row = generate_row(fields, rng)
        # Force discriminator value (first field = record type marker)
        disc_match = rt_config.get("match", "")
        if disc_match and fields:
            disc_field = fields[0]["name"]
            length = fields[0].get("length")
            row[disc_field] = _pad(disc_match, int(length) if length else None, "string")
        return (rt_key, row, fields)

    results: list = []

    for key in header_keys:
        results.append(_make_typed_row(key, record_types[key]))

    for key in detail_keys:
        for _ in range(detail_rows):
            results.append(_make_typed_row(key, record_types[key]))

    for key in trailer_keys:
        rt_config = record_types[key]
        mapping = _load_mapping(rt_config)
        fields = mapping.get("fields", [])
        row = generate_row(fields, rng)
        disc_match = rt_config.get("match", "")
        if disc_match and fields:
            disc_field = fields[0]["name"]
            length = fields[0].get("length")
            row[disc_field] = _pad(disc_match, int(length) if length else None, "string")
        # Auto-populate RECORD_COUNT if present
        for field in fields:
            if "count" in field["name"].lower():
                length = field.get("length")
                row[field["name"]] = _pad(str(detail_rows), int(length) if length else None, "decimal")
        results.append((key, row, fields))

    return results
