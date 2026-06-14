#!/usr/bin/env python3
"""Bulk convert rules templates (CSV/XLS/XLSX) to rules JSON files."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
import sys

from src.config.ba_rules_template_converter import BARulesTemplateConverter
from src.config.rules_template_converter import RulesTemplateConverter
import pandas as pd

SUPPORTED_EXTS = {".csv", ".xlsx", ".xls"}

# Rule-ID formats accepted by the strict validator. Per
# ``prompts/generate-rules-csv.md`` the canonical formats are ``R001`` for
# per-row field rules and ``CR001`` for cross-row rules. ``BR001`` is
# accepted for backward compatibility with earlier BA templates that used
# the ``BR`` prefix.
_RULE_ID_RE = re.compile(r"^(?:R|BR|CR)\d+$")

# Rule types from the BA-friendly converter whose ``Expected / Values``
# cell is legitimately blank — the rule semantics carry no value
# parameter. Anything outside this set must supply a non-empty value.
# Source: ``prompts/generate-rules-csv.md`` Rule Type Reference table.
_RULE_TYPES_WITHOUT_EXPECTED: frozenset[str] = frozenset(
    {
        "not_empty",
        "numeric",
        "required",
        "cross_row:unique",
        "cross_row:unique_composite",
        "cross_row:consistent",
        "cross_row:group_count",
    }
)


def load_template_df(template_path: Path) -> pd.DataFrame:
    if template_path.suffix.lower() == ".csv":
        return pd.read_csv(template_path, dtype=str)
    return pd.read_excel(template_path, dtype=str)


def validate_template_strict(template_path: Path) -> tuple[list[dict], str]:
    """Return row-level validation errors and detected template kind.

    Returns:
        (issues, kind) where kind is one of: ba_friendly, standard
    """
    df = load_template_df(template_path)
    df.columns = [c.strip() for c in df.columns]

    ba_required = ['Rule ID', 'Rule Name', 'Field', 'Rule Type', 'Severity', 'Expected / Values', 'Enabled']
    std_required = ['Rule ID', 'Rule Name', 'Description', 'Type', 'Severity', 'Operator']

    issues: list[dict] = []

    is_ba = all(c in df.columns for c in ba_required)
    kind = 'ba_friendly' if is_ba else 'standard'

    required_columns = ba_required if is_ba else std_required
    valid_severities = {'error', 'warning', 'info'}

    missing_headers = [c for c in required_columns if c not in df.columns]
    if missing_headers:
        issues.append({
            'row': 'HEADER',
            'field': '<headers>',
            'issue': f'Missing required headers: {missing_headers}',
            'value': ''
        })
        return issues, kind

    # ``Expected / Values`` must exist as a header (it is part of the BA
    # schema), but the cell is allowed to be empty when the rule type
    # does not consume a value parameter. The per-row checks below enforce
    # the rule-type-aware semantics; the column is excluded here from the
    # blanket "no empty values" sweep.
    per_row_required_columns = [
        c for c in required_columns if c != 'Expected / Values'
    ]

    # duplicate Rule ID check
    dup_ids = df['Rule ID'].dropna().astype(str).str.strip()
    dup_ids = dup_ids[dup_ids.duplicated()]
    for rid in sorted(set(dup_ids.tolist())):
        issues.append({'row': 'MULTI', 'field': 'Rule ID', 'issue': 'Duplicate Rule ID', 'value': rid})

    for idx, row in df.iterrows():
        row_no = idx + 2

        rid = (row.get('Rule ID') or '').strip() if pd.notna(row.get('Rule ID')) else ''
        # Per ``prompts/generate-rules-csv.md``: ``R001`` for field rules,
        # ``CR001`` for cross-row rules. ``BR001`` accepted for legacy
        # templates.
        if rid and not _RULE_ID_RE.match(rid):
            issues.append({'row': row_no, 'field': 'Rule ID', 'issue': 'Invalid Rule ID format', 'value': rid})

        for c in per_row_required_columns:
            v = (row.get(c) or '').strip() if pd.notna(row.get(c)) else ''
            if not v:
                issues.append({'row': row_no, 'field': c, 'issue': 'Required value is empty', 'value': ''})

        if is_ba:
            sev_v = (row.get('Severity') or '').strip().lower() if pd.notna(row.get('Severity')) else ''
            if sev_v and sev_v not in valid_severities:
                issues.append({'row': row_no, 'field': 'Severity', 'issue': 'Invalid severity', 'value': sev_v})

            cond_v = (row.get('Condition (optional)') or '').strip() if pd.notna(row.get('Condition (optional)')) else ''
            if '~~' in cond_v:
                issues.append({'row': row_no, 'field': 'Condition (optional)', 'issue': 'Invalid condition syntax', 'value': cond_v})

            # Rule-type-aware ``Expected / Values`` check. Empty is fine
            # for the value-less rule types listed in
            # ``_RULE_TYPES_WITHOUT_EXPECTED``; required for all others.
            rt_v = (row.get('Rule Type') or '').strip().lower() if pd.notna(row.get('Rule Type')) else ''
            ev_v = (row.get('Expected / Values') or '').strip() if pd.notna(row.get('Expected / Values')) else ''
            if rt_v and not ev_v and rt_v not in _RULE_TYPES_WITHOUT_EXPECTED:
                issues.append({
                    'row': row_no,
                    'field': 'Expected / Values',
                    'issue': f"Required value is empty for rule type '{rt_v}'",
                    'value': '',
                })
        else:
            type_v = (row.get('Type') or '').strip().lower() if pd.notna(row.get('Type')) else ''
            sev_v = (row.get('Severity') or '').strip().lower() if pd.notna(row.get('Severity')) else ''
            valid_types = {'field_validation', 'cross_field'}
            if type_v and type_v not in valid_types:
                issues.append({'row': row_no, 'field': 'Type', 'issue': 'Invalid type', 'value': type_v})
            if sev_v and sev_v not in valid_severities:
                issues.append({'row': row_no, 'field': 'Severity', 'issue': 'Invalid severity', 'value': sev_v})

    return issues, kind


def write_error_report(report_dir: Path, template_path: Path, issues: list[dict]) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{template_path.stem}.errors.csv"
    pd.DataFrame(issues, columns=['row', 'field', 'issue', 'value']).to_csv(report_path, index=False)
    return report_path


def convert_file(template_path: Path, output_dir: Path, kind: str = "standard") -> Path:
    """Convert a single rules template to its JSON form.

    Selects the converter that matches the template's detected ``kind``
    (returned by :func:`validate_template_strict`):

    - ``ba_friendly`` → :class:`BARulesTemplateConverter` (BA-facing columns:
      ``Rule Type``, ``Expected / Values``, optional ``Condition``).
    - ``standard``    → :class:`RulesTemplateConverter`   (engineering
      columns: ``Type``, ``Operator``, ``Description``).

    Previously the script always instantiated ``RulesTemplateConverter``,
    silently misinterpreting BA-friendly templates and emitting malformed
    rules JSON. See handover open follow-up #7 in
    ``docs/handover/SHAW_source_onboarding_session.md``.
    """
    if kind == "ba_friendly":
        converter: object = BARulesTemplateConverter()
    else:
        converter = RulesTemplateConverter()

    if template_path.suffix.lower() == ".csv":
        converter.from_csv(str(template_path))
    else:
        converter.from_excel(str(template_path))

    out_path = output_dir / f"{template_path.stem}.json"
    converter.save(str(out_path))
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default="rules/csv", help="Directory containing rules CSV/Excel templates")
    parser.add_argument("--output-dir", default="config/rules", help="Directory to write rules JSON files")
    parser.add_argument("--error-report-dir", default="reports/template_validation",
                        help="Directory to write strict validation error reports")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)

    if not input_dir.exists():
        print(f"❌ Input directory not found: {input_dir}")
        return 1

    templates = sorted(p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS)
    if not templates:
        print(f"⚠️ No rules templates found in {input_dir} (supported: {sorted(SUPPORTED_EXTS)})")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    error_report_dir = Path(args.error_report_dir)

    success = 0
    failed = 0
    for template in templates:
        issues, kind = validate_template_strict(template)
        if issues:
            report = write_error_report(error_report_dir, template, issues)
            print(f"❌ Validation failed for {template.name}. Report: {report}")
            failed += 1
            continue

        try:
            out_path = convert_file(template, output_dir, kind=kind)
            print(f"✅ {template.name} ({kind}) -> {out_path}")
            success += 1
        except Exception as exc:
            print(f"❌ Failed to convert {template}: {exc}")
            failed += 1

    print(f"\nDone. Converted: {success}, Failed: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
