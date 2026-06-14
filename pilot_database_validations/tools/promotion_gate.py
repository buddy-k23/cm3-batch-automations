#!/usr/bin/env python3
"""Wave 4 promotion-gate policy evaluator.

Evaluates pre-gate conversion status (SUCCESS/WARN/FAILED) into a terminal decision
(PASS/FAIL) using configurable policy knobs documented in template spec / stage gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


POLICY_VERSION = "w4-c.v1"


@dataclass(frozen=True)
class PromotionPolicy:
    warn_acceptance_mode: str = "review-required"  # block | review-required | auto-accept
    warn_acceptance_max_open: int = 0
    warn_acceptance_expiry_days: int = 7
    min_template_completeness_ba: float = 95.0
    min_template_completeness_qa: float = 95.0
    min_template_completeness_required_columns: float = 100.0
    derivation_default_transaction_code: str = "disabled"
    derivation_default_source_field: str = "disabled"
    derivation_mode_on_enable: str = "placeholder"


@dataclass(frozen=True)
class PromotionInputs:
    pre_gate_status: str
    hard_error_count: int
    open_warn_count: int
    template_completeness_ba: float
    template_completeness_qa: float
    required_columns_completeness: float
    warn_signoff_ba: bool
    warn_signoff_qa: bool
    warn_waiver_age_days: int | None


def evaluate_promotion_gate(policy: PromotionPolicy, inputs: PromotionInputs, enabled: bool) -> dict[str, Any]:
    reasons: list[dict[str, Any]] = []

    def _reason(code: str, severity: str, message: str, blocking: bool, details: dict[str, Any] | None = None) -> None:
        payload = {
            "code": code,
            "severity": severity,
            "message": message,
            "blocking": blocking,
        }
        if details:
            payload["details"] = details
        reasons.append(payload)

    if not enabled:
        decision = "PASS" if inputs.pre_gate_status in {"SUCCESS", "WARN"} else "FAIL"
        _reason(
            code="GATE_DISABLED",
            severity="INFO",
            message="Promotion gate enforcement disabled; preserving legacy status behavior.",
            blocking=False,
            details={"legacyStatus": inputs.pre_gate_status},
        )
        return {
            "policyVersion": POLICY_VERSION,
            "enabled": False,
            "decision": decision,
            "preGateStatus": inputs.pre_gate_status,
            "blocking": decision == "FAIL",
            "reasons": reasons,
        }

    if inputs.pre_gate_status == "FAILED" or inputs.hard_error_count > 0:
        _reason(
            code="HARD_ERRORS_PRESENT",
            severity="ERROR",
            message="Hard errors are present; promotion is blocked.",
            blocking=True,
            details={"hardErrorCount": inputs.hard_error_count, "preGateStatus": inputs.pre_gate_status},
        )

    if inputs.required_columns_completeness < policy.min_template_completeness_required_columns:
        _reason(
            code="REQUIRED_COLUMNS_COMPLETENESS_BELOW_MIN",
            severity="ERROR",
            message="Required-columns completeness is below configured minimum.",
            blocking=True,
            details={
                "observed": inputs.required_columns_completeness,
                "minimum": policy.min_template_completeness_required_columns,
            },
        )

    if inputs.template_completeness_ba < policy.min_template_completeness_ba:
        _reason(
            code="BA_COMPLETENESS_BELOW_MIN",
            severity="ERROR",
            message="BA completeness is below configured minimum.",
            blocking=True,
            details={"observed": inputs.template_completeness_ba, "minimum": policy.min_template_completeness_ba},
        )

    if inputs.template_completeness_qa < policy.min_template_completeness_qa:
        _reason(
            code="QA_COMPLETENESS_BELOW_MIN",
            severity="ERROR",
            message="QA completeness is below configured minimum.",
            blocking=True,
            details={"observed": inputs.template_completeness_qa, "minimum": policy.min_template_completeness_qa},
        )

    if inputs.open_warn_count > policy.warn_acceptance_max_open:
        _reason(
            code="WARN_COUNT_ABOVE_THRESHOLD",
            severity="ERROR",
            message="Open WARN count exceeds configured threshold.",
            blocking=True,
            details={"openWarnCount": inputs.open_warn_count, "maxOpen": policy.warn_acceptance_max_open},
        )

    if inputs.open_warn_count > 0:
        if policy.warn_acceptance_mode == "block":
            _reason(
                code="WARN_MODE_BLOCK",
                severity="ERROR",
                message="WARN acceptance mode is block; unresolved WARNs are not allowed.",
                blocking=True,
            )
        elif policy.warn_acceptance_mode == "review-required":
            if not inputs.warn_signoff_ba or not inputs.warn_signoff_qa:
                _reason(
                    code="WARN_SIGNOFF_REQUIRED",
                    severity="ERROR",
                    message="WARN acceptance requires both BA and QA signoff.",
                    blocking=True,
                    details={"ba": inputs.warn_signoff_ba, "qa": inputs.warn_signoff_qa},
                )
            if inputs.warn_waiver_age_days is None:
                _reason(
                    code="WARN_WAIVER_AGE_MISSING",
                    severity="ERROR",
                    message="WARN waiver age is required in review-required mode.",
                    blocking=True,
                )
            elif inputs.warn_waiver_age_days > policy.warn_acceptance_expiry_days:
                _reason(
                    code="WARN_WAIVER_EXPIRED",
                    severity="ERROR",
                    message="WARN waiver has exceeded configured expiry window.",
                    blocking=True,
                    details={
                        "waiverAgeDays": inputs.warn_waiver_age_days,
                        "expiryDays": policy.warn_acceptance_expiry_days,
                    },
                )
        else:
            _reason(
                code="WARN_AUTO_ACCEPTED",
                severity="INFO",
                message="WARNs auto-accepted by policy.",
                blocking=False,
                details={"openWarnCount": inputs.open_warn_count},
            )

    blocking = any(r["blocking"] for r in reasons)
    if not blocking:
        _reason(
            code="PROMOTION_POLICY_SATISFIED",
            severity="INFO",
            message="All promotion gate conditions satisfied.",
            blocking=False,
        )

    return {
        "policyVersion": POLICY_VERSION,
        "enabled": True,
        "decision": "FAIL" if blocking else "PASS",
        "preGateStatus": inputs.pre_gate_status,
        "blocking": blocking,
        "reasons": reasons,
    }


def build_promotion_evidence(
    run_id: str,
    policy: PromotionPolicy,
    inputs: PromotionInputs,
    enabled: bool,
) -> dict[str, Any]:
    evaluation = evaluate_promotion_gate(policy=policy, inputs=inputs, enabled=enabled)
    return {
        "runId": run_id,
        "gate": "promotion",
        "evaluation": evaluation,
        "policy": {
            "warnAcceptanceMode": policy.warn_acceptance_mode,
            "warnAcceptanceMaxOpen": policy.warn_acceptance_max_open,
            "warnAcceptanceExpiryDays": policy.warn_acceptance_expiry_days,
            "minTemplateCompletenessBa": policy.min_template_completeness_ba,
            "minTemplateCompletenessQa": policy.min_template_completeness_qa,
            "minTemplateCompletenessRequiredColumns": policy.min_template_completeness_required_columns,
            "derivationDefaultTransactionCode": policy.derivation_default_transaction_code,
            "derivationDefaultSourceField": policy.derivation_default_source_field,
            "derivationModeOnEnable": policy.derivation_mode_on_enable,
        },
        "observed": {
            "preGateStatus": inputs.pre_gate_status,
            "hardErrorCount": inputs.hard_error_count,
            "openWarnCount": inputs.open_warn_count,
            "templateCompletenessBa": inputs.template_completeness_ba,
            "templateCompletenessQa": inputs.template_completeness_qa,
            "requiredColumnsCompleteness": inputs.required_columns_completeness,
            "warnSignoffBa": inputs.warn_signoff_ba,
            "warnSignoffQa": inputs.warn_signoff_qa,
            "warnWaiverAgeDays": inputs.warn_waiver_age_days,
        },
    }
