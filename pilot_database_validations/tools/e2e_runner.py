#!/usr/bin/env python3
"""Wave 2 end-to-end runner.

Pipeline:
1) template input(s) -> canonical ingest JSON
2) canonical ingest JSON -> mapping/rules JSON + conversion report
3) execute rule validations and produce summary JSON + detail CSV + telemetry JSON

Determinism:
- canonical/mapping/rules/conversion/summary/telemetry outputs are stable for same inputs
  when --deterministic is enabled (default).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

try:
    from tools.generate_contracts import generate
    from tools.promotion_gate import PromotionInputs, PromotionPolicy, build_promotion_evidence
    from tools.template_parser import DerivationConfig, ValidationError, parse_template_with_report
except ModuleNotFoundError:
    from generate_contracts import generate
    from promotion_gate import PromotionInputs, PromotionPolicy, build_promotion_evidence
    from template_parser import DerivationConfig, ValidationError, parse_template_with_report

DETERMINISTIC_TIMESTAMP = "1970-01-01T00:00:00+00:00"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _stable_run_id(canonical: dict[str, Any], version: str, owner: str) -> str:
    digest = hashlib.sha256(_canonical_bytes(canonical) + f"|{version}|{owner}".encode("utf-8")).hexdigest()
    return f"run-{digest[:12]}"


def _as_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if re.fullmatch(r"[-+]?\d+(\.\d+)?", text):
        return float(text)
    return None


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _build_records(canonical: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for idx, row in enumerate(canonical.get("mappingRows", []), start=1):
        rec: dict[str, Any] = {"__index__": idx}
        for key, value in row.items():
            if key.startswith("__"):
                continue
            rec[_normalize_key(key)] = value
        target = str(row.get("targetField", "")).strip()
        if target:
            fallback_value = row.get("defaultValue") if row.get("defaultValue") not in (None, "") else row.get("sourceField")
            rec[_normalize_key(target)] = fallback_value
            rec[target.upper()] = fallback_value
        records.append(rec)
    return records


def _resolve_value(token: str, context: dict[str, Any]) -> Any:
    t = token.strip()
    if t.startswith("'") and t.endswith("'"):
        return t[1:-1]
    if t.startswith('"') and t.endswith('"'):
        return t[1:-1]
    if t.lower() in {"true", "false"}:
        return t.lower() == "true"
    n = _as_number(t)
    if n is not None:
        return n

    if t in context:
        return context[t]
    nt = _normalize_key(t)
    if nt in context:
        return context[nt]
    if t.upper() in context:
        return context[t.upper()]

    dotted = t.split(".")
    cur: Any = context
    for part in dotted:
        if not isinstance(cur, dict):
            return None
        if part in cur:
            cur = cur[part]
            continue
        np = _normalize_key(part)
        if np in cur:
            cur = cur[np]
            continue
        if part.upper() in cur:
            cur = cur[part.upper()]
            continue
        return None
    return cur


def _compare_expression(expr: str, context: dict[str, Any]) -> bool | None:
    m = re.fullmatch(r"\s*(.+?)\s*(<=|>=|==|!=|<|>)\s*(.+?)\s*", expr)
    if not m:
        return None
    left_raw, op, right_raw = m.groups()
    left = _resolve_value(left_raw, context)
    right = _resolve_value(right_raw, context)
    if left is None or right is None:
        return None

    ln = _as_number(left)
    rn = _as_number(right)
    if ln is not None and rn is not None:
        left, right = ln, rn

    try:
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == "<":
            return left < right
        if op == ">":
            return left > right
        if op == "<=":
            return left <= right
        if op == ">=":
            return left >= right
    except TypeError:
        return None
    return None


def _evaluate_group_rule(expression: str, records: list[dict[str, Any]], context: dict[str, Any]) -> bool | None:
    count_where = re.fullmatch(r"\s*count\(where\(([^=\s]+)\s*=\s*'([^']+)'\)\)\s*==\s*(\d+)\s*", expression, flags=re.IGNORECASE)
    if count_where:
        field, expected_value, expected_count = count_where.groups()
        values = [_resolve_value(field, r) for r in records]
        if all(v is None for v in values):
            return None
        observed = sum(1 for v in values if str(v) == expected_value)
        return observed == int(expected_count)

    count_any = re.fullmatch(r"\s*count\(\*\)\s*(<=|>=|==|!=|<|>)\s*(\d+)\s*", expression, flags=re.IGNORECASE)
    if count_any:
        op, rhs = count_any.groups()
        return _compare_expression(f"{len(records)} {op} {rhs}", context)

    strict_desc = re.fullmatch(r"\s*is_strict_desc\(([^)]+)\)\s*", expression, flags=re.IGNORECASE)
    if strict_desc:
        field = strict_desc.group(1).strip()
        seq = [_resolve_value(field, r) for r in records]
        nums = [_as_number(v) for v in seq]
        if any(v is None for v in nums):
            return None
        return all(nums[i] > nums[i + 1] for i in range(len(nums) - 1))

    return _compare_expression(expression, context)


def _evaluate_file_rule(expression: str, context: dict[str, Any]) -> bool | None:
    return _compare_expression(expression, context)


def _evaluate_record_rule(expression: str, record: dict[str, Any]) -> bool | None:
    return _compare_expression(expression, record)


def _execute_validations(canonical: dict[str, Any], rules_payload: dict[str, Any], run_id: str) -> dict[str, Any]:
    records = _build_records(canonical)
    file_cfg = canonical.get("fileConfig", {})
    file_context = {
        "header": {
            "total_count": _as_number(file_cfg.get("headerTotalCountField")),
            "enabled": bool(file_cfg.get("headerEnabled")),
        },
        "detail": {"count": len(records)},
    }

    violations: list[dict[str, Any]] = []
    validated = 0

    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {(): records}

    for rule in sorted(rules_payload.get("rules", []), key=lambda x: (int(x.get("priority", 0)), str(x.get("ruleId", "")))):
        if not rule.get("enabled", True):
            continue

        scope = rule.get("scope")
        expression = str(rule.get("expression", "")).strip()
        rule_id = str(rule.get("ruleId", ""))
        severity = str(rule.get("severity", "INFO"))
        message = str(rule.get("messageTemplate", "Validation failed"))

        if scope in {"field", "record"}:
            for rec in records:
                validated += 1
                ok = _evaluate_record_rule(expression, rec)
                if ok is False:
                    violations.append(
                        {
                            "runId": run_id,
                            "ruleId": rule_id,
                            "severity": severity,
                            "scope": scope,
                            "recordRef": f"record:{rec.get('__index__', '')}",
                            "message": message,
                        }
                    )

        elif scope == "group":
            group_by = rule.get("groupBy", []) or []
            grouped = {}
            for rec in records:
                key = tuple(_resolve_value(str(k), rec) for k in group_by)
                grouped.setdefault(key, []).append(rec)

            for key, members in sorted(grouped.items(), key=lambda x: repr(x[0])):
                validated += 1
                context = {"group": {"count": len(members), "key": list(key)}, "detail": {"count": len(records)}}
                ok = _evaluate_group_rule(expression, members, context)
                if ok is False:
                    violations.append(
                        {
                            "runId": run_id,
                            "ruleId": rule_id,
                            "severity": severity,
                            "scope": scope,
                            "recordRef": f"group:{'|'.join('' if v is None else str(v) for v in key)}",
                            "message": message,
                        }
                    )

        elif scope == "file":
            validated += 1
            ok = _evaluate_file_rule(expression, file_context)
            if ok is False:
                violations.append(
                    {
                        "runId": run_id,
                        "ruleId": rule_id,
                        "severity": severity,
                        "scope": scope,
                        "recordRef": "file",
                        "message": message,
                    }
                )

    fail_count = sum(1 for v in violations if v["severity"] == "ERROR")
    warn_count = sum(1 for v in violations if v["severity"] == "WARN")
    passed = max(validated - fail_count - warn_count, 0)

    by_rule = []
    for rule in rules_payload.get("rules", []):
        rid = rule.get("ruleId", "")
        by_rule.append(
            {
                "ruleId": rid,
                "severity": rule.get("severity", "INFO"),
                "failCount": sum(1 for v in violations if v["ruleId"] == rid),
            }
        )

    top_failing = sorted(
        [{"ruleId": r["ruleId"], "count": r["failCount"]} for r in by_rule if r["failCount"] > 0],
        key=lambda x: (-x["count"], x["ruleId"]),
    )[:5]

    return {
        "violations": violations,
        "summary": {
            "validated": validated,
            "passed": passed,
            "failed": fail_count,
            "warned": warn_count,
            "topFailingRules": top_failing,
        },
        "byRule": by_rule,
        "source": {"rowCount": len(records)},
    }


def _write_detail_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["runId", "ruleId", "severity", "scope", "recordRef", "message"])
        for row in rows:
            writer.writerow([row["runId"], row["ruleId"], row["severity"], row["scope"], row["recordRef"], row["message"]])


def _build_summary_report(run_id: str, status: str, quality: dict[str, Any]) -> dict[str, Any]:
    return {
        "runId": run_id,
        "status": status,
        "summary": {
            "validated": int(quality.get("validated", 0)),
            "passed": int(quality.get("passed", 0)),
            "failed": int(quality.get("failed", 0)),
            "warned": int(quality.get("warned", 0)),
            "topFailingRules": quality.get("topFailingRules", []),
        },
        "artifacts": {
            "summaryJson": "summary-report.json",
            "detailCsv": "detail-violations.csv",
            "telemetryJson": "telemetry.json",
        },
    }


def _build_telemetry(run_id: str, version: str, ts: str, quality: dict[str, Any], by_rule: list[dict[str, Any]], row_count: int) -> dict[str, Any]:
    return {
        "runId": run_id,
        "timestamp": ts,
        "mappingVersion": version,
        "rulePackVersion": version,
        "phases": {
            "extractMs": 0,
            "validateMs": 0,
            "reportMs": 0,
            "totalMs": 0,
        },
        "quality": {
            "validated": int(quality.get("validated", 0)),
            "passed": int(quality.get("passed", 0)),
            "failed": int(quality.get("failed", 0)),
            "warned": int(quality.get("warned", 0)),
            "byRule": by_rule,
        },
        "source": {
            "system": "template-runner",
            "queryHash": "",
            "rowCount": int(row_count),
        },
        "system": {
            "peakMemoryMb": 0,
            "recordsPerSecond": 0,
        },
    }


def run_pipeline(
    input_path: Path,
    out_dir: Path,
    rules_input: Path | None,
    file_config_input: Path | None,
    version: str,
    owner: str,
    deterministic: bool,
    source_query_file: Path | None = None,
    derivation_config: DerivationConfig | None = None,
    promotion_gate_enabled: bool = False,
    promotion_policy: PromotionPolicy | None = None,
    template_completeness_ba: float = 100.0,
    template_completeness_qa: float = 100.0,
    required_columns_completeness: float = 100.0,
    warn_signoff_ba: bool = False,
    warn_signoff_qa: bool = False,
    warn_waiver_age_days: int | None = None,
) -> dict[str, Any]:
    parse_result = parse_template_with_report(input_path, rules_input, file_config_input, derivation_config=derivation_config)
    canonical = parse_result["payload"]
    lineage_report = parse_result.get("conversion", {}).get("lineage", {})

    out_dir.mkdir(parents=True, exist_ok=True)
    canonical_path = out_dir / "template-ingest.json"
    _write_json(canonical_path, canonical)

    run_id = _stable_run_id(canonical, version=version, owner=owner)
    ts = DETERMINISTIC_TIMESTAMP if deterministic else ""

    conversion_report = generate(
        input_path=canonical_path,
        out_dir=out_dir,
        version=version,
        owner=owner,
        generated_at=(DETERMINISTIC_TIMESTAMP if deterministic else None),
        input_ref=canonical_path.name,
        conversion_warnings=parse_result["conversion"]["warnings"],
    )

    # Convention: inject source query from --source-query-file or sibling source-query.sql
    mapping_path = out_dir / "mapping.json"
    mapping_payload = json.loads(mapping_path.read_text(encoding="utf-8"))
    query_path = source_query_file
    if query_path is None:
        candidate = input_path.parent / "source-query.sql"
        if candidate.exists():
            query_path = candidate
    if query_path and query_path.exists():
        query_text = query_path.read_text(encoding="utf-8").strip()
        if query_text:
            mapping_payload.setdefault("source", {})["query"] = query_text
            _write_json(mapping_path, mapping_payload)

    rules_payload = json.loads((out_dir / "rules.json").read_text(encoding="utf-8"))

    validation = _execute_validations(canonical=canonical, rules_payload=rules_payload, run_id=run_id)
    _write_detail_csv(out_dir / "detail-violations.csv", validation["violations"])
    _write_json(out_dir / "lineage-report.json", lineage_report)

    conversion_status = conversion_report.get("status") if conversion_report.get("status") in {"SUCCESS", "WARN"} else "FAILED"
    has_error_violations = validation["summary"]["failed"] > 0
    has_warn_violations = validation["summary"]["warned"] > 0
    if conversion_status == "FAILED" or has_error_violations:
        pre_gate_status = "FAILED"
    elif conversion_status == "WARN" or has_warn_violations:
        pre_gate_status = "WARN"
    else:
        pre_gate_status = "SUCCESS"

    policy = promotion_policy or PromotionPolicy()
    gate_evidence = build_promotion_evidence(
        run_id=run_id,
        policy=policy,
        enabled=promotion_gate_enabled,
        inputs=PromotionInputs(
            pre_gate_status=pre_gate_status,
            hard_error_count=int(conversion_report.get("summary", {}).get("errors", 0)) + int(validation["summary"]["failed"]),
            open_warn_count=int(conversion_report.get("summary", {}).get("warnings", 0)) + int(validation["summary"]["warned"]),
            template_completeness_ba=template_completeness_ba,
            template_completeness_qa=template_completeness_qa,
            required_columns_completeness=required_columns_completeness,
            warn_signoff_ba=warn_signoff_ba,
            warn_signoff_qa=warn_signoff_qa,
            warn_waiver_age_days=warn_waiver_age_days,
        ),
    )
    _write_json(out_dir / "promotion-evidence.json", gate_evidence)

    final_status = gate_evidence["evaluation"]["decision"] if promotion_gate_enabled else pre_gate_status
    summary = _build_summary_report(run_id=run_id, status=final_status, quality=validation["summary"])
    summary["promotion"] = {
        "enabled": promotion_gate_enabled,
        "decision": gate_evidence["evaluation"]["decision"],
        "preGateStatus": pre_gate_status,
        "evidenceJson": "promotion-evidence.json",
    }
    _write_json(out_dir / "summary-report.json", summary)

    telemetry = _build_telemetry(
        run_id=run_id,
        version=version,
        ts=ts or DETERMINISTIC_TIMESTAMP,
        quality=validation["summary"],
        by_rule=validation["byRule"],
        row_count=validation["source"]["rowCount"],
    )
    _write_json(out_dir / "telemetry.json", telemetry)

    return {
        "status": summary["status"],
        "runId": run_id,
        "artifacts": {
            "canonical": str(canonical_path),
            "mapping": str(out_dir / "mapping.json"),
            "rules": str(out_dir / "rules.json"),
            "conversion": str(out_dir / "conversion-report.json"),
            "lineage": str(out_dir / "lineage-report.json"),
            "promotionEvidence": str(out_dir / "promotion-evidence.json"),
            "summary": str(out_dir / "summary-report.json"),
            "detailCsv": str(out_dir / "detail-violations.csv"),
            "telemetry": str(out_dir / "telemetry.json"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run end-to-end template -> contracts -> validation pipeline")
    parser.add_argument("--input", required=True, help="Primary template path (.csv or .xlsx)")
    parser.add_argument("--rules-input", help="Optional rules template path (.csv or .xlsx)")
    parser.add_argument("--file-config-input", help="Optional file-config template path (.csv or .xlsx)")
    parser.add_argument("--source-query-file", help="Optional SQL file path. If omitted, auto-load sibling source-query.sql when present")
    parser.add_argument("--out-dir", default="generated", help="Output directory for all artifacts")
    parser.add_argument("--version", default="0.1.0", help="Version stamp for generated mapping/rules")
    parser.add_argument("--owner", default="contract-generator", help="Default owner for generated rules")
    parser.add_argument(
        "--deterministic",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable deterministic timestamps and stable artifact content (default: enabled)",
    )
    parser.add_argument("--derive-missing", action=argparse.BooleanOptionalAction, default=False, help="Enable deterministic derivation for missing transaction_code/source_field")
    parser.add_argument("--derive-transaction-code-mode", default="sheet_name", choices=["sheet_name", "column_fallback", "placeholder"], help="transaction_code derivation mode")
    parser.add_argument("--derive-source-field-mode", default="target_field", choices=["target_field", "definition", "column_fallback", "lineage_hardening", "placeholder"], help="source_field derivation mode")
    parser.add_argument("--lineage-max-placeholder-ratio", type=float, default=0.02, help="Lineage threshold for placeholder source ratio")
    parser.add_argument("--lineage-max-low-confidence-ratio", type=float, default=0.15, help="Lineage threshold for low-confidence source ratio")
    parser.add_argument("--transaction-code-placeholder", default="UNRESOLVED_TXN", help="Fallback transaction code placeholder")
    parser.add_argument("--source-field-placeholder", default="UNRESOLVED_SOURCE", help="Fallback source field placeholder")

    parser.add_argument("--promotion-gate", action=argparse.BooleanOptionalAction, default=False, help="Enable Wave 4 promotion gate enforcement (WARN->PASS/FAIL)")
    parser.add_argument("--warn-acceptance-mode", default="review-required", choices=["block", "review-required", "auto-accept"], help="WARN acceptance mode policy knob")
    parser.add_argument("--warn-acceptance-max-open", type=int, default=0, help="Maximum open WARN count allowed")
    parser.add_argument("--warn-acceptance-expiry-days", type=int, default=7, help="WARN waiver expiry window in days")
    parser.add_argument("--min-template-completeness-ba", type=float, default=95.0, help="Minimum BA completeness percentage")
    parser.add_argument("--min-template-completeness-qa", type=float, default=95.0, help="Minimum QA completeness percentage")
    parser.add_argument("--min-template-completeness-required-columns", type=float, default=100.0, help="Minimum required-column completeness percentage")
    parser.add_argument("--template-completeness-ba", type=float, default=100.0, help="Observed BA completeness percentage")
    parser.add_argument("--template-completeness-qa", type=float, default=100.0, help="Observed QA completeness percentage")
    parser.add_argument("--required-columns-completeness", type=float, default=100.0, help="Observed required-column completeness percentage")
    parser.add_argument("--warn-signoff-ba", action=argparse.BooleanOptionalAction, default=False, help="BA signoff for WARN acceptance")
    parser.add_argument("--warn-signoff-qa", action=argparse.BooleanOptionalAction, default=False, help="QA signoff for WARN acceptance")
    parser.add_argument("--warn-waiver-age-days", type=int, help="Age in days for the active WARN waiver")
    parser.add_argument("--derivation-default-transaction-code", default="disabled", choices=["disabled", "enabled"], help="Policy knob for default transaction-code derivation")
    parser.add_argument("--derivation-default-source-field", default="disabled", choices=["disabled", "enabled"], help="Policy knob for default source-field derivation")
    parser.add_argument("--derivation-mode-on-enable", default="placeholder", choices=["sheet_name", "column_fallback", "target_field", "definition", "lineage_hardening", "placeholder"], help="Policy fallback mode when derivation is enabled")
    args = parser.parse_args()

    try:
        result = run_pipeline(
            input_path=Path(args.input),
            out_dir=Path(args.out_dir),
            rules_input=Path(args.rules_input) if args.rules_input else None,
            file_config_input=Path(args.file_config_input) if args.file_config_input else None,
            version=args.version,
            owner=args.owner,
            deterministic=args.deterministic,
            source_query_file=Path(args.source_query_file) if args.source_query_file else None,
            derivation_config=DerivationConfig(
                enabled=args.derive_missing,
                transaction_code_mode=args.derive_transaction_code_mode,
                source_field_mode=args.derive_source_field_mode,
                transaction_code_placeholder=args.transaction_code_placeholder,
                source_field_placeholder=args.source_field_placeholder,
                lineage_max_placeholder_ratio=args.lineage_max_placeholder_ratio,
                lineage_max_low_confidence_ratio=args.lineage_max_low_confidence_ratio,
            ),
            promotion_gate_enabled=args.promotion_gate,
            promotion_policy=PromotionPolicy(
                warn_acceptance_mode=args.warn_acceptance_mode,
                warn_acceptance_max_open=args.warn_acceptance_max_open,
                warn_acceptance_expiry_days=args.warn_acceptance_expiry_days,
                min_template_completeness_ba=args.min_template_completeness_ba,
                min_template_completeness_qa=args.min_template_completeness_qa,
                min_template_completeness_required_columns=args.min_template_completeness_required_columns,
                derivation_default_transaction_code=args.derivation_default_transaction_code,
                derivation_default_source_field=args.derivation_default_source_field,
                derivation_mode_on_enable=args.derivation_mode_on_enable,
            ),
            template_completeness_ba=args.template_completeness_ba,
            template_completeness_qa=args.template_completeness_qa,
            required_columns_completeness=args.required_columns_completeness,
            warn_signoff_ba=args.warn_signoff_ba,
            warn_signoff_qa=args.warn_signoff_qa,
            warn_waiver_age_days=args.warn_waiver_age_days,
        )
    except ValidationError as e:
        print(json.dumps({"status": "FAILED", "errors": [x.as_dict() for x in e.errors]}, indent=2), flush=True)
        return 1

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("status") in {"SUCCESS", "WARN", "PASS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
