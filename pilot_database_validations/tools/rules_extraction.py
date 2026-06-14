#!/usr/bin/env python3
"""Rule extraction and transform-logic normalization helpers (Wave 4 / W4-A)."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


def _slug(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", (value or "").upper()).strip("_") or "UNKNOWN"


def _stable_rule_id(transaction_code: str, target_field: str, kind: str) -> str:
    base = f"{transaction_code}|{target_field}|{kind}".encode("utf-8")
    suffix = hashlib.sha1(base).hexdigest()[:8].upper()
    return f"W4A_{_slug(transaction_code)}_{_slug(target_field)}_{_slug(kind)}_{suffix}"


def normalize_transform_logic(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _extract_default(transform: str) -> str | None:
    m = re.search(r"(?:default|hardcode)\s*(?:to)?\s*['\"]([^'\"]+)['\"]", transform, re.IGNORECASE)
    if m:
        return m.group(1)
    m2 = re.search(r"default\((?:'|\")([^'\"]+)(?:'|\")\)", transform, re.IGNORECASE)
    if m2:
        return m2.group(1)
    return None


def normalize_transform_logic_to_rule(row: dict[str, Any]) -> dict[str, Any]:
    transform = normalize_transform_logic(str(row.get("transformLogic") or ""))
    target = str(row.get("targetField") or "").strip()
    txn = str(row.get("transactionCode") or "UNKNOWN").strip() or "UNKNOWN"

    source_loc = row.get("sourceLocation") or {}
    meta = {
        "transactionCode": txn,
        "targetField": target,
        "sourceLocation": {
            "sheet": source_loc.get("sheet", "csv"),
            "row": source_loc.get("row", 0),
        },
        "normalizedTransformLogic": transform,
    }

    if not transform:
        return {"status": "skipped", "reason": "NO_TRANSFORM_LOGIC", "meta": meta}

    low = transform.lower()
    default_value = _extract_default(transform)
    if default_value is not None:
        return {
            "status": "resolved",
            "rule": {
                "ruleId": _stable_rule_id(txn, target, "default"),
                "ruleName": f"Extracted default for {target}",
                "scope": "field",
                "severity": "INFO",
                "priority": 900,
                "expression": f"{target} == '{default_value}'",
                "messageTemplate": f"{target} should default to {default_value}",
                "enabled": True,
                "sourceLocation": meta["sourceLocation"],
            },
            "meta": {**meta, "kind": "default", "defaultValue": default_value, "confidence": "high"},
        }

    if "leave blank" in low or "nullable" in low:
        return {
            "status": "resolved",
            "rule": {
                "ruleId": _stable_rule_id(txn, target, "nullable"),
                "ruleName": f"Extracted nullable rule for {target}",
                "scope": "field",
                "severity": "INFO",
                "priority": 905,
                "expression": f"{target} == '' OR {target} IS NULL",
                "messageTemplate": f"{target} may be blank/null",
                "enabled": True,
                "sourceLocation": meta["sourceLocation"],
            },
            "meta": {**meta, "kind": "nullable", "confidence": "medium"},
        }

    if low.startswith("if ") or " then " in low:
        return {
            "status": "unresolved",
            "warning": {
                "warningCode": "RULE_EXTRACTION_UNRESOLVED_IF_THEN",
                "column": "transform_logic",
                "row": meta["sourceLocation"]["row"],
                "sheet": meta["sourceLocation"]["sheet"],
                "message": f"Could not reliably normalize complex IF/THEN logic for {target}; manual rule authoring required",
            },
            "meta": {**meta, "kind": "conditional", "confidence": "low", "unresolvedMarker": "MANUAL_RULE_REQUIRED"},
        }

    if "pass as is" in low or "transform as is" in low:
        return {
            "status": "resolved",
            "rule": {
                "ruleId": _stable_rule_id(txn, target, "passthrough"),
                "ruleName": f"Extracted passthrough rule for {target}",
                "scope": "field",
                "severity": "INFO",
                "priority": 910,
                "expression": f"{target} == SOURCE({row.get('sourceField') or target})",
                "messageTemplate": f"{target} should pass through from source",
                "enabled": True,
                "sourceLocation": meta["sourceLocation"],
            },
            "meta": {**meta, "kind": "passthrough", "confidence": "medium"},
        }

    return {
        "status": "unresolved",
        "warning": {
            "warningCode": "RULE_EXTRACTION_UNRECOGNIZED_TRANSFORM",
            "column": "transform_logic",
            "row": meta["sourceLocation"]["row"],
            "sheet": meta["sourceLocation"]["sheet"],
            "message": f"No deterministic normalization pattern matched for {target}; manual rule authoring required",
        },
        "meta": {**meta, "kind": "unknown", "confidence": "low", "unresolvedMarker": "MANUAL_RULE_REQUIRED"},
    }


def extract_rules_from_mapping_rows(mapping_rows: list[dict[str, Any]]) -> dict[str, Any]:
    extracted: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    seen_ids: set[str] = set()

    for row in mapping_rows:
        result = normalize_transform_logic_to_rule(row)
        if result["status"] == "resolved":
            rule = result["rule"]
            if rule["ruleId"] in seen_ids:
                warnings.append(
                    {
                        "warningCode": "RULE_EXTRACTION_DUPLICATE_RULE_ID",
                        "row": rule.get("sourceLocation", {}).get("row", 0),
                        "column": "transform_logic",
                        "message": f"Duplicate extracted ruleId {rule['ruleId']} skipped",
                    }
                )
                continue
            seen_ids.add(rule["ruleId"])
            extracted.append(rule)
        elif result["status"] == "unresolved":
            unresolved.append(result["meta"])
            warnings.append(result["warning"])

    return {
        "rules": sorted(extracted, key=lambda r: (r["ruleId"], r.get("priority", 0))),
        "unresolved": unresolved,
        "warnings": warnings,
        "summary": {
            "resolvedCount": len(extracted),
            "unresolvedCount": len(unresolved),
            "warningCount": len(warnings),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract normalized rule rows from mapping transform logic")
    parser.add_argument("--input", required=True, help="Canonical ingest JSON path")
    parser.add_argument("--output", required=True, help="Output JSON path for extraction artifact")
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    extracted = extract_rules_from_mapping_rows(payload.get("mappingRows", []))

    out = {
        "input": args.input,
        "generatedAt": "deterministic-v1",
        "rulesExtracted": extracted["rules"],
        "unresolved": extracted["unresolved"],
        "warnings": extracted["warnings"],
        "summary": extracted["summary"],
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
