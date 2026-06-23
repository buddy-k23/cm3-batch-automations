"""Throwaway empirical profiler for Valdo non-chunked wide-file validation.

Generates representative wide inputs (300 fields), writes matching mapping +
rules configs, then runs the REAL non-chunked validate entry point
(``run_validate_service``, which is what ``validate_command`` calls for the
non-chunked path) under cProfile for four scenarios:

    {delimited, fixed-width} x {with business rules, without}

For each scenario it prints the top functions by cumulative time and by
tottime, the measured cost of the specifically-named hot-spot functions, and
the total wall-clock. All temp artefacts go under a tmp dir (default /tmp),
NOT into the repo. No src/ code is modified; no git is run.

Usage:
    .venv/bin/python scripts/profile_validate.py [--rows N] [--fields N] [--tmp DIR]
"""
from __future__ import annotations

import argparse
import cProfile
import json
import os
import pstats
import random
import string
import sys
import time
from io import StringIO
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

random.seed(1234)

# Field-name -> generator category. We build 300 fields with a realistic mix.
# Categories: numeric, date (named so the date-suspicion code fires), code
# (small valid_values domain), text (free text).
CODE_DOMAIN = ["100010", "100020", "100030", "100040", "100050"]
STATUS_DOMAIN = ["ACTIVE", "CLOSED", "SUSPENDED"]


def _rand_text(n: int = 12) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=n))


def build_field_plan(n_fields: int):
    """Return a list of (name, category) describing each generated column."""
    plan = []
    # A few well-known fields the rules will target.
    plan.append(("ACCT-NUM", "acct"))          # 10-digit numeric code
    plan.append(("LOCATION-CODE", "code"))      # small valid_values domain
    plan.append(("ACCT-STATUS", "status"))      # small valid_values domain
    plan.append(("CREDIT-LIMIT", "numeric"))    # numeric
    plan.append(("OPEN-DATE", "date"))          # date-like YYYYMMDD
    remaining = n_fields - len(plan)
    # Distribute the remaining ~mix: 30% numeric, 20% date, 20% code, 30% text.
    for i in range(remaining):
        r = i % 10
        if r < 3:
            cat = "numeric"
        elif r < 5:
            cat = "date"
        elif r < 7:
            cat = "code"
        else:
            cat = "text"
        plan.append((f"FIELD_{i:03d}_{cat.upper()}", cat))
    return plan


def gen_value(cat: str, row: int) -> str:
    if cat == "acct":
        return f"{random.randint(0, 9_999_999_999):010d}"
    if cat == "numeric":
        return str(random.randint(0, 100000))
    if cat == "date":
        # mostly valid YYYYMMDD, ~3% invalid to make date code do work
        if random.random() < 0.03:
            return "00000000"
        y = random.randint(1990, 2025)
        m = random.randint(1, 12)
        d = random.randint(1, 28)
        return f"{y:04d}{m:02d}{d:02d}"
    if cat == "code":
        return random.choice(CODE_DOMAIN)
    if cat == "status":
        return random.choice(STATUS_DOMAIN)
    # text
    return _rand_text()


# ------------------------------------------------------------------ delimited

def write_delimited(path: Path, plan, rows: int):
    names = [p[0] for p in plan]
    with open(path, "w", encoding="utf-8") as f:
        f.write("|".join(names) + "\n")
        for r in range(rows):
            f.write("|".join(gen_value(cat, r) for _, cat in plan) + "\n")


def write_delimited_mapping(path: Path, plan):
    fields = []
    for name, cat in plan:
        fld = {"name": name, "data_type": "String", "required": False}
        if cat in ("code",):
            fld["valid_values"] = list(CODE_DOMAIN)
        if cat == "status":
            fld["valid_values"] = list(STATUS_DOMAIN)
        fields.append(fld)
    cfg = {
        "mapping_name": "profile_delimited",
        "source": {"type": "file", "format": "pipe_delimited",
                   "delimiter": "|", "has_header": True},
        "fields": fields,
        "total_fields": len(fields),
    }
    path.write_text(json.dumps(cfg))


# ---------------------------------------------------------------- fixed-width

def _fw_len(cat: str) -> int:
    return {"acct": 10, "numeric": 8, "date": 8, "code": 6,
            "status": 9, "text": 12}.get(cat, 12)


def write_fixed_width(path: Path, plan, rows: int):
    widths = [_fw_len(cat) for _, cat in plan]
    with open(path, "w", encoding="utf-8") as f:
        for r in range(rows):
            parts = []
            for (name, cat), w in zip(plan, widths):
                v = gen_value(cat, r)
                # status uses 9-wide domain values; pad/truncate to width
                parts.append(v[:w].ljust(w))
            line = "".join(parts)
            # Inject ~2% length mismatches so misalignment scanner does work.
            if r % 50 == 0:
                line = line[:-3]
            f.write(line + "\n")


def write_fixed_width_mapping(path: Path, plan):
    fields = []
    pos = 1
    for name, cat in plan:
        length = _fw_len(cat)
        fld = {"name": name, "data_type": "string", "position": pos,
               "length": length, "required": False}
        if cat == "code":
            fld["valid_values"] = list(CODE_DOMAIN)
        if cat == "status":
            fld["valid_values"] = list(STATUS_DOMAIN)
        if cat == "numeric":
            fld["format"] = f"9({length})"
        fields.append(fld)
        pos += length
    cfg = {
        "mapping_name": "profile_fixed_width",
        "source": {"type": "file", "format": "fixed_width", "encoding": "UTF-8"},
        "fields": fields,
        "total_record_length": pos - 1,
    }
    path.write_text(json.dumps(cfg))


# --------------------------------------------------------------------- rules

def write_rules(path: Path):
    """A handful of business rules: field rules + cross-row unique + group_count."""
    rules = {
        "metadata": {"name": "profile_rules"},
        "rules": [
            {"id": "r001", "name": "Acct 10 digits", "type": "field_validation",
             "severity": "error", "operator": "regex", "field": "ACCT-NUM",
             "pattern": "^[0-9]{10}$", "enabled": True},
            {"id": "r002", "name": "Credit positive", "type": "field_validation",
             "severity": "warning", "operator": ">", "field": "CREDIT-LIMIT",
             "value": 0, "enabled": True},
            {"id": "r003", "name": "Status valid", "type": "field_validation",
             "severity": "warning", "operator": "in", "field": "ACCT-STATUS",
             "values": list(STATUS_DOMAIN), "enabled": True},
            {"id": "r004", "name": "Location valid", "type": "field_validation",
             "severity": "warning", "operator": "in", "field": "LOCATION-CODE",
             "values": list(CODE_DOMAIN), "enabled": True},
            {"id": "r005", "name": "Acct unique", "type": "cross_row",
             "severity": "error", "check": "unique", "field": "ACCT-NUM",
             "enabled": True},
            {"id": "r006", "name": "Status group count", "type": "cross_row",
             "severity": "warning", "check": "group_count",
             "key_field": "ACCT-STATUS", "count_field": "ACCT-NUM",
             "min_value": 1, "enabled": True},
        ],
    }
    path.write_text(json.dumps(rules))


# ------------------------------------------------------------------ profiling

# (label, file_basename, func_name) — matched exactly on the pstats key so a
# generic name like "parse" only matches the PARSER's parse(), never
# dateutil's _parse / pandas parse helpers.
HOTSPOTS = [
    ("parser.parse() [delimited]", "pipe_delimited_parser.py", "parse"),
    ("parser.parse() [fixed-width]", "fixed_width_parser.py", "parse"),
    ("_analyze_date_fields", "enhanced_validator.py", "_analyze_date_fields"),
    ("_analyze_fields", "enhanced_validator.py", "_analyze_fields"),
    ("_analyze_field", "enhanced_validator.py", "_analyze_field"),
    ("_infer_data_type", "enhanced_validator.py", "_infer_data_type"),
    ("_analyze_numeric_field", "enhanced_validator.py", "_analyze_numeric_field"),
    ("_analyze_string_field", "enhanced_validator.py", "_analyze_string_field"),
    ("_analyze_duplicates", "enhanced_validator.py", "_analyze_duplicates"),
    ("_calculate_quality_metrics", "enhanced_validator.py", "_calculate_quality_metrics"),
    ("_detect_first_misalignment_by_row", "enhanced_validator.py", "_detect_first_misalignment_by_row"),
    ("_validate_fixed_width_row_lengths", "enhanced_validator.py", "_validate_fixed_width_row_lengths"),
    ("_validate_strict_fixed_width", "enhanced_validator.py", "_validate_strict_fixed_width"),
    ("_validate_format", "enhanced_validator.py", "_validate_format"),
    ("_validate_schema", "enhanced_validator.py", "_validate_schema"),
    ("_profile_data", "enhanced_validator.py", "_profile_data"),
    ("_build_appendix_data", "enhanced_validator.py", "_build_appendix_data"),
    ("_detect_date_formats", "enhanced_validator.py", "_detect_date_formats"),
    ("_is_value_valid_for_format", "enhanced_validator.py", "_is_value_valid_for_format"),
    ("_validate_business_rules", "enhanced_validator.py", "_validate_business_rules"),
    ("RuleEngine.validate", "rule_engine.py", "validate"),
    ("CrossRowValidator.validate", "cross_row_validator.py", "validate"),
    ("pd.to_datetime (all callers)", "datetimes.py", "to_datetime"),
    ("pd.read_csv", "readers.py", "read_csv"),
    ("pd.read_fwf", "readers.py", "read_fwf"),
]


def run_scenario(name, file_path, mapping_path, rules_path):
    from src.services.validate_service import run_validate_service

    pr = cProfile.Profile()
    t0 = time.time()
    pr.enable()
    result = run_validate_service(
        file=str(file_path),
        mapping=str(mapping_path),
        rules=str(rules_path) if rules_path else None,
        output=None,
        detailed=True,
        use_chunked=False,
    )
    pr.disable()
    wall = time.time() - t0

    print("\n" + "=" * 78)
    print(f"SCENARIO: {name}")
    print(f"  total_rows={result.get('total_rows')} "
          f"error_count={result.get('error_count')} "
          f"warning_count={result.get('warning_count')}")
    print(f"  WALL CLOCK (profiled): {wall:.2f}s   "
          f"elapsed_seconds(reported)={result.get('elapsed_seconds')}")
    print("=" * 78)

    stats = pstats.Stats(pr)
    total_tt = sum(v[2] for v in stats.stats.values())  # sum of tottime

    # --- Top by cumulative ---
    print("\n  TOP 15 BY CUMULATIVE TIME:")
    print(f"  {'cumtime':>9} {'tottime':>9} {'ncalls':>10}  function")
    s = StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
    ps.print_stats(15)
    _print_parsed(s.getvalue(), wall)

    print("\n  TOP 15 BY TOTTIME (self time):")
    print(f"  {'cumtime':>9} {'tottime':>9} {'ncalls':>10}  function")
    s = StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("tottime")
    ps.print_stats(15)
    _print_parsed(s.getvalue(), wall)

    # --- Named hot-spot extraction (cumtime), exact (basename, func) match ---
    print("\n  NAMED HOT-SPOT MEASUREMENTS (cumtime, % of wall):")
    rows = {}  # (basename, func) -> aggregated [nc, tt, ct]
    for (fn_file, line, fname), (cc, nc, tt, ct, callers) in stats.stats.items():
        key = (os.path.basename(fn_file), fname)
        agg = rows.setdefault(key, [0, 0.0, 0.0])
        agg[0] += nc
        agg[1] += tt
        agg[2] += ct
    for label, base, func in HOTSPOTS:
        agg = rows.get((base, func))
        if not agg:
            continue
        ncalls, tot, cum = agg
        pct = cum / wall * 100 if wall else 0
        print(f"    {label:34s} cum={cum:7.2f}s tot={tot:7.2f}s "
              f"calls={ncalls:>9}  {pct:5.1f}% of wall")
    return wall, result


def _print_parsed(text, wall):
    """Extract the ncalls/tottime/cumtime table rows from pstats text output."""
    started = False
    count = 0
    for line in text.splitlines():
        ls = line.strip()
        if ls.startswith("ncalls"):
            started = True
            continue
        if not started or not ls:
            continue
        parts = ls.split(None, 5)
        if len(parts) < 6:
            continue
        ncalls, tottime, percall1, cumtime, percall2, func = parts
        try:
            tt = float(tottime)
            ct = float(cumtime)
        except ValueError:
            continue
        pct = ct / wall * 100 if wall else 0
        func_short = func
        if len(func_short) > 60:
            func_short = "..." + func_short[-57:]
        print(f"  {ct:9.2f} {tt:9.2f} {ncalls:>10}  {func_short} ({pct:.0f}%)")
        count += 1
        if count >= 15:
            break


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=100_000)
    ap.add_argument("--fields", type=int, default=300)
    ap.add_argument("--tmp", default="/tmp/valdo_profile")
    args = ap.parse_args()

    tmp = Path(args.tmp)
    tmp.mkdir(parents=True, exist_ok=True)

    plan = build_field_plan(args.fields)
    print(f"Generating {args.fields} fields x {args.rows} rows ...")
    cats = {}
    for _, c in plan:
        cats[c] = cats.get(c, 0) + 1
    print(f"  field category mix: {cats}")

    delim_file = tmp / "wide_delimited.psv"
    delim_map = tmp / "delim_mapping.json"
    fw_file = tmp / "wide_fixed.dat"
    fw_map = tmp / "fw_mapping.json"
    rules_file = tmp / "rules.json"

    t = time.time()
    write_delimited(delim_file, plan, args.rows)
    write_delimited_mapping(delim_map, plan)
    write_fixed_width(fw_file, plan, args.rows)
    write_fixed_width_mapping(fw_map, plan)
    write_rules(rules_file)
    print(f"  generation took {time.time()-t:.1f}s; "
          f"delim={delim_file.stat().st_size/1e6:.0f}MB "
          f"fw={fw_file.stat().st_size/1e6:.0f}MB")

    summary = []
    for name, dfile, mfile, rfile in [
        ("DELIMITED  - no rules",   delim_file, delim_map, None),
        ("DELIMITED  - with rules", delim_file, delim_map, rules_file),
        ("FIXEDWIDTH - no rules",   fw_file,    fw_map,    None),
        ("FIXEDWIDTH - with rules", fw_file,    fw_map,    rules_file),
    ]:
        wall, _ = run_scenario(name, dfile, mfile, rfile)
        summary.append((name, wall))

    print("\n" + "#" * 78)
    print("WALL-CLOCK SUMMARY")
    for name, wall in summary:
        print(f"  {name:28s} {wall:7.2f}s")
    print("#" * 78)


if __name__ == "__main__":
    main()
