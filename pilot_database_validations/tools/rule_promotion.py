#!/usr/bin/env python3
"""Wave 5 governance flow for promoting extracted rules into managed rule packs.

Opt-in utility:
- Loads extracted candidates from rules-extraction artifacts.
- Applies explicit review decisions.
- Detects conflicts against existing rule packs.
- Emits traceable promotion report and optionally writes updated rule pack.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

PROMOTION_VERSION = "w5-governance.v1"
REVIEW_STATES = {"extracted", "in_review", "approved", "rejected", "deferred", "unresolved"}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _extract_payload(extraction_artifact: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if "rulesExtracted" in extraction_artifact:
        return extraction_artifact.get("rulesExtracted", []), extraction_artifact.get("unresolved", [])

    conversion = extraction_artifact.get("conversion", {})
    rx = conversion.get("rulesExtraction", {})
    return rx.get("extractedRuleRows", []), rx.get("unresolved", [])


def _build_existing_rule_map(rule_pack: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r.get("ruleId", ""): r for r in rule_pack.get("rules", []) if r.get("ruleId")}


def _normalize_decisions(decisions_payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not decisions_payload:
        return {}

    if isinstance(decisions_payload.get("decisions"), list):
        out: dict[str, dict[str, Any]] = {}
        for item in decisions_payload["decisions"]:
            rid = item.get("ruleId")
            if not rid:
                continue
            out[rid] = item
        return out

    raw = decisions_payload.get("decisions", {})
    out = {}
    for rid, state in raw.items():
        if isinstance(state, dict):
            out[rid] = state
        else:
            out[rid] = {"reviewState": state}
    return out


def plan_promotion(
    extraction_artifact: dict[str, Any],
    rule_pack: dict[str, Any],
    decisions_payload: dict[str, Any] | None = None,
    require_unresolved_resolved: bool = True,
) -> dict[str, Any]:
    extracted_rules, unresolved = _extract_payload(extraction_artifact)
    existing_by_id = _build_existing_rule_map(rule_pack)
    decisions = _normalize_decisions(decisions_payload)

    candidate_reviews: list[dict[str, Any]] = []
    promoted_rules: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    for rule in extracted_rules:
        rule_id = rule.get("ruleId", "")
        decision = decisions.get(rule_id, {})
        review_state = decision.get("reviewState", "in_review")
        if review_state not in REVIEW_STATES:
            review_state = "in_review"

        existing = existing_by_id.get(rule_id)
        conflict_code = None
        conflict_details = None
        if existing:
            if (
                existing.get("expression") == rule.get("expression")
                and existing.get("scope") == rule.get("scope")
                and existing.get("severity") == rule.get("severity")
            ):
                conflict_code = "DUPLICATE_EXISTING_RULE"
                conflict_details = {
                    "ruleId": rule_id,
                    "message": "Rule already exists with matching behavior; candidate skipped.",
                }
            else:
                conflict_code = "RULE_ID_CONFLICT"
                conflict_details = {
                    "ruleId": rule_id,
                    "message": "Rule ID already exists with different behavior; manual reconciliation required.",
                    "existing": {
                        "scope": existing.get("scope"),
                        "severity": existing.get("severity"),
                        "expression": existing.get("expression"),
                    },
                    "candidate": {
                        "scope": rule.get("scope"),
                        "severity": rule.get("severity"),
                        "expression": rule.get("expression"),
                    },
                }
            conflicts.append(conflict_details)

        traceability = {
            "promotionVersion": PROMOTION_VERSION,
            "reviewState": review_state,
            "reviewer": decision.get("reviewer"),
            "reviewedAt": decision.get("reviewedAt"),
            "notes": decision.get("notes"),
            "sourceLocation": rule.get("sourceLocation"),
            "sourceArtifact": extraction_artifact.get("input") or extraction_artifact.get("generatedAt") or "rules-extraction",
        }

        candidate_reviews.append(
            {
                "ruleId": rule_id,
                "reviewState": review_state,
                "conflictCode": conflict_code,
                "traceability": traceability,
            }
        )

        if review_state == "approved" and not conflict_code:
            promoted_rule = dict(rule)
            promoted_rule["traceability"] = traceability
            promoted_rules.append(promoted_rule)

    unresolved_queue = []
    for item in unresolved:
        unresolved_queue.append(
            {
                "reviewState": "unresolved",
                "unresolvedMarker": item.get("unresolvedMarker", "MANUAL_RULE_REQUIRED"),
                "transactionCode": item.get("transactionCode"),
                "targetField": item.get("targetField"),
                "sourceLocation": item.get("sourceLocation"),
                "normalizedTransformLogic": item.get("normalizedTransformLogic"),
                "kind": item.get("kind"),
                "confidence": item.get("confidence"),
            }
        )

    unresolved_block = require_unresolved_resolved and len(unresolved_queue) > 0
    review_block = any(c["reviewState"] == "in_review" for c in candidate_reviews)
    conflict_block = any(c.get("conflictCode") == "RULE_ID_CONFLICT" for c in candidate_reviews)

    decision = "BLOCKED" if unresolved_block or review_block or conflict_block else "READY"

    updated_pack = dict(rule_pack)
    if decision == "READY" and promoted_rules:
        combined = [*rule_pack.get("rules", []), *promoted_rules]
        deduped = {r.get("ruleId"): r for r in combined if r.get("ruleId")}
        updated_pack["rules"] = [deduped[k] for k in sorted(deduped.keys())]
        updated_pack["status"] = "approved"
        governance = updated_pack.get("governance", {})
        governance["promotion"] = {
            "version": PROMOTION_VERSION,
            "promotedRuleCount": len(promoted_rules),
            "candidateCount": len(extracted_rules),
            "unresolvedCount": len(unresolved_queue),
        }
        updated_pack["governance"] = governance

    return {
        "promotionVersion": PROMOTION_VERSION,
        "decision": decision,
        "summary": {
            "candidateCount": len(extracted_rules),
            "approvedCount": len([c for c in candidate_reviews if c["reviewState"] == "approved"]),
            "promotedCount": len(promoted_rules),
            "conflictCount": len(conflicts),
            "unresolvedCount": len(unresolved_queue),
            "inReviewCount": len([c for c in candidate_reviews if c["reviewState"] == "in_review"]),
        },
        "blocks": {
            "unresolvedPresent": unresolved_block,
            "candidatesInReview": review_block,
            "ruleIdConflicts": conflict_block,
        },
        "candidateReviews": candidate_reviews,
        "unresolvedQueue": unresolved_queue,
        "conflicts": conflicts,
        "updatedRulePack": updated_pack,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote extracted rule candidates into governed rule packs")
    parser.add_argument("--extraction", required=True, help="rules extraction artifact JSON path")
    parser.add_argument("--rule-pack", required=True, help="managed rule pack JSON path")
    parser.add_argument("--output-report", required=True, help="promotion report output JSON")
    parser.add_argument("--output-pack", help="optional output path for updated rule pack")
    parser.add_argument("--decisions", help="optional review decisions JSON path")
    parser.add_argument(
        "--require-unresolved-resolution",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="block promotion when unresolved extraction items exist",
    )
    args = parser.parse_args()

    extraction = _read_json(Path(args.extraction))
    rule_pack = _read_json(Path(args.rule_pack))
    decisions = _read_json(Path(args.decisions)) if args.decisions else None

    report = plan_promotion(
        extraction_artifact=extraction,
        rule_pack=rule_pack,
        decisions_payload=decisions,
        require_unresolved_resolved=args.require_unresolved_resolution,
    )
    _write_json(Path(args.output_report), report)

    if args.output_pack:
        _write_json(Path(args.output_pack), report["updatedRulePack"])

    print(json.dumps({"decision": report["decision"], "summary": report["summary"]}, indent=2))
    return 0 if report["decision"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
