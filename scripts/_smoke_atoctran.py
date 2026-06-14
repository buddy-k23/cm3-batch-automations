"""One-shot smoke recipe for the SHAW ATOCTRAN multi-record validator.

Mirrors the reproduction recipe in
``docs/handover/SHAW_atoctran_smoke_findings.md``. Kept as a tiny script
because PowerShell quote-escaping makes the equivalent ``python -c``
one-liner fragile on Windows.

This is a developer aid, not part of any pipeline; safe to delete after
the ADR 0006 / ADR 0007 work is signed off.
"""

from __future__ import annotations

import sys

import yaml

from src.config.multi_record_config import MultiRecordConfig
from src.validators.multi_record_validator import MultiRecordValidator


def main() -> int:
    """Run the smoke validation and print a per-record summary."""
    umbrella_path = "config/mappings/SHAW_ATOCTRAN.yaml"
    sample_path = "data/samples/atoctran_shaw_20260514.txt"

    with open(umbrella_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f.read())

    cfg = MultiRecordConfig(**raw)
    result = MultiRecordValidator().validate(sample_path, cfg)

    print(f"valid={result['valid']}  total_rows={result['total_rows']}")
    print(f"cross_type_violations={len(result.get('cross_type_violations', []))}")
    print()
    print("Per-record-type results:")
    for key, value in sorted(result.get("record_type_results", {}).items()):
        # Per-group results come from run_validate_service via
        # _validate_record_group, so the row count key is ``total_rows``
        # (not ``row_count`` — that key only appears on the no-mapping
        # fallback branch). Surface every count field we can find.
        total = value.get("total_rows", value.get("row_count"))
        valid_rows = value.get("valid_rows")
        err_count = value.get("error_count")
        warn_count = value.get("warning_count")
        is_valid = value.get("valid")
        ics = value.get("issue_code_summary") or {}
        print(
            f"  {key}: total_rows={total} valid_rows={valid_rows} "
            f"errors={err_count} warnings={warn_count} valid={is_valid} "
            f"issue_codes={dict(ics) if ics else '{}'}"
        )

    # Surface the top 5 issue codes overall (sorted by count desc).
    print()
    print("Top issue codes across all record types:")
    code_totals: dict[str, int] = {}
    for value in result.get("record_type_results", {}).values():
        for code, count in (value.get("issue_code_summary") or {}).items():
            code_totals[code] = code_totals.get(code, 0) + count
    for code, count in sorted(code_totals.items(), key=lambda kv: -kv[1])[:5]:
        print(f"  {code}: {count}")

    # Sample the first few errors from each non-empty group so we can see
    # what is actually failing now that the rule engine and the
    # converter both behave.
    print()
    print("Sample errors per record type (first 3 per group):")
    for key, value in sorted(result.get("record_type_results", {}).items()):
        errors = value.get("errors") or []
        if not errors:
            continue
        # ``errors`` may be a list of dicts or ints; coerce to dict view.
        print(f"  {key} (showing {min(3, len(errors))}/{len(errors)}):")
        for err in errors[:3]:
            if isinstance(err, dict):
                print(
                    f"    code={err.get('code')} row={err.get('row')} "
                    f"field={err.get('field')} "
                    f"msg={(err.get('message') or '')[:120]}"
                )
            else:
                print(f"    {err}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
