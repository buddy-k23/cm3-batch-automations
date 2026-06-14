# ADR 0014: Record-Reader Field-Strategy Seam (fixed-width default)

- Status: Accepted
- Date: 2026-06-08
- Accepted: 2026-06-11 (landed on trunk feature/valdo-engine-v3, commits c3175ea, fe9f898)
- Supersedes: none
- Related: [ADR 0003](0003-fixed-width-v2-normalization.md),
  [ADR 0005](0005-multi-record-pipeline-dispatch.md),
  [ADR 0008](0008-extract-multi-record-reader-primitive.md),
  `docs/ARCHITECTURE_REVIEW_2026-06-03.md` (recommendation **R-06**, probe P4),
  issue #36 (R-06a), follow-up issue #37 (R-06b),
  `src/validators/multi_record_reader.py`,
  `scripts/e2e_lib/multi_record_file_parser.py`.

## Context

The 2026-06-03 architecture review's probe **P4** ("delimited, non-fixed-width
multi-record output") rated the harness **Partial**: the multi-record reader
primitive (`src/validators/multi_record_reader.py`, extracted in #21 / ADR
0008) and its field-slicing consumer
(`scripts/e2e_lib/multi_record_file_parser.py`) are **fixed-width / position
oriented**. Both locate the per-line *discriminator* — the substring that
decides which record type a line is — with a 1-indexed `position`/`length`
slice (`_extract_discriminator`). A delimited (CSV / pipe) multi-record output,
where the discriminator is the *N*th delimited field rather than a fixed column
span, cannot be read today without changing the reader.

The review's remediation (**R-06**) is to "generalise the reader to a delimited
strategy." That is deliberately split into two slices so each lands as a small,
reviewable change:

- **R-06a (this ADR, #36):** introduce the *seam* only — isolate the
  line-to-discriminator extraction behind a strategy interface, with the
  existing fixed-width logic as the default. **Pure refactor, no new format,
  no behaviour change.**
- **R-06b (#37, depends on #36):** add the actual delimited strategy on top of
  the seam.

This ADR covers R-06a.

### Why a seam, and why now

The dispatch logic in `read_multi_record_file` (positional `first`/`last`
priority, then value-based `match`, with insertion-order tie-break; blank-line
skipping; BOM stripping; O(1) single-row look-ahead) is **format-agnostic**.
The *only* fixed-width-specific concern in the module is how the discriminator
substring is located. Isolating that one concern behind a strategy lets the
same, well-tested dispatch machinery serve a delimited file in R-06b without
duplicating any of it — exactly the anti-duplication argument ADR 0008 made
when it extracted the primitive in the first place.

### `src/` change justification (AGENTS.md hard rule #1)

This is a **core `src/` change via the normal ADR flow**, not harness work.
The architecture review and the story-runner backlog both classify R-06a /
R-06b as core changes (like R-01x), distinct from the harness-motivated
carve-out in hard rule #1. The seam is additive and behaviour-preserving; no
existing consumer changes.

## Decision

Introduce a `RecordFieldStrategy` protocol and a default
`FixedWidthFieldStrategy` implementation in
`src/validators/multi_record_reader.py`. Thread an **optional**
`field_strategy` parameter through `read_multi_record_file`, defaulting to a
shared module-level `FixedWidthFieldStrategy` instance. The reader's dispatch
code calls `strategy.extract(line, disc)` instead of the free
`_extract_discriminator` function.

### Public API (additive)

```python
# src/validators/multi_record_reader.py

class RecordFieldStrategy(Protocol):
    def extract(self, line: str, disc: DiscriminatorConfig) -> str: ...


class FixedWidthFieldStrategy:
    """1-indexed position/length slice — the historical behaviour, verbatim."""
    def extract(self, line: str, disc: DiscriminatorConfig) -> str: ...


def read_multi_record_file(
    file_path: Union[str, Path],
    config: MultiRecordConfig,
    field_strategy: Optional[RecordFieldStrategy] = None,  # NEW, defaults to fixed-width
) -> Iterator[Union[ParsedRow, UnknownRecordTypeRow]]:
    ...
```

### Design choices

**Protocol, not ABC.** The seam is a single-method extraction point. A
`typing.Protocol` matches the house style of the existing real strategy seam in
the codebase (`SecretProvider` protocol, noted by the review as the model to
follow) and lets a future delimited strategy — or a test double — satisfy it
structurally without importing a base class.

**Default instance is module-level and shared.** `FixedWidthFieldStrategy` is
stateless and side-effect-free, so a single `_DEFAULT_FIELD_STRATEGY` instance
is reused across all reader invocations. `field_strategy=None` resolves to it,
so every existing call site keeps the exact prior behaviour.

**`_extract_discriminator` retained as a shim.** The free function
`_extract_discriminator(line, disc)` is kept (it is imported by tests and is the
documented canonical extraction point per ADR 0008). It now delegates to
`_DEFAULT_FIELD_STRATEGY.extract(...)`, so there is a single implementation of
the slice logic and no drift risk. This preserves backward compatibility for
any caller importing it.

**Permissive short-line contract preserved.** Both the protocol docstring and
the fixed-width implementation keep the historical behaviour of returning the
empty string (never raising) when a line is too short for the discriminator
slice — matching the reader's pre-seam `_extract_discriminator` and the
validator's downstream expectations.

### Rejected alternatives

- **A. Add a `format` enum to `DiscriminatorConfig` and branch inside
  `_extract_discriminator`.** Pushes format knowledge into the config schema and
  grows a conditional in the reader every time a format is added — the opposite
  of a seam. Rejected.
- **B. Duplicate the reader for delimited input under `scripts/e2e_lib/`.**
  The exact anti-pattern ADR 0008 rejected (two copies of the dispatch priority
  rules to keep in sync). Rejected.
- **C. Do R-06a and R-06b in one change.** Larger, harder to review, and mixes
  a pure refactor with a new feature. The backlog explicitly slices them.
  Rejected.

## Consequences

### Positive

- R-06b (delimited strategy) becomes a small additive change: implement one
  `extract` method and pass it in. No dispatch logic to touch or re-test.
- Single source of truth for the slice logic preserved (`_extract_discriminator`
  now delegates to the default strategy).
- The seam is unit-testable in isolation, and a toy custom strategy proves the
  reader is genuinely format-agnostic below the extraction point.

### Negative / accepted

- One more indirection (a method call through a strategy object) on the
  hot per-line path. The strategy is a plain method call with no allocation per
  line (the instance is shared); the cost is negligible relative to file I/O.
- `scripts/e2e_lib/multi_record_file_parser._slice_fields` (the *field-set*
  slicer, distinct from the discriminator extractor) is **not** generalised by
  this ADR. It already tolerates non-fixed-width mappings by skipping fields
  without `position`/`length`. Generalising the full field-set extraction for
  delimited input is R-06b's concern; flagged here, not done.

### Migration

- No data migration, no on-disk format change, no config-schema change.
- `read_multi_record_file`'s new parameter is optional and defaults to the
  prior behaviour, so every existing caller (the validator wrapper, the L2b
  file parser) is unaffected.
- Byte-identical equivalence verified: a SHA-256 over the reader's full row
  stream (record type / line number / raw line / discriminator / row class) is
  identical with the default strategy and with an explicit
  `FixedWidthFieldStrategy()`, on both synthetic inputs and a real SHAW TRANERT
  fixture. The validator suite (`test_multi_record_validator.py`, built on top
  of the reader) passes unchanged.

## Implementation order

1. **Commit (this story) — `refactor: introduce record-reader field-strategy
   seam (fixed-width default)`**:
   - `src/validators/multi_record_reader.py`: add `RecordFieldStrategy`
     protocol + `FixedWidthFieldStrategy` default; thread optional
     `field_strategy` through `read_multi_record_file` → `_iter_rows` →
     `_dispatch_row`; `_extract_discriminator` becomes a shim over the default.
   - `tests/unit/test_multi_record_reader.py`: add seam tests, incl. the
     byte-identical SHA equivalence check and a custom-strategy dispatch test.
   - This ADR.
2. R-06b (#37) — add the delimited `RecordFieldStrategy` implementation and its
   tests on top of this seam.

## Open questions deferred

- Whether the delimited strategy (R-06b) should also generalise
  `multi_record_file_parser._slice_fields` (full per-record-type field set) or
  introduce a parallel field-set strategy. **Still deferred** beyond R-06b:
  R-06b (#37) generalises only the *discriminator* extraction (which record type
  a line is); the full field-set slicer for delimited per-type validation is a
  separate follow-up and is noted as such in the R-06b handoff.
- Whether `DiscriminatorConfig` should grow a delimiter/field-index shape for
  delimited files, or whether that lives entirely inside the delimited strategy.
  **Resolved by R-06b (#37):** `DiscriminatorConfig` gained optional
  `delimiter`, `column` (1-indexed int or name), and `columns` fields, with a
  model validator enforcing mode consistency (fixed-width requires
  `position`/`length`; delimited requires `column`). The delimited *extraction*
  lives in `DelimitedFieldStrategy`, selected from the config by
  `field_strategy_for`. This keeps selection config-only while the per-line
  logic stays in the strategy.
