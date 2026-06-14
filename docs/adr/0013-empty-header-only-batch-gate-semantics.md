# ADR 0013: Empty / Header-Only Batch Gate Semantics

- Status: Accepted — **Option A** chosen by the user (2026-06-05) and
  implemented in the R-05 (#32) slice.
- Date: 2026-06-05
- Supersedes: none
- Related: `docs/ARCHITECTURE_REVIEW_2026-06-03.md` (recommendation R-05),
  `docs/handover/L2B_SESSION_11_HANDOVER.md` §6–7,
  `src/validators/cross_type_validator.py` (`_check_header_trailer_count`),
  `scripts/e2e_lib/db_truth_comparator.py` (`_evaluate_assertion`,
  `batch_header_count`), `config/mappings/SHAW_TRANERT.yaml` (the
  `header_trailer_count` cross-type rule), `config/e2e/sources/SHAW/reconciliation/tranert.yml`.
  Implementation is the R-05 (#32) slice, gated on this decision.

## Context

The 2026-06-03 SHAW TRANERT batch was **header-only**: one Batch Header line,
zero detail rows. It surfaced a disagreement between two gates that look at the
same file with different truth sources (L2B_SESSION_11 §6):

- **L1 / rules (multi-record report)** validates the file against itself + the
  spec. On the header-only batch the rules report **FAILed** on the item-count
  axis. It **cannot know** whether "0 details" is correct — only that the file's
  internal numbers must be self-consistent and well-formed.
- **L2b SQL-truth reconcile** validates the file against the live DB-derived
  expected rowset. It **PASSed** with `expected_rows = 0` for every detail type,
  confirming the DB genuinely has **no charge-off accounts** for that batch date.
  L2b is the only gate that would catch a *truncated* file (it raises
  `missing_expected`).

Both verdicts are individually correct for their truth source. The product
question is what the **L1 item-count axis** should do on a legitimately empty
batch, so a normal header-only day does not raise a spurious blocking failure
while a genuinely malformed/truncated file still fails.

### Exactly which L1 check is in question

There are two distinct item-count mechanisms; this ADR is about the **L1** one:

1. **L1 (in scope).** `header_trailer_count` cross-type rule on the
   `SHAW_TRANERT.yaml` umbrella → `cross_type_validator._check_header_trailer_count`:
   compares the header's `ITM-CNT-BRT` (`declared_int`) to the number of detail
   rows (`actual_count`); a mismatch is one `error` violation. Plus the
   per-field Batch-Header rules on `ITM-CNT-BRT` (R058 `not_empty`, R059
   `numeric`, R060 `exact_length=9`).
2. **L2b (not in scope, already correct).** The `batch_header_count` assertion
   `header.ITM-CNT-BRT == sum(detail_row_counts)` in
   `db_truth_comparator._evaluate_assertion`, which already PASSes on the empty
   batch because the DB confirms 0 expected rows.

### What the current L1 code actually does on a clean empty batch

`ITM-CNT-BRT` is `9(9)` numeric, so a correct header-only file carries
`"000000000"`. With that value:

- R058/R059/R060 **pass** (`"000000000"` is non-empty, numeric, 9 chars).
- `_check_header_trailer_count` evaluates `declared_int (0) != actual_count (0)`
  → `False` → **no violation**.

So a *correctly-formed* zero-filled header-only batch already passes L1 today.
The observed FAIL therefore came from one of: (a) the header carried a blank /
non-zero `ITM-CNT-BRT` for the empty day (operational format question), or
(b) some other item-count assertion in the rules surface. **This is precisely
why the decision cannot be guessed**: it depends on how SHAW represents "zero
items" in a header-only file, and on whether header-only batches are a *normal,
expected* occurrence or an *exception worth flagging*. AGENTS.md lists gate
blocking semantics as do-not-guess.

## Decision drivers

- **No false blocking failures** on a normal/expected empty batch.
- **No masking of real defects:** a truncated file (header says N>0, file has 0
  details) and a malformed header must still fail.
- **Keep L1 and L2b complementary** (different truth sources), not redundant.
- **AGENTS.md "do not guess"** on gate blocking semantics → user decides.
- **Small, surgical change** once decided (a conditional in the cross-type
  count check and/or the header-field rule semantics); no `src/` redesign.

## Options considered

### Option A — Treat "header count == 0 AND 0 details" as valid (recommended)

Make the L1 item-count axis pass **only** the specific empty-batch case:
`ITM-CNT-BRT == 0` *and* `actual detail count == 0`. Every other combination is
unchanged, so a truncated/over-counted file still fails.

- Concretely (subject to the implementation slice): `_check_header_trailer_count`
  already returns no violation for `0 == 0`; the gap, if any, is the **per-field
  `not_empty` rule (R058)** rejecting a *blank* (space-filled) `ITM-CNT-BRT` on
  the empty day. Option A means: a header-only batch must still carry a
  zero-filled `"000000000"` count (so R058/R059/R060 pass and `0 == 0` holds);
  if SHAW emits *blank* for zero, add a narrow "blank ⇒ treat as 0 only when
  detail count is 0" allowance in the count check, leaving R058 to still catch a
  blank count when details exist.
- **Pros**
  - Removes the spurious blocking failure on a normal empty day.
  - Still catches truncation (`N>0` header, `0` details) and over/under-count.
  - Minimal, well-scoped; matches L2b's "0 expected is fine" verdict, restoring
    L1/L2b agreement on the empty case without making them redundant.
- **Cons**
  - Slightly more conditional logic in the count check.
  - Requires confirming SHAW's zero representation (zero-filled vs blank) — a
    one-line operational confirmation, not a guess we can make.

### Option B — Header-only batches always fail L1 (status quo intent)

Treat any header-only batch as an exception requiring human acknowledgement;
keep the L1 item-count failure, rely on the operator to confirm via L2b.

- **Pros**: zero code change; forces a human to look at every empty day.
- **Cons**: noisy — every legitimately empty batch raises a blocking L1 failure
  the operator must dismiss; erodes signal; contradicts L2b's PASS and the
  "normal occurrence" framing in the issue. Rejected unless empty batches are
  genuinely rare exceptions.

### Option C — Make the L1 item-count axis non-blocking (downgrade severity)

Demote the `header_trailer_count` rule (and/or the empty case) to `warning`.

- **Pros**: empty batches never block; the signal is still surfaced.
- **Cons**: also downgrades the **truncation** signal (a real defect), which we
  explicitly want to keep blocking. Rejected: too blunt — it weakens a genuine
  data-integrity check to fix a narrow empty-batch edge.

### Option D — Gate the whole batch on emptiness upstream

Detect a header-only batch before L1 and skip the item-count axis entirely.

- **Pros**: clean separation.
- **Cons**: adds an orchestrator-level special case and a new "is empty?"
  contract; broader than the issue's "tiny conditional once decided" framing;
  risks skipping checks that *should* run on an empty file. Rejected for this
  slice.

## Recommendation

**Option A — treat "`ITM-CNT-BRT == 0` and 0 detail rows" as valid**, with the
truncation and malformed-header cases still failing. It is the smallest change
that removes the false positive while preserving every real defect signal and
restoring L1/L2b agreement on the empty case.

**Two questions for the user before implementation (do-not-guess):**

1. **Are header-only / zero-detail batches a normal, expected occurrence** for
   SHAW TRANERT (and any other multi-record source), i.e. should the harness
   pass them silently? (If they are rare exceptions that warrant a human look,
   Option B/C is the intent instead.)
2. **How does the source represent "zero items" in the header** — a zero-filled
   `"000000000"` `ITM-CNT-BRT`, or a blank/space-filled field? This determines
   whether the change lives only in the cross-type count check or must also
   relax the per-field `not_empty` rule (R058) for the empty case.

## Consequences

### If Option A is accepted
- R-05 (#32) implements the conditional in
  `cross_type_validator._check_header_trailer_count` (and, only if the source
  emits blank-for-zero, a narrow allowance interacting with R058) so that
  `count == 0 ∧ details == 0` is clean while `count ≠ details` still fails.
- Add a unit test for the header-only batch (clean) **and** a truncation test
  (`ITM-CNT-BRT > 0`, 0 details → still fails) to pin that real defects survive.
- No change to non-empty-batch behaviour (issue acceptance criterion).
- Document the rule's empty-batch semantics in the umbrella YAML comment and the
  testing guide.

### If Option B/C/D is accepted
- R-05 is re-scoped accordingly (no change / severity demotion / orchestrator
  emptiness gate) with the matching tests and docs.

### Common
- This ADR records the decision only; **no code/config changes land under the
  decision step.** The implementation is the gated R-05 slice.

## Decision outcome

The user selected **Option A** on 2026-06-05. Implemented in the R-05 (#32)
slice as a config-driven, opt-in allowance:

- `src/config/multi_record_config.py`: added an opt-in
  `allow_empty_batch: bool = False` field to `CrossTypeRule` (default `False`,
  so every existing rule and source is unaffected).
- `src/validators/cross_type_validator.py::_check_header_trailer_count`: when
  the rule sets `allow_empty_batch` **and** the declared count is 0 **and** the
  counted-rows total is 0, the check passes (no violation). Every other case is
  unchanged — in particular a **truncated** file (`declared > 0`, 0 rows) and
  any non-zero mismatch still fail.
- `config/mappings/SHAW_TRANERT.yaml`: the `header_trailer_count` rule opts in
  with `allow_empty_batch: true`.
- Tests: added clean-empty, truncation-still-fails, and non-empty-unchanged
  cases under `tests/unit/test_multi_record_validator.py::TestCrossTypeValidator`.

### Scope note on the two do-not-guess questions

The implemented change lives entirely in the **L1 item-count axis** named by
R-05, and is correct **regardless** of how the source represents "zero items":

- If the header is zero-filled (`ITM-CNT-BRT == "000000000"`), the opt-in passes
  the `0 == 0` case (and the per-field rules R058/R059/R060 already pass).
- If the header is left **blank** for zero, `_check_header_trailer_count` already
  skips it (the `int(...)` parse raises and `continue`s), so the count axis does
  not fail; the separate per-field `not_empty` rule **R058** would still flag a
  blank field. That is a distinct field-format rule, **not** the item-count
  assertion R-05 addresses, so it is intentionally left untouched. If SHAW emits
  blank-for-zero and the team wants R058 relaxed on the empty batch too, that is
  a small follow-up tracked separately (it was not part of the R-05 item-count
  decision).
