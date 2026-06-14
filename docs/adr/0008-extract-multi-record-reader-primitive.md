# ADR 0008: Extract Multi-Record Reader Primitive

- Status: Accepted
- Date: 2026-05-27
- Accepted: 2026-06-11 (landed on trunk feature/valdo-engine-v3, commits 93ab664, a20471b)
- Supersedes: none
- Related: [ADR 0005](0005-multi-record-pipeline-dispatch.md),
  [ADR 0006](0006-fix-ba-friendly-rules-execution.md),
  [ADR 0007](0007-template-converter-preserve-string-literals.md),
  issues #17 and #21,
  `docs/handover/L2B_SESSION_2_HANDOVER.md`,
  `docs/handover/L2B_SESSION_3_HANDOVER.md`

## Context

The L2b SQL-Truth gate (issue #17) is a new E2E-harness gate that
reconciles a Valdo output file row-by-row against a SQL truth source
derived from the same input data. It needs to **iterate** a multi-record
fixed-width file — dispatching each line to its record type by
discriminator — without dragging in any of the validator-side concerns
(per-type schema validation, cross-type rules, `RuleViolation`
generation, `default_action` handling, audit logging, DataFrame
buffering, temp-file writes).

That iteration logic exists in exactly one place today:
`MultiRecordValidator._read_and_group` inside
`src/validators/multi_record_validator.py`. It is a private method on a
class whose `validate()` entry point also runs the full validation
pipeline. There is no way to reuse the file-reading and
discriminator-dispatch behaviour without either:

1. Calling `MultiRecordValidator.validate()` and discarding everything
   except the raw row groups (wasteful, and the side-effects — audit
   writes, per-type validation runs — are not free or idempotent).
2. Copy-pasting the ~40-line `_read_and_group` body and its two
   helpers (`_extract_discriminator`, `_identify_record_type`) into
   `scripts/e2e_lib/`.
3. Adding a back-door public method on `MultiRecordValidator` that
   exposes the internals.

None of these are acceptable. Option 2 in particular is a known
anti-pattern in this codebase — ADR 0007 noted the same shape
("`bulk_convert_mappings.py` reads its own copy of the CSV with
`dtype=str`… the conversion pass loses it") as the cause of a silent
data-corruption seam that took a SHAW smoke test to discover.

### AGENTS.md hard rule #1

Hard rule #1 forbids `src/` modifications from harness work. ADRs 0005,
0006 (MR 1 + MR 2), and 0007 have already documented four exceptions,
all for the same shape: harness work surfaces a Valdo-internal gap and
an ADR authorises a minimal, additive fix. ADR 0007's "Negative /
accepted" section flagged that "if the next harness milestone finds a
fourth Valdo-internal bug, AGENTS.md hard rule #1 should be revised to
formalize the 'harness work surfaces internal bugs → ADR-authorized
minimal fix' pattern rather than treating each case as an exception."

This is that case — except this is not a bug, it is a missing
*primitive*. The reader logic is correct; it is just not exposed in a
form that allows reuse. The carve-out this ADR authorises is therefore
slightly different in shape from 0005/0006/0007: those ADRs fix bugs;
this one extracts a primitive. Commit 4 of #21 appends a carve-out
paragraph to AGENTS.md hard rule #1 making the pattern explicit going
forward.

### Why option 2 (duplicate) was specifically rejected

The duplicate would have:

- Two copies of the discriminator-dispatch priority rules to keep in
  sync (`position="first"` → `position="last"` → value-based `match`,
  with insertion-order tie-break).
- Two copies of the line-normalisation rules (CRLF stripping, blank-
  line filtering).
- A drift-detection test would be needed to confirm the copies match
  (alternative F discussed in `docs/handover/L2B_SESSION_2_HANDOVER.md`),
  which is complexity tax for a problem extraction solves outright.
- Adding BOM stripping or any future improvement would require two
  edits, two reviews, and a third drift check.

`MultiRecordConfig`'s schema is already shared between the validator
and the L2b gate via `src/config/multi_record_config.py`. The reader is
the next layer down; sharing it is the natural shape.

## Decision

Extract `read_multi_record_file()` as a pure primitive at
`src/validators/multi_record_reader.py`. Rewire
`MultiRecordValidator._read_and_group` as a thin buffering wrapper on
top of it. No behaviour change for any existing validator consumer.

### Public API

```python
# src/validators/multi_record_reader.py

from typing import Iterator, Union
from pathlib import Path

from src.config.multi_record_config import MultiRecordConfig


class MultiRecordReaderError(ValueError):
    """Raised when the primitive is misused (e.g. file not found)."""


@dataclass(frozen=True)
class ParsedRow:
    record_type: str
    line_number: int            # true 1-indexed file line number
    raw_line: str
    discriminator_value: Optional[str]


@dataclass(frozen=True)
class UnknownRecordTypeRow:
    line_number: int
    raw_line: str
    discriminator_value: str


def read_multi_record_file(
    file_path: Union[str, Path],
    config: MultiRecordConfig,
) -> Iterator[Union[ParsedRow, UnknownRecordTypeRow]]:
    ...
```

The two row dataclasses are `frozen=True` value types — consumers may
hash them or use them as dict keys without worrying about mutation.

### Design choices

**Eager file-open, lazy body.** The public function does
`path.open(...)` itself and raises `MultiRecordReaderError` at call
time on a missing file. The generator body is split into an internal
`_iter_rows()` so that "missing file" surfaces immediately, not on the
caller's first `next()`. This matters for L2b: the gate is a
workflow-driver step, and a missing file should fail the step
synchronously, not deep inside a streaming reconciliation loop where
the partial state has to be unwound. The validator wrapper catches the
error and translates it to its existing log-and-return-empty contract,
preserving backward compatibility.

**Single-row look-ahead for `position="last"`.** Dispatch priority
preserved from the validator is: `position="first"` on the first non-
empty row, then `position="last"` on the last non-empty row, then
value-based `match`. Step 2 requires knowing whether the current row
is the last non-empty one, but the primitive must not buffer the
whole file (that would defeat the point of having a streaming reader
for L2b, which is expected to be invoked on files with millions of
rows). Solution: hold one row in a `pending` slot; emit it as soon
as the next non-empty row arrives; emit the final `pending` row at
EOF marked `is_last=True`. Memory is O(1) in the file size.

**`errors="replace"` preserved.** The validator's historical
`open(encoding="utf-8", errors="replace")` behaviour is preserved
verbatim. SHAW files have historically contained occasional
unmappable bytes; tightening this without coordinating with operators
would surface as a new flood of errors on files that previously
processed cleanly.

**No logging side-effects in the primitive.** The primitive declares
a `_logger = logging.getLogger(__name__)` for handler attachment by
callers, but emits no log lines itself. Logging is the validator
wrapper's responsibility; L2b will attach its own JSONL audit sink.
This matches the `scripts/e2e_lib/` house style of side-effect-free
service modules.

**Additive behaviour changes inherited by the validator.** Two
behaviours of the primitive are stricter than the validator's pre-#21
inline logic. Both were pre-approved as additive in the session-3
handover (§3) on the grounds that no existing consumer reads them:

1. **BOM stripping** — a leading UTF-8 BOM on line 1 is stripped.
   The validator never did this. No SHAW file currently ships a BOM,
   so the change is detectable only on synthetic inputs.
2. **`line_number` is the true file line number** with blank lines
   counted. The validator's `_read_and_group` filtered blank lines
   before indexing, so any line-number-adjacent index it computed was
   non-blank. The validator does not expose any line number through
   its public API; `line_number` is read only by the new primitive's
   consumers, all of which are downstream of #21.

### Type-honesty drive-by

Discovered while implementing commit 2 of #21:
`MultiRecordValidator._read_and_group` has *always* appended `None`
to its `all_rows_types` return value for unknown rows, but its
annotation lied as `List[str]`.
`CrossTypeValidator.validate` and its seven `_check_*` methods
likewise declared `List[str]` for the same parameter despite
receiving `None` entries at runtime. Commit 2 widens both to
`List[Optional[str]]`. No runtime behaviour change; mypy is now
honest.

### Vestigial validator methods

`MultiRecordValidator._extract_discriminator` and
`MultiRecordValidator._identify_record_type` are kept (the canonical
implementations now live in `multi_record_reader._extract_discriminator`
and `multi_record_reader._dispatch_row`). They are retained because:

1. `tests/unit/test_multi_record_validator.py::TestExtractDiscriminator`
   and `::TestIdentifyRecordType` call them directly. Removing them
   would require deleting those tests, which the #21 acceptance
   criteria forbid ("all 1,063+ existing tests pass without
   modification").
2. `MultiRecordValidator._handle_unknown_rows` still calls
   `self._extract_discriminator` to compute the violation `value`
   field. Replacing that with the free function would be a
   behavioural change adjacent to the validator's violation contract.

Both methods carry a `TODO(#21-followup)` marker so the redundancy is
visible to anyone reading the code, and a follow-up issue can decide
whether to delete them along with their tests.

### Rejected alternatives

- **A. Full duplication into `scripts/e2e_lib/`.** Discussed in
  Context. Schema-drift inevitable; two readers to maintain forever.
- **B. Import `MultiRecordConfig`, duplicate the reader (~70 lines)
  in `scripts/e2e_lib/multi_record_reader.py`.** Same drift problem
  as A; only saves the AGENTS.md carve-out paragraph.
- **F. Duplicate plus a drift-detection test.** Complexity tax for a
  problem extraction solves outright. The drift test itself would
  have to know both implementations and assert behavioural
  equivalence, which is the test version of the same anti-pattern.

These map to the same options enumerated in the session-2 handover
§5 of the work that became #21.

## Consequences

### Positive

- Single source of truth for multi-record file reading. ADR 0005
  established `MultiRecordConfig` as the shared schema; this ADR
  extends that to the reader.
- L2b SQL-Truth gate (#17) becomes implementable without copy-paste.
- A future mapping-driven `valdo regenerate` capability (ADR 0012, Option A;
  the `L2_regeneration` gate itself was retired) could reuse the same primitive.
- Easier to unit-test the reader in isolation. The new
  `tests/unit/test_multi_record_reader.py` covers 14 cases including
  edge cases the validator's test suite never hit (empty file, BOM,
  single-row file with both `position="first"` and `position="last"`
  configured, generator-not-list semantics via `next()`).
- The eager-file-open pattern means L2b can fail fast on missing
  files at workflow-driver time, not deep in a streaming
  reconciliation.

### Negative / accepted

- `src/` discipline relaxed a fifth time (after ADR 0005, ADR 0006
  MR 1, ADR 0006 MR 2, and ADR 0007). The pattern ADR 0007 flagged
  has now materialised. Commit 4 of #21 formalises the carve-out in
  AGENTS.md hard rule #1 so this is the *last* per-case exception;
  future primitive extractions follow the documented carve-out
  process instead.
- `MultiRecordValidator._extract_discriminator` and
  `_identify_record_type` become dead-code-equivalent inside the
  validator. They are retained for test backward-compatibility; a
  follow-up issue should delete both methods and their tests in a
  single targeted cleanup once #21's MR has merged.
- The primitive opens the file eagerly; if a caller wants the
  generator to be cheap to construct without committing to a file
  read, they cannot get that. Acceptable: no current or planned
  caller wants that semantics.

### Migration

- No data migration. No on-disk format change. No API change to
  `MultiRecordValidator.validate()` — its return shape, behaviour
  on missing files, and per-type result structure are unchanged.
- No existing tests modified. 51/51 `test_multi_record_validator.py`
  cases pass after commit 2 of #21 without edits.
- Smoke-test verified on the two SHAW fixtures
  (`data/samples/atoctran_shaw_20260514.txt` 125,903 rows, 8 record
  types; `data/samples/tranert_shaw_20260422.txt` 86 rows, 9 record
  types): the `Dict[str, List[str]]` output of
  `MultiRecordValidator()._group_rows()` is byte-identical (SHA-256
  per record-type group) before and after commit 2.

## Implementation order

1. ~~Commit 1 — `feat: add multi_record_reader primitive`~~
   **Done** at `93ab664`:
   - `src/validators/multi_record_reader.py` (new, ~270 lines).
   - `tests/unit/test_multi_record_reader.py` (new, 14 tests).
2. ~~Commit 2 — `refactor: rewire MultiRecordValidator._read_and_group on top of multi_record_reader`~~
   **Done** at `a20471b`:
   - `src/validators/multi_record_validator.py`:
     `_read_and_group` becomes a thin buffering wrapper.
     `_extract_discriminator` and `_identify_record_type` kept with
     `TODO(#21-followup)` markers.
   - `src/validators/cross_type_validator.py`: drive-by type-honesty
     widening of `all_rows_types: List[str]` → `List[Optional[str]]`
     in `validate()` and seven `_check_*` methods.
   - Smoke test verified on both SHAW fixtures: no diff.
3. **Commit 3 — `docs: add ADR 0008 for multi-record reader primitive`**
   *This file.*
4. Commit 4 — `docs: add AGENTS.md hard rule 1 carve-out for primitive extraction`
   appends the carve-out paragraph specified in #21's description.

## Open questions deferred

- Whether `_extract_discriminator` and `_identify_record_type` on
  `MultiRecordValidator` should be removed in a follow-up issue
  (along with their two test classes). Recommended yes, but not in
  #21's scope — the test modification rules it out.
- Whether the carve-out language in AGENTS.md hard rule #1 should
  also explicitly cover the type-honesty drive-by pattern (widening
  annotations to match runtime reality without behavioural change).
  Today's carve-out language is phrased around "exposing a primitive
  that already exists"; type-honesty fixes don't quite fit. Defer
  until a third type-honesty drive-by surfaces; revisit then.
- Whether L2b will need a `read_multi_record_file_with_fields()`
  variant that also runs per-record-type field slicing (using the
  per-type mapping JSON). The session-2 handover §3.4 plans this as
  a thin wrapper at `scripts/e2e_lib/multi_record_file_parser.py`
  rather than as a primitive extension; revisit if the wrapper grows
  past ~30 lines.
