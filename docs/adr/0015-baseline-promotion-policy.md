# ADR 0015: Baseline-Promotion Policy and Drift Guard

- Status: Accepted
- Date: 2026-06-11
- Related: `docs/ARCHITECTURE_REVIEW_2026-06-03.md` (recommendation **R-12**),
  issue #41 (R-12),
  `scripts/e2e_lib/promote_baseline.py`,
  `config/e2e/promotion_policy.yml`,
  [ADR 0010](0010-truthsource-backend-abstraction.md),
  `docs/handover/ARCH_REVIEW_R-12_HANDOFF.md`.

## Context

`scripts/e2e_lib/promote_baseline.py` provides the *mechanism* for promoting a
signed-off run's outputs into the next baseline release. Before this ADR the
*policy* was undefined: who may promote, what constitutes drift, and how
`--force` overrides are governed were all left to operator convention.

The 2026-06-03 architecture review (R-12) flagged this gap. AGENTS.md hard rule
§"do not guess on baseline-promotion policy" required a human decision before
implementation.

## Decision

### 1. Approver allowlist (config-driven)

`--approved-by` must match an entry in the `approvers` list declared in
`config/e2e/promotion_policy.yml`. The script validates the value at runtime
and rejects unknown approvers with a clear error. The allowlist is config-driven
(not hardcoded) so adding or removing approvers is a config change, not a code
change.

### 2. Approval workflow

Free-text `--comment` is sufficient for the script. The actual approval
workflow lives in CODEOWNERS review on the resulting MR. The script does not
enforce a GitLab MR reference in the comment.

### 3. Drift guard (Option A — diff-clean)

Before promoting, the script computes a byte-level diff between the candidate
run output and the *current* baseline file (if one exists for the same
`(env, source, file_type)`). If the files differ, promotion is **blocked**
unless `--force` is supplied. This ensures that a promotion that changes the
baseline is always an explicit, acknowledged act.

Rationale for Option A over B (version-bump) or C (no guard):
- Option A catches the case where a mapping version was bumped but the output
  did not actually change (spurious promotion) and the case where output changed
  but the mapping version was not bumped (silent drift).
- Option B would miss output drift when the version string is not updated.
- Option C provides no drift signal at all.

### 4. `--force` requires `--reason`

When `--force` is passed, `--reason` is also required. The reason string is
recorded as a separate field (`force_reason`) in the manifest entry alongside
`approved_by`, `approved_at`, and `comment`. This creates an auditable trail
for every forced promotion.

### 5. Environments are config-driven

The set of valid environments is read from the `envs` block of
`config/e2e/paths.yml` via `PathResolver.known_envs()`. No environment names
are hardcoded in `promote_baseline.py`. Adding a new environment (e.g. `prod`)
is a `paths.yml` config change only.

## Consequences

- `config/e2e/promotion_policy.yml` is a new required config artifact. Its
  schema is documented in `docs/CONFIG_SCHEMA_REGISTRY.md`.
- `promote_baseline.py` gains three new behaviours: approver validation, drift
  check, and `--reason` enforcement. All are gated by the policy file.
- Existing callers that do not pass `--force` are unaffected (drift check only
  fires when a baseline already exists and the candidate differs).
- Callers that currently pass `--force` without `--reason` will receive a new
  error and must add `--reason`.
- Unit tests cover all four new guard paths.
