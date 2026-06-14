# Stage Gates Checklist

## Feedback-Loop Policy Knobs (Wave 5.1 Pilot Locked)
These knobs are locked for pilot-readiness decisions. Any relaxation requires explicit BA+QA+release-owner signoff.

- `warn_acceptance_mode`: `review-required`
  - `block`: any WARN blocks gate progression.
  - `review-required`: WARNs may pass only with BA + QA approval and documented rationale.
  - `auto-accept`: WARNs do not block unless escalated by BA/QA.
- `warn_acceptance_max_open`: `20`
  - Maximum unresolved WARN count allowed at gate signoff during pilot.
- `warn_acceptance_expiry_days`: `7`
  - WARN waivers must be revisited within this window.

- `min_template_completeness_ba`: `95%`
  - Minimum BA-owned template completeness required for gate acceptance.
- `min_template_completeness_qa`: `95%`
  - Minimum QA-owned template completeness required for gate acceptance.
- `min_template_completeness_required_columns`: `100%`
  - All required columns and enums must be present/valid even when overall completeness threshold is met.

- `derivation_default_transaction_code`: `disabled`
- `derivation_default_source_field`: `disabled`
  - Defaults preserve strict behavior; derivation must be explicitly enabled per run/pipeline policy.
- `derivation_mode_on_enable`: `placeholder`
  - If derivation is enabled but no explicit mode is provided, use deterministic `placeholder` derivation.

## Gate A: PLANNING -> CONTRACT_FREEZE
- [ ] Master plan approved
- [ ] Dependency map approved
- [ ] Risk register initialized

## Gate B: CONTRACT_FREEZE -> TEMPLATE_INGEST_FREEZE
- [ ] mapping/rules/report/telemetry schemas reviewed
- [ ] architecture and ADR placeholders reviewed
- [ ] compatibility policy defined

## Gate C: TEMPLATE_INGEST_FREEZE -> BUILD
- [ ] template spec approved
- [ ] template-ingest schema approved
- [ ] converter design approved
- [ ] sample templates validated
- [ ] BA/QA minimum template completeness meets policy knobs (`min_template_completeness_ba`, `min_template_completeness_qa`)
- [ ] required-column completeness is 100% (`min_template_completeness_required_columns`)
- [ ] derivation defaults documented for this release (`derivation_default_transaction_code`, `derivation_default_source_field`, `derivation_mode_on_enable`)

## Gate D: BUILD -> INTEGRATE
- [ ] engine unit tests pass
- [ ] adapter tests pass
- [ ] converter generates valid contracts

## Gate E: INTEGRATE -> HARDEN
- [ ] end-to-end golden tests pass
- [ ] summary/detailed reports generated and valid
- [ ] telemetry schema conformance pass

## Gate F: HARDEN -> READY_FOR_RELEASE
- [ ] adversarial tests complete
- [ ] deterministic output checks pass
- [ ] performance baseline within threshold
- [ ] PII masking checks pass
- [ ] WARN acceptance decision recorded per policy (`warn_acceptance_mode`, `warn_acceptance_max_open`, `warn_acceptance_expiry_days`)
- [ ] unresolved WARN count within allowed threshold for release-readiness

## Gate G: READY_FOR_RELEASE -> RELEASED
- [ ] release checklist complete
- [ ] rollback plan documented
- [ ] user final signoff
- [ ] release-time WARN posture confirmed (`warn_acceptance_mode` not looser than Gate F decision)
- [ ] open WARN waivers carry owner + expiry (`warn_acceptance_expiry_days`)
