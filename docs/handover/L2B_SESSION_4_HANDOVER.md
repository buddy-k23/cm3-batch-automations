# L2b SQL-Truth Gate — Session 4 Handover

**Generated:** 2026-05-27
**Predecessor:** [`docs/handover/L2B_SESSION_3_HANDOVER.md`](L2B_SESSION_3_HANDOVER.md) — read first; everything in this doc is a *delta* on session 3, not a replacement.
**Branch at handover:** `feature/issue-11-kill-file-search` (trunk, head TBD after this commit lands)
**Trunk at handover:** `feature/issue-11-kill-file-search`
**Issues this session touched:** #21 (commits 2–4 of 4 landed; MR not yet opened)

---

## 1. Where you are in the workflow

Session 4 finished issue #21 end-to-end: commits 2, 3, and 4 landed on
the feature branch and the validator's behaviour is byte-identical to
the pre-#21 baseline on both SHAW fixtures. #21 is **ready to be
merged into trunk**; #17 commits 2–3 are still blocked on that merge.

| Item | State | Notes |
|---|---|---|
| #21 commit 1 of 4 | ✅ Landed (`93ab664`) | Reader primitive — session 3 |
| #21 commit 2 of 4 | ✅ Landed (`a20471b`) | Validator rewire + drive-by type-honesty in `cross_type_validator.py` |
| #21 commit 3 of 4 | ✅ Landed (`77539a4`) | ADR 0008 |
| #21 commit 4 of 4 | ✅ Landed (`f0fa46a`) | AGENTS.md hard rule #1 carve-out |
| #21 merged to trunk | 🔄 Next | Fast-forward; single-developer workflow per session-4 user direction |
| #17 commit 1 of 3 | ✅ Already landed in session 2 (`1abdfd2`) | On `feature/issue-17-l2b-sql-truth-engine`, local-only |
| #17 commits 2–3 | ⏸ Blocked on #21 merge | Engine + parser + SQL bootstrap |
| #18, #19, #20 | ⏸ Blocked on #17 | Per design |

## 2. The dependency chain (unchanged from sessions 2/3)

```
#21 (refactor: extract reader primitive)        ← complete, awaiting merge
   ↓ blocks
#17 (L2b engine + SQL bootstrap)                ← commit 1 done; resume here next session
   ↓ blocks
#18 (SHAW TRANERT SQL artifacts)
   ↓ blocks
#19 (SHAW TRANERT reconciliation YAML + wiring)
   ↓ blocks
#20 (tests + CI + infographic pill flip to "wired")
```

## 3. Decisions made in session 4 (do not re-litigate)

These were confirmed by the user during the session before any code
was written or after specific findings surfaced:

| Decision | Choice | Why |
|---|---|---|
| `_extract_discriminator` / `_identify_record_type` on the validator after the rewire | **Keep both with `TODO(#21-followup)` markers** | `tests/unit/test_multi_record_validator.py::TestExtractDiscriminator` and `::TestIdentifyRecordType` test them directly; `_handle_unknown_rows` still calls `_extract_discriminator`. Removal would require deleting tests, which #21 acceptance forbids. |
| Drive-by type-honesty in `cross_type_validator.py` | **Widen `all_rows_types: List[str]` → `List[Optional[str]]` in both `validate()` and seven `_check_*` signatures** | The runtime *always* passed `None`s; only the annotation lied. Honest annotation has zero runtime cost; mypy goes from 94→93 errors (the one I introduced disappears). |
| Black reformat on `multi_record_validator.py` | **Defer** | The file was not black-clean at `93ab664` either; pre-existing style noise belongs in a separate `style:` commit, not in #21's refactor scope. |
| Flake8 enforcement | **Defer** | Pre-existing `.flake8` config bug (`#`-comment in `ignore=` value) prevents flake8 from running anywhere. Not #21's problem. |
| ADR 0008 status field | **`Proposed (flip to Accepted on MR merge)`** | Matches session-3 §5.3 guidance. ADRs 0006/0007 used `Accepted — Implemented YYYY-MM-DD` post-hoc; this works either way. |
| HTML report tool used for the smoke test | **`scripts/render_multi_record_html.py`** (the existing tool), not an ad-hoc script | First instinct was to roll my own; user pointed out the existing tool. Lesson logged in §7.3 below. |
| Pre/post-#21 validation parity test on TRANERT | **Full `validate()` end-to-end + HTML report + CSV sidecars on both `93ab664` and `f0fa46a`** | The commit-2 smoke test only covered `_group_rows`. The full `validate()` path also runs per-type validation, cross-type rules, `_enforce_expect`, `_handle_unknown_rows`. Required to confirm no regression. |
| `_read_and_group` missing-file behaviour | **Wrapper catches `MultiRecordReaderError`, logs, returns `({}, [], 0)`** | Preserves the validator's pre-#21 log-and-continue contract for downstream callers, while the primitive's eager-file-open semantics are inherited by L2b (#17). |
| Session-4 handover ordering vs. push/merge | **Handover → unicode fix → merge → #17 plan** | Safer than the user's literal 1→2→3→4; gets the handover committed before trunk moves. |

## 4. What changed in this session — concretely

### 4.1 Commit 2 of 4 — `a20471b refactor: rewire MultiRecordValidator._read_and_group on top of multi_record_reader`

**Files touched (2):**

- `src/validators/multi_record_validator.py`:
  - `_read_and_group` rewritten as a thin buffering wrapper around
    `read_multi_record_file()`. Return shape unchanged
    (`Dict[str, List[str]]`, `List[Optional[str]]`, `int`).
    Return annotation tightened from `List[str]` to
    `List[Optional[str]]` (honest about the `None`s the method has
    always been producing for unknown rows).
  - `_extract_discriminator` and `_identify_record_type` kept,
    each with a `# TODO(#21-followup)` marker explaining why
    (test backward-compat + `_handle_unknown_rows` still uses
    `_extract_discriminator`).
- `src/validators/cross_type_validator.py`:
  - Type-honesty drive-by: `all_rows_types: List[str]` →
    `List[Optional[str]]` in `validate()` and the seven `_check_*`
    method signatures. Two docstrings updated to mention that `None`
    entries indicate rows whose record type could not be identified.
  - Added `Optional` to the typing import.

**Verified parity:** the dict-of-list output of
`MultiRecordValidator()._group_rows()` is byte-identical pre and post
for both `data/samples/atoctran_shaw_20260514.txt` (125,903 rows, 8
record types) and `data/samples/tranert_shaw_20260422.txt` (103 rows,
9 record types). Captured via SHA-256 per record-type group.

### 4.2 Commit 3 of 4 — `77539a4 docs: add ADR 0008 for multi-record reader primitive`

**Files touched (1):** `docs/adr/0008-extract-multi-record-reader-primitive.md` (new, 321 lines).

House style matches ADRs 0005/0006/0007. Status: `Proposed (flip to
Accepted on MR merge)`. Sections cover Context, Decision (with all
sub-decisions documented: eager-file-open / lazy-body, single-row
look-ahead, `errors="replace"` preservation, side-effect-free logging,
BOM stripping, true line numbers), Rejected alternatives A/B/F mapped
to session-2 §5 enumeration, drive-by type-honesty rationale,
vestigial-validator-methods justification, Consequences, Implementation
order (with commits 1 and 2 already showing their SHAs), and Open
questions deferred.

ADR explicitly references ADR 0007's prescient note: *"if the next
harness milestone finds a fourth Valdo-internal bug, AGENTS.md hard
rule #1 should be revised to formalize the pattern."* This ADR is the
formalisation. Commit 4 implements it.

### 4.3 Commit 4 of 4 — `f0fa46a docs: add AGENTS.md hard rule 1 carve-out for primitive extraction`

**Files touched (1):** `AGENTS.md` (+8 lines).

Appended the carve-out paragraph from session-3 §5.4 verbatim to hard
rule #1. The three conditions (pure-refactor, ADR-documented, separate
MR) are listed. Numbered-list renumbering preserved.

### 4.4 TRANERT validation parity test

Beyond commit 2's per-group smoke test, ran the full `validate()` path
end-to-end on both `93ab664` (pre-rewire) and `f0fa46a` (post-rewire)
via `scripts/render_multi_record_html.py`, producing the full HTML
report set with per-record-type subpages and CSV sidecars.

Verified pre/post:

- Overall verdict: FAIL → FAIL ✓
- Total rows: 103 → 103 ✓
- Cross-type violations: 0 → 0 ✓
- Per-record-type valid counts, error counts, warning counts: all
  identical across 8 record types ✓
- All 14 CSV sidecars: **byte-identical via `fc`** on both error and
  warning files for all 7 record types that produced reports ✓

`rt_32010` had a pre-existing 5-error / 4-warning state in both runs
— the ORI conditional-suppression case documented in session-2 §5.1.
Not a regression.

Reports live (gitignored) at:

- `reports/smoke_21/SHAW_TRANERT_before/` (pre-#21 baseline)
- `reports/smoke_21/SHAW_TRANERT_after/`  (post-#21 confirmation)

### 4.5 Full-suite test parity

Ran `python -m pytest tests/unit/ --no-cov -q --tb=no` against both
`93ab664` and `f0fa46a`. Both produced the **same 7 pre-existing
failures** in unrelated files (`test_alembic_install`,
`test_chunked_validator_stats`, `test_downloader_service`,
`test_strict_mode_parity`, `test_trend_service`, `test_web_ui`,
`test_workflow_engine`). None touch the modules #21 changed. Confirmed
by stashing the #21 diff and re-running the failing files on
`93ab664`.

The two #21-relevant suites both pass cleanly:

- `tests/unit/test_multi_record_reader.py`: 14/14 ✓
- `tests/unit/test_multi_record_validator.py`: 51/51 ✓

## 5. Resume sequence — do these in order

### 5.1 Merge #21 to trunk

Single-developer workflow per user direction: fast-forward, no MR.

```powershell
git checkout feature/issue-11-kill-file-search
git merge --ff-only feature/issue-21-extract-multi-record-reader
git push origin feature/issue-11-kill-file-search
```

The user is the only developer on this project; the MR review pattern
session 2/3 anticipated is not in play.

After the push, trunk head moves from `68b079b` to `f0fa46a` (plus
this handover commit — which lands first, see §5.0 below).

### 5.0 [Before the merge] Land this handover doc

This handover is itself a commit on trunk. Land it before the merge so
the handover snapshot precedes the #21 work it describes:

```powershell
git add docs/handover/L2B_SESSION_4_HANDOVER.md
git commit -F <handover-message>  # use a temp file per §7.3
git push origin feature/issue-11-kill-file-search
```

Then proceed to §5.1.

### 5.2 Land the conftest Unicode fix as a standalone commit

Detailed plan in §6.1 below. This is a separate `fix:` commit on
trunk, not part of #21. Land it after the #21 merge so trunk's pytest
exit code is reliably 0 on Windows going forward.

### 5.3 Resume #17 commit 2 of 3

Detailed plan in §6.2. Two new modules under `scripts/e2e_lib/`:
`sql_bootstrap.py` (PEP-249 directory runner with Oracle `/`
PL/SQL-block support) and `multi_record_file_parser.py` (thin wrapper
around the #21 primitive that adds per-field slicing using each
record-type's mapping JSON). Then commit 3 of 3 of #17: the
`db_truth_comparator` engine.

### 5.4 Continue with #18 → #19 → #20

Per session-2 §3.5 and the issue descriptions. Each gets its own
branch and its own commit set (still single-developer, still
fast-forward to trunk).

## 6. Plans for the next four moves

### 6.1 Conftest Unicode fix — standalone `fix:` commit

**Symptom:** `tests/conftest.py::pytest_sessionfinish` prints `\u2713`
and `\u26a0` which crash on Windows cp1252 in the post-run teardown,
producing exit code 1 even when all tests pass. Linux/RHEL CI is
unaffected.

**Source:** `stash@{0}` on trunk from session 2 already contains the
fix (`✓`/`⚠` → `[OK]`/`[WARN]`). Plus it carries other unrelated WIP
(see session-2 §6) — must apply selectively.

**Plan:**

1. `git checkout feature/issue-11-kill-file-search` (trunk).
2. `git stash show -p stash@{0} -- tests/conftest.py > conftest.patch`
   to extract just the conftest hunk.
3. `git apply conftest.patch`.
4. Verify the hunk only changes `pytest_sessionfinish` print calls
   (lines ~158 and ~161). No imports, no other behaviour change.
5. Quality gate: `python -m pytest tests/unit/test_multi_record_reader.py --no-cov -q`
   — expect exit code 0 on Windows for the first time.
6. Commit:
   ```
   fix(tests): replace unicode characters in conftest sessionfinish for Windows compatibility

   The pytest_sessionfinish hook prints U+2713 (✓) and U+26A0 (⚠)
   when summarising the test-results-DB write. On Windows the default
   stdout codec is cp1252, which cannot encode these characters,
   raising UnicodeEncodeError in the teardown after all tests have
   passed. The exception is invisible to test runners' "did any test
   fail" check but pollutes the exit code (1 instead of 0), making
   CI scripts that read $LASTEXITCODE unreliable on Windows.

   Replace ✓ → [OK] and ⚠ → [WARN]. The semantic meaning of the
   teardown messages is preserved; Linux/RHEL CI is unaffected
   (cp1252 only matters on Windows console output).

   Tracks: docs/handover/L2B_SESSION_2_HANDOVER.md §6,
           docs/handover/L2B_SESSION_3_HANDOVER.md §6.1,
           docs/handover/L2B_SESSION_4_HANDOVER.md §6.1
   ```
7. `del conftest.patch && git push origin feature/issue-11-kill-file-search`.
8. **Do not** apply the rest of `stash@{0}` — the `src/api/*` files
   and the `TRANERT_DB_TESTING_SESSION_SUMMARY.md` are separate
   decisions and have their own dispositions (session-2 §6).

Estimated effort: 10 minutes.

### 6.2 #17 commit 2 of 3 — SQL bootstrap + multi-record file parser

**Context:** Session 2 §3.4 planned this as two new modules under
`scripts/e2e_lib/`. The session-3 ADR-0008 work made the second one
possible by extracting the reader primitive.

**Module 1: `scripts/e2e_lib/sql_bootstrap.py`**

API:

```python
def run_sql_directory(
    conn,
    directory: Path,
    *,
    params: dict[str, str] | None = None,
) -> SqlBootstrapResult:
    ...
```

Behaviour:

- Iterates `directory/*.sql` in sorted filename order.
- Each file is split into statements. Default separator: `;` on its
  own line. Oracle PL/SQL blocks are terminated by `/` on column 1 of
  its own line, per Oracle convention; the splitter recognises both.
- Each statement is executed via the PEP-249 connection's cursor.
- Idempotency is a **convention**, not enforced — files should use
  `CREATE OR REPLACE`, `IF NOT EXISTS`, `MERGE`, `TRUNCATE` as
  appropriate. The bootstrap does not check.
- `params` substitutes `:param_name` placeholders.
- Returns `SqlBootstrapResult(files_run: int, statements_executed:
  int, files: list[SqlFileResult])` — `@dataclass(frozen=True)` per
  `scripts/e2e_lib/` house style.
- Errors raise `SqlBootstrapError(ValueError)` with the failing file
  and statement number, then re-raise (no partial-success surfacing).

Test plan: `tests/unit/test_e2e_sql_bootstrap.py`:
- Empty directory → no-op, returns zeroed result.
- One file with one statement → statement executed once.
- One file with multiple `;`-terminated statements → each executed.
- One file with an Oracle PL/SQL block terminated by `/` → block
  executed as one statement.
- Parameter substitution.
- Statement failure mid-file → raises, lists the failing
  `(file, stmt_index)`.
- DB stub via `MagicMock` for the PEP-249 connection (no real Oracle).

**Module 2: `scripts/e2e_lib/multi_record_file_parser.py`**

API:

```python
def iter_parsed_rows(
    file_path: Path,
    umbrella_config: MultiRecordConfig,
    mapping_paths: dict[str, Path],
) -> Iterator[tuple[str, dict[str, str], int]]:
    """Yield (record_type, parsed_fields_dict, line_number) per row."""
```

Behaviour:

- Imports `read_multi_record_file` from `src.validators.multi_record_reader`
  (the #21 primitive).
- For each `ParsedRow` from the primitive, looks up `mapping_paths[row.record_type]`,
  loads the mapping JSON (or caches it), uses its field definitions to
  slice `row.raw_line` into a `dict[str, str]` of field-name → trimmed-value.
- For each `UnknownRecordTypeRow`, yields `(None, {}, row.line_number)`
  or raises `MultiRecordFileParserError` depending on the umbrella's
  `default_action` ("error" → raise, "warn"/"skip" → yield with None
  record_type and let the consumer decide).
- Mapping JSON shape is `src/config/mapping_parser.py`'s
  `MappingConfig` — load via `MappingConfig.from_file(path)` and use
  its existing field definitions; do not re-implement slicing.

Test plan: `tests/unit/test_e2e_multi_record_file_parser.py`:
- Happy path with a synthetic 3-record-type fixture, fields slice
  cleanly.
- Unknown discriminator with `default_action=error` → raises.
- Unknown discriminator with `default_action=warn` → yields with
  `None` record_type.
- Missing mapping JSON for a configured record type → raises at
  construction time, not mid-iteration (fail-fast, matches the
  primitive's eager-file-open pattern).
- Mapping cache is populated on first encounter per record type, not
  per row.

**Commit message:** `feat: add SQL bootstrap and multi-record file parser`.

**Acceptance:**
- Both modules import cleanly from `scripts/e2e_lib/`.
- Both tests directories pass.
- Full pytest suite still passes (no regressions in the validator or
  the reader; this is purely additive).
- `mypy scripts/e2e_lib/sql_bootstrap.py scripts/e2e_lib/multi_record_file_parser.py`
  is clean (per session-2 §7.2, mypy doesn't run against `scripts/e2e_lib/`
  by default but the modules should be type-clean anyway).

### 6.3 #17 commit 3 of 3 — db_truth_comparator engine

**Module:** `scripts/e2e_lib/db_truth_comparator.py`.

Per session-2 §3.4: the engine consumes a `ReconciliationSpec`
(landed in commit 1 of #17 at `1abdfd2`), a SQL truth query, and an
iterator from `multi_record_file_parser.iter_parsed_rows` (from
commit 2 of #17). It reconciles row-by-row, producing a list of
`ReconciliationViolation` value types.

Plan can be filled out at the start of next session — the API surface
depends on the `ReconciliationSpec` model fields, which I haven't
re-read since session 2 (it's local on `feature/issue-17-l2b-sql-truth-engine`).

### 6.4 Issue follow-ups not yet filed

Per session-4 user direction, no new issues filed. The follow-ups to
consider after #21 merges:

1. **Delete vestigial validator methods** — `_extract_discriminator`
   and `_identify_record_type` on `MultiRecordValidator`, plus their
   two test classes in `tests/unit/test_multi_record_validator.py`,
   plus the call site in `_handle_unknown_rows` (which would need to
   use the free function from the primitive). Pure cleanup, one MR.
   Recommended by ADR 0008's "Open questions deferred" section.
2. **`.flake8` config bug** — the `ignore=` value contains a `#`
   comment which modern flake8 rejects with
   `ValueError: Error code '#' supplied to 'ignore' option does not
   match '^[A-Z]{1,3}[0-9]{0,3}$'`. Breaks `flake8` everywhere in the
   repo. One-line fix in `.flake8`.
3. **mypy whole-tree cleanup** — 93 errors, mostly missing `Optional`
   annotations, missing stub packages (`pandas-stubs`, `types-psutil`,
   `types-requests`), and several legitimate type bugs in
   `chunked_validator`, `rule_engine`, and `comparators`. Out of scope
   for #21 but worth a dedicated MR.

## 7. Things this session learned that weren't in session 3

### 7.1 Reading the validator end-to-end revealed a third semantic worth noting

`MultiRecordValidator._read_and_group`'s return-type annotation
`Tuple[Dict[str, List[str]], List[str], int]` was technically wrong:
the middle list has always contained `None` for unknown rows. The
mypy type-checker only catches this when a caller passes the result
to something annotated `List[str]`, which
`CrossTypeValidator.validate` does. Both annotations were lies that
mypy didn't surface because they agreed with each other.

The lesson: when extracting a primitive, **tighten annotations on the
caller boundary first**, run mypy, fix the breakage by tightening the
annotations all the way through. This surfaces type lies that the
wider tree has been hiding from itself.

### 7.2 The Windows console exit-code issue is more pervasive than session 3 framed it

Session 3 §6.1 framed the conftest Unicode crash as a Windows-only
nuisance. Session 4 noticed two more places where Windows shell
defaults silently corrupt output:

- `python -m black <file> & python -m flake8 <file> & python -m mypy <file>`
  chained with `&` in cmd suppresses intermediate exit codes and
  fuses all output into one stream. Use one command at a time.
- `findstr` filters expand glob characters (`*`, `?`, `|`) unless
  every metachar in the search pattern is independently quoted. The
  `python -m pytest … | findstr /R …` pipeline used in session 3
  silently dropped its filter half the time.
- `git commit -m "message with | pipe"` parses the `|` as a shell
  pipe even inside the quoted argument when invoked via `cmd /c`.
  Solution: always write multi-paragraph commit messages to a temp
  file (under `$env:TEMP\valdo_commit_*.txt`) and use `git commit
  -F <file>`. Established as a session-3 convention; session 4
  followed it for every commit.

### 7.3 Always check `scripts/` before writing a new script

Session 4 started writing an ad-hoc HTML report generator
(`scratch_validate_tranert.py`) and was halfway through when the user
pointed at `reports/smoke/SHAW_TRANERT/index.html` — an already-
existing tool's output. `scripts/render_multi_record_html.py` does
exactly what was needed, written for exactly this kind of smoke test,
with PII redaction and CSV sidecars already wired through
`ValidationReporter`.

Lesson: before writing any standalone diagnostic script in this repo,
`grep` for an existing one. `scripts/` is large.

### 7.4 The pre-existing 5-error / 4-warning state on `rt_32010` is real and load-bearing

Both pre-#21 (`93ab664`) and post-#21 (`f0fa46a`) HTML reports show
the same `REP-TYP-ORI` empty-field findings on row 1 of `rt_32010`
(the ORI record type). This is the ORI conditional-suppression case
session-2 §5.1 documented: `getTranertCus32010` in the Java returns
`null` when `chgOffCd != "1"`, and the sample file happens to have
one row matching that suppression condition.

The L2b reconciliation YAML (#19) for `rt_32010` will need to mark
these fields `regression_only: true` or otherwise model the
conditional suppression, since SQL truth will also produce `null` for
the same rows. Calling this out here so #19 doesn't trip over it.

## 8. Decisions log — combined session-1 + 2 + 3 + 4

Cross-reference: session-3 handover §8 has the first eleven. Session
4 added:

| Decision | Why | Where it's documented |
|---|---|---|
| Validator's vestigial methods stay with `TODO(#21-followup)` | Test backward-compat forbids removal; `_handle_unknown_rows` still uses one of them | This doc §3, ADR 0008 |
| Drive-by type-honesty widening in `cross_type_validator.py` is in scope for #21 | Same-directory, same-module-family, no runtime change, makes mypy honest | This doc §3, ADR 0008 |
| Defer black / flake8 cleanups | Pre-existing tree noise, not #21's problem | This doc §3 |
| Use `scripts/render_multi_record_html.py` for the HTML smoke test | The tool already exists with the right CSS and PII redaction | This doc §3, §7.3 |
| Full `validate()` parity test on TRANERT, not just `_group_rows` | The commit-2 smoke only covered grouping; the full path runs per-type + cross-type + expect checks | This doc §3, §4.4 |
| Conftest Unicode fix lands after #21 merge, not before | Doesn't block #21; cleaner as its own commit on trunk after the merge | This doc §3, §6.1 |
| Session-4 ordering: handover → unicode → merge → #17 plan | Safer than literal 1-4 from the user's note; handover is committed before trunk moves | This doc §3 |
| Single-developer workflow for #21 → trunk merge | User is sole developer; MR review process not in play | This doc §5.1 |

## 9. Open questions for the next session

These were not resolved during session 4:

1. **Should the vestigial-validator-methods cleanup be filed as an
   issue now**, or remembered as informal follow-up? User said "no
   new issues" in session 4; defaulting to "no" until asked.
2. **Should `.flake8` config bug** be fixed as part of the next
   trunk-touching commit (e.g. the unicode fix), or as a separate
   one-line `fix:`? Recommendation: separate.
3. **MR opening for #17 onward.** Session 4 confirmed
   single-developer workflow for #21 (fast-forward, no MR). Confirm
   this remains the workflow for #17, #18, #19, #20 as well, or if
   the user wants to start using MRs once external review enters the
   picture.
4. **Infographic pill flip in #20.** Per session-2 §7.4, the
   architecture-tab pills flip from `designed/planned/not yet built`
   → `wired` when L2b lands. Confirm #20's acceptance still requires
   that flip when #20 is picked up.

## 10. Quick links

- [#17 — L2b engine + SQL bootstrap](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/17) — commit 1 done, 2–3 pending; resume after #21 merge
- [#18 — SHAW TRANERT SQL artifacts](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/18)
- [#19 — SHAW TRANERT reconciliation YAML + wiring](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/19)
- [#20 — tests + CI + infographic pill flip](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/20)
- [#21 — refactor: extract multi-record reader primitive](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/21) — **complete; merging to trunk in §5.1**
- [`docs/handover/L2B_SESSION_3_HANDOVER.md`](L2B_SESSION_3_HANDOVER.md) — predecessor
- [`docs/handover/L2B_SESSION_2_HANDOVER.md`](L2B_SESSION_2_HANDOVER.md) — original L2b doc
- [`docs/adr/0008-extract-multi-record-reader-primitive.md`](../adr/0008-extract-multi-record-reader-primitive.md) — ADR landed in commit 3 of #21
- `docs/handover/TRANERT_DB_TESTING_SESSION_SUMMARY.md` — session-1 design doc (still stashed)
- `docs/Valdo-Infographic.html` Architecture tab → "E2E harness gates" section
- Java ground truth (untracked, intentional): `config/templates/TranertMapper (1).java`, `config/templates/DAOOperations (2).java`
- SHAW smoke-test fixtures (committed): `data/samples/atoctran_shaw_20260514.txt`, `data/samples/tranert_shaw_20260422.txt`
- TRANERT pre/post-#21 HTML reports (gitignored): `reports/smoke_21/SHAW_TRANERT_before/index.html`, `reports/smoke_21/SHAW_TRANERT_after/index.html`
