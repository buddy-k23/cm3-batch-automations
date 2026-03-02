#!/usr/bin/env python3
"""Prototype contract generator for canonical ingest JSON.

Converts canonical template-ingest JSON into:
- generated/mapping.json
- generated/rules.json
- generated/conversion-report.json

This prototype intentionally keeps logic explicit and deterministic.
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from tools.schema_validation import SchemaValidationError, validate_payload
except ModuleNotFoundError:
    from schema_validation import SchemaValidationError, validate_payload


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _to_bool_required(value: str | None) -> bool:
    return (value or "").strip().upper() == "Y"


def _safe_rule_id(value: str) -> str:
    cleaned = []
    for ch in (value or ""):
        if ch.isalnum() or ch in "_-":
            cleaned.append(ch.upper())
        else:
            cleaned.append("_")
    normalized = "".join(cleaned).strip("_")
    return normalized or "RULE_UNSPECIFIED"


def _unique_sorted(values: list[str]) -> list[str]:
    return sorted({v for v in values if isinstance(v, str) and v.strip()})


def _build_mapping(canonical: dict[str, Any], mapping_id: str, version: str) -> dict[str, Any]:
    file_cfg = canonical.get("fileConfig", {})
    input_obj = canonical.get("input", {})
    mapping_rows = canonical.get("mappingRows", [])

    target: dict[str, Any] = {
        "format": file_cfg.get("format"),
        "recordLayout": {},
    }

    if file_cfg.get("delimiter"):
        target["delimiter"] = file_cfg["delimiter"]
    if file_cfg.get("quoteChar"):
        target["quoteChar"] = file_cfg["quoteChar"]
    if file_cfg.get("escapeChar"):
        target["escapeChar"] = file_cfg["escapeChar"]

    header_enabled = file_cfg.get("headerEnabled")
    header_total = file_cfg.get("headerTotalCountField")
    if header_enabled is not None or header_total:
        target["header"] = {}
        if header_enabled is not None:
            target["header"]["enabled"] = bool(header_enabled)
        if header_total:
            target["header"]["totalCountField"] = header_total

    if file_cfg.get("format") == "fixed-width":
        record_layout = {"strictLength": True}
        if file_cfg.get("recordLength") is not None:
            record_layout["recordLength"] = int(file_cfg["recordLength"])
        target["recordLayout"] = record_layout
    else:
        # Keep schema-required object present and stable.
        target["recordLayout"] = {"strictLength": False}

    fields: list[dict[str, Any]] = []
    for row in mapping_rows:
        field: dict[str, Any] = {
            "name": row.get("targetField"),
            "source": row.get("sourceField"),
            "targetType": row.get("dataType"),
            "required": _to_bool_required(row.get("required")),
        }
        if row.get("transactionCode"):
            field["transactionCode"] = row["transactionCode"]
        if row.get("format"):
            field["format"] = row["format"]
        if row.get("length") is not None:
            field["length"] = int(row["length"])
        if row.get("positionStart") is not None:
            field["positionStart"] = int(row["positionStart"])
        if row.get("positionEnd") is not None:
            field["positionEnd"] = int(row["positionEnd"])
        if "defaultValue" in row:
            field["default"] = row.get("defaultValue")
        if row.get("transformLogic"):
            field["transform"] = row["transformLogic"]
        if row.get("sourceLineage"):
            field["lineage"] = row["sourceLineage"]
        fields.append(field)

    # Deterministic order.
    fields.sort(
        key=lambda f: (
            str(f.get("transactionCode", "")),
            str(f.get("name", "")),
            str(f.get("source", "")),
        )
    )

    file_name = input_obj.get("fileName", "").strip() or "template-input"

    return {
        "mappingId": mapping_id,
        "version": version,
        "description": f"Generated from canonical ingest: {file_name}",
        "source": {
            "type": "oracle",
            "query": "SELECT * FROM SOURCE_TABLE",
            "groupKeys": _unique_sorted([r.get("transactionCode", "") for r in mapping_rows]),
        },
        "target": target,
        "fields": fields,
    }


def _build_rules(canonical: dict[str, Any], rule_pack_id: str, version: str, owner: str) -> dict[str, Any]:
    file_cfg = canonical.get("fileConfig", {})
    mapping_rows = canonical.get("mappingRows", [])
    rule_rows = canonical.get("ruleRows", [])

    rules: list[dict[str, Any]] = []
    for row in rule_rows:
        scope = row.get("scope")
        rule: dict[str, Any] = {
            "ruleId": _safe_rule_id(str(row.get("ruleId", ""))),
            "scope": scope,
            "severity": row.get("severity"),
            "priority": int(row.get("priority")),
            "expression": row.get("expression"),
            "messageTemplate": row.get("messageTemplate"),
            "owner": owner,
            "enabled": bool(row.get("enabled", True)),
        }
        if row.get("ruleName"):
            rule["name"] = row["ruleName"]
        if scope == "group":
            rule["groupBy"] = sorted(row.get("groupBy", []))
        rules.append(rule)

    if not rules:
        # Keep rules contract valid for mapping-only workbooks while preserving
        # deterministic no-op validation behavior.
        rules.append(
            {
                "ruleId": "RULEPACK_PLACEHOLDER_INFO",
                "name": "Generated placeholder rule for mapping-only workbook",
                "scope": "file",
                "severity": "INFO",
                "priority": 9999,
                "expression": "1 == 1",
                "messageTemplate": "No explicit rules supplied; generated informational placeholder.",
                "owner": owner,
                "enabled": True,
            }
        )

    # Deterministic order.
    rules.sort(key=lambda r: (int(r.get("priority", 0)), str(r.get("ruleId", ""))))

    return {
        "rulePackId": rule_pack_id,
        "version": version,
        "status": "draft",
        "appliesTo": {
            "fileType": file_cfg.get("format", "any"),
            "transactionCodes": _unique_sorted([r.get("transactionCode", "") for r in mapping_rows]),
        },
        "rules": rules,
    }


def _validate_mapping_contract(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ["mappingId", "version", "source", "target", "fields"]:
        if key not in payload:
            errors.append(f"mapping missing required key: {key}")

    source = payload.get("source", {})
    if source.get("type") != "oracle":
        errors.append("mapping.source.type must be 'oracle'")
    if not source.get("query"):
        errors.append("mapping.source.query is required")

    target = payload.get("target", {})
    fmt = target.get("format")
    if fmt not in {"fixed-width", "delimited"}:
        errors.append("mapping.target.format must be fixed-width|delimited")
    if fmt == "delimited" and not target.get("delimiter"):
        errors.append("mapping.target.delimiter is required for delimited format")

    fields = payload.get("fields", [])
    if not isinstance(fields, list) or len(fields) == 0:
        errors.append("mapping.fields must be a non-empty array")
    else:
        for idx, field in enumerate(fields):
            for key in ["name", "source", "targetType"]:
                if key not in field or field.get(key) in (None, ""):
                    errors.append(f"mapping.fields[{idx}] missing required key: {key}")

    return errors


def _validate_rules_contract(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ["rulePackId", "version", "status", "rules"]:
        if key not in payload:
            errors.append(f"rules missing required key: {key}")

    if payload.get("status") not in {"draft", "candidate", "approved", "deprecated", "retired"}:
        errors.append("rules.status is invalid")

    rules = payload.get("rules", [])
    if not isinstance(rules, list) or len(rules) == 0:
        errors.append("rules.rules must be a non-empty array")
    else:
        seen_ids = set()
        for idx, rule in enumerate(rules):
            for key in ["ruleId", "scope", "severity", "priority", "expression", "messageTemplate", "owner"]:
                if key not in rule:
                    errors.append(f"rules.rules[{idx}] missing required key: {key}")
            rid = rule.get("ruleId")
            if rid in seen_ids:
                errors.append(f"duplicate ruleId generated: {rid}")
            seen_ids.add(rid)
            if rule.get("scope") == "group" and "groupBy" not in rule:
                errors.append(f"rules.rules[{idx}] group scope requires groupBy")

    return errors


def generate(
    input_path: Path,
    out_dir: Path,
    version: str,
    owner: str,
    generated_at: str | None = None,
    input_ref: str | None = None,
    conversion_warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    canonical = _read_json(input_path)
    schema_dir = Path(__file__).resolve().parents[1] / "schemas"

    schema_errors: list[str] = []

    def _record_schema_error(ex: SchemaValidationError) -> None:
        schema_errors.extend([f"{ex.schema_name}: {issue.path} {issue.message}" for issue in ex.issues])

    try:
        validate_payload(canonical, schema_dir / "template-ingest.schema.json", "template-ingest")
    except SchemaValidationError as ex:
        _record_schema_error(ex)

    stem = input_path.stem
    mapping_id = f"{stem}-mapping"
    rule_pack_id = f"{stem}-rules"

    mapping = _build_mapping(canonical, mapping_id=mapping_id, version=version)
    rules = _build_rules(canonical, rule_pack_id=rule_pack_id, version=version, owner=owner)

    mapping_errors = _validate_mapping_contract(mapping)
    rules_errors = _validate_rules_contract(rules)

    try:
        validate_payload(mapping, schema_dir / "mapping.schema.json", "mapping")
    except SchemaValidationError as ex:
        _record_schema_error(ex)
    try:
        validate_payload(rules, schema_dir / "rules.schema.json", "rules")
    except SchemaValidationError as ex:
        _record_schema_error(ex)

    _write_json(out_dir / "mapping.json", mapping)
    _write_json(out_dir / "rules.json", rules)

    run_id = f"contract-gen-{uuid.uuid4()}"
    now = generated_at or datetime.now(timezone.utc).isoformat()

    warnings = conversion_warnings or []
    validated_count = len(mapping.get("fields", [])) + len(rules.get("rules", []))
    pre_schema_error_count = len(mapping_errors) + len(rules_errors) + len(schema_errors)

    report = {
        "runId": run_id,
        "status": "SUCCESS" if pre_schema_error_count == 0 else "FAILED",
        "summary": {
            "validated": validated_count,
            "passed": validated_count if pre_schema_error_count == 0 else 0,
            "failed": pre_schema_error_count,
            "warned": len(warnings),
        },
        "artifacts": {
            "summaryJson": str(out_dir / "conversion-report.json"),
            "detailCsv": str(out_dir / "conversion-errors.csv"),
            "telemetryJson": str(out_dir / "telemetry.json"),
        },
    }

    telemetry = {
        "runId": run_id,
        "timestamp": now,
        "mappingVersion": str(mapping.get("version", version)),
        "rulePackVersion": str(rules.get("version", version)),
        "phases": {
            "extractMs": 0,
            "validateMs": 0,
            "reportMs": 0,
            "totalMs": 0,
        },
        "quality": {
            "validated": report["summary"]["validated"],
            "passed": report["summary"]["passed"],
            "failed": report["summary"]["failed"],
            "warned": len(warnings),
            "byRule": [],
        },
    }

    try:
        validate_payload(report, schema_dir / "report.schema.json", "report")
    except SchemaValidationError as ex:
        _record_schema_error(ex)
    try:
        validate_payload(telemetry, schema_dir / "telemetry.schema.json", "telemetry")
    except SchemaValidationError as ex:
        _record_schema_error(ex)

    total_errors = len(mapping_errors) + len(rules_errors) + len(schema_errors)
    status = "FAILED" if total_errors > 0 else ("WARN" if warnings else "SUCCESS")
    report["status"] = status
    report["summary"]["failed"] = total_errors
    report["summary"]["passed"] = validated_count if status in {"SUCCESS", "WARN"} else 0
    telemetry["quality"]["failed"] = total_errors
    telemetry["quality"]["passed"] = report["summary"]["passed"]
    telemetry["quality"]["warned"] = len(warnings)

    legacy_report = {
        "generatedAt": now,
        "input": input_ref or str(input_path),
        "status": status,
        "summary": {
            "mappingRows": len(canonical.get("mappingRows", [])),
            "ruleRows": len(canonical.get("ruleRows", [])),
            "generatedFields": len(mapping.get("fields", [])),
            "generatedRules": len(rules.get("rules", [])),
            "errors": total_errors,
            "warnings": len(warnings),
        },
        "errors": mapping_errors + rules_errors + schema_errors,
        "warnings": warnings,
        "artifacts": {
            "mapping": "mapping.json",
            "rules": "rules.json",
            "report": "report.json",
            "telemetry": "telemetry.json",
        },
    }

    _write_json(out_dir / "report.json", report)
    _write_json(out_dir / "telemetry.json", telemetry)
    _write_json(out_dir / "conversion-report.json", legacy_report)

    return legacy_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate mapping/rules contracts from canonical ingest JSON")
    parser.add_argument("--input", required=True, help="Path to canonical ingest JSON")
    parser.add_argument("--out-dir", default="generated", help="Output directory for generated artifacts")
    parser.add_argument("--version", default="0.1.0", help="Contract version to stamp")
    parser.add_argument("--owner", default="contract-generator", help="Default owner for generated rules")
    parser.add_argument("--generated-at", help="Override generatedAt timestamp in conversion-report")
    parser.add_argument("--input-ref", help="Stable input reference stored in conversion-report")
    args = parser.parse_args()

    report = generate(
        input_path=Path(args.input),
        out_dir=Path(args.out_dir),
        version=args.version,
        owner=args.owner,
        generated_at=args.generated_at,
        input_ref=args.input_ref,
    )

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("status") in {"SUCCESS", "WARN"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
