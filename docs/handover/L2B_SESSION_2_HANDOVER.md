# L2b SQL-Truth Gate — Session 2 Handover

**Generated:** 2026-05-27
**Predecessor:** `docs/handover/TRANERT_DB_TESTING_SESSION_SUMMARY.md` (still stashed, design source of record but contains corrections — see §5).
**Branch at handover:** `feature/issue-17-l2b-sql-truth-engine` (head `1abdfd2`).
**Open issues this work created:** #17, #18, #19, #20, #21.

---

## 1. Where you are in the workflow

You are in the middle of implementing **issue #17** (L2b SQL-Truth gate — generic comparator engine and SQL bootstrap) on branch `feature/issue-17-l2b-sql-truth-engine`. One commit landed. Work was deliberately paused mid-issue because a design discovery during commit 2 spawned a new prerequisite issue **#21**.

**You must complete #21 before continuing #17.** See §3 for the precise resume sequence.

| Item | State | Notes |
|---|---|---|
| #17 commit 1 of 3 | ✅ Landed (`1abdfd2`) | `ReconciliationSpec` model + YAML loader; 36 unit tests, all green |
| #17 commits 2–3 | ⏸ Blocked on #21 | Engine + parser + SQL bootstrap |
| #18, #19, #20 | ⏸ Blocked on #17 | Per design — sequential dependency chain |
| #21 | 🆕 Created, ready to start | Pure refactor of `src/validators/multi_record_validator.py` |

## 2. The five issues and how they relate

```
#21 (refactor: extract reader primitive)        ← do this first
   ↓ blocks
#17 (L2b engine + SQL bootstrap)                ← commit 1 already done
   ↓ blocks
#18 (SHAW TRANERT SQL artifacts)
   ↓ blocks
#19 (SHAW TRANERT reconciliation YAML + wiring)
   ↓ blocks
#20 (tests + CI + infographic pill flip to "wired")
```

Cross-reference: each issue's description carries `Depends on` and `Scope — out` sections that name the downstream issue picking up deferred work.

## 3. Resume sequence — do these in order

### 3.1 Switch branches and start #21

```powershell
# from feature/issue-17-l2b-sql-truth-engine
git checkout feature/issue-11-kill-file-search   # current trunk
git pull
git checkout -b feature/issue-21-extract-multi-record-reader
```

### 3.2 Implement #21

Pure refactor, 4 commits expected, single MR. Full scope in [#21](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/21). Highlights:

1. New `src/validators/multi_record_reader.py` with `read_multi_record_file(path, config) -> Iterator[ParsedRow | UnknownRecordTypeRow]`. Generator-based, pure, no logging/audit side-effects.
2. Refactor `src/validators/multi_record_validator.py::MultiRecordValidator._read_and_group` into a thin buffering wrapper around the new primitive. Public surface unchanged.
3. New `tests/unit/test_multi_record_reader.py` covering empty file, BOM, CRLF, `position: "first"`, discriminator-based dispatch, unknown discriminators, short lines.
4. New `docs/adr/0008-extract-multi-record-reader-primitive.md`.
5. Append AGENTS.md hard rule 1 carve-out paragraph (exact wording in #21 description).

**Acceptance:** all 1,063+ existing tests pass without modification. The validator's `groups: Dict[str, List[...]]` return shape is identical before/after.

### 3.3 Merge #21 to trunk

After review and CI green, merge the #21 MR into `feature/issue-11-kill-file-search` (or whatever trunk has moved to).

### 3.4 Resume #17 commit 2

```powershell
git checkout feature/issue-17-l2b-sql-truth-engine
git rebase feature/issue-11-kill-file-search   # pick up #21
```

Commit 2 plan: `feat: add SQL bootstrap and multi-record file parser`. Two modules:

- **`scripts/e2e_lib/sql_bootstrap.py`** — `run_sql_directory(conn, directory, *, params=None)`. PEP-249 connection. Idempotency is a convention (CREATE OR REPLACE / IF NOT EXISTS / MERGE / TRUNCATE), not enforced. Supports Oracle PL/SQL blocks terminated by `/` on column 1.
- **`scripts/e2e_lib/multi_record_file_parser.py`** — **thin wrapper** around the #21 primitive. Adds per-field slicing using the per-record-type mapping JSON (since the primitive only handles line-level dispatch, not field-level slicing). Yields `(record_type, parsed_fields_dict, line_number)` tuples.

Then commit 3: `feat: add db_truth_comparator engine`.

### 3.5 Continue with #18 → #19 → #20

Per issue descriptions. Each is its own branch, its own MR.

## 4. Git state at handover

| | |
|---|---|
| **Current branch** | `feature/issue-17-l2b-sql-truth-engine` |
| **Head commit** | `1abdfd2` (`feat: add ReconciliationSpec model and YAML loader for L2b SQL-Truth gate`) |
| **Parent branch / trunk** | `feature/issue-11-kill-file-search` (per the previous session — confirm this is still trunk before branching #21) |
| **Branched from** | `dd63736` on the trunk (`docs: surface L2b SQL-Truth gate design across infographic tabs`) |
| **Stash** | `stash@{0}` — see §6 |
| **Untracked files** | `config/templates/DAOOperations (2).java`, `config/templates/TranertMapper (1).java` (intentionally not committed, per session-2 user direction), `mappings/excel/~$TRANERT_SHAW_Mappings.xlsx` (Excel lock file, can't be removed while Excel has it open) |

## 5. Corrections to the original handover doc

`docs/handover/TRANERT_DB_TESTING_SESSION_SUMMARY.md` was written before the Java source files were read end-to-end. The Java is the ground truth; the handover summarised it inaccurately in places. Comment on #17 (https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/17) has the full 13-item correction list. Most-important corrections:

1. **32010 (ORI) is conditionally suppressed** — `getTranertCus32010` returns `null` when `chgOffCd != "1"`. Cardinality is `zero_or_one_per_driver_row` with predicate `CHG_OFF_CD = '1'`, not `one_per_driver_row`.
2. **32005 (CUS) is per-contact, not per-account.** Composite key `(ACCT_NUM, CONTACT_ID)`. Cardinality `many_per_driver_row`.
3. **32025 has six more directly-testable fields** than the handover claimed (`RPO-COD-COD`, `DAT-LAS-RPO-COD`, `ORG-LVL-NUM6-COD`, `LCE-GEO-COD-COD`, `OGL-LN-OFC-COD`, `LN-OFC-CUR-COD`).
4. **32005 `CIF-CSM-INF-IND-CUS` and `ECOA-CODE-CUS` are deterministic SQL CASE**, not "skip". The bankruptcy-chapter cases (A/B/C/D when both dates null; E/F/G/H when only dismissed null; Q for dismissed-present and for `consInfoInd ∈ {I..Z}`) are fully reproducible in SQL.
5. **`DAOOperations.stateProvinceMap` is a `public static` field** — per-batch state. If the JVM serves multiple batches, stale state could leak. L2b reconciliation YAML must mark any field that depends on cross-run state as `regression_only: true`.

Anything that contradicts the original handover, the Java + the corrections comment win.

## 6. The stash — what's in it, what to do with it

`stash@{0}` was created on `feature/issue-11-kill-file-search` and contains:

| File | What it is | What to do |
|---|---|---|
| `tests/conftest.py` | **Unicode crash fix** — replaces `✓`/`⚠` with `[OK]`/`[WARN]` in `pytest_sessionfinish` for Windows cp1252 compatibility. Causes exit code 1 on Windows even when all tests pass. | Apply as its own `fix:` commit on trunk before #21 (or on the #21 branch if you prefer). Either way, **not** inside #17. |
| `docs/handover/TRANERT_DB_TESTING_SESSION_SUMMARY.md` | Design source of record (with the corrections in §5 applied). | Commit to `main`/trunk as a `docs:` commit. Already referenced by all five issue descriptions. |
| `src/api/models/file.py`, `src/api/routers/multi_record.py` | Unknown — possibly user work in progress from before the L2b session. | **Don't touch.** Confirm with the user whether these are theirs or were accidentally pulled into the stash. |
| `_e_clean.txt`, `_i_clean.txt`, `_u_clean.txt` | Look like scratch/temp files from a prior session. | Likely safe to discard. Confirm with user. |
| `scripts/_patch_files_router.py` | Underscore-prefixed → looks like a one-shot patch script. Probably scratch. | Confirm with user; likely add to `.gitignore` or delete. |

Recovery: `git stash pop` from the trunk branch. Then commit/discard selectively.

## 7. Things I want the next session to know

### 7.1 Conventions discovered in `scripts/e2e_lib/`

After reading existing modules (`failure_sink.py`, `jsonl_logger.py`, `secret_resolver.py`, `path_resolver.py`, `run_source.py`):

- `from __future__ import annotations` at the top.
- `@dataclass(frozen=True)` for value types, `@dataclass` for service types. **Do not use Pydantic** here — the rest of `scripts/e2e_lib/` deliberately avoids it (Pydantic is reserved for `src/`).
- Single `<Module>Error(ValueError)` class per module.
- Google-style docstrings with `Args:` / `Returns:` / `Raises:`.
- Classmethod factory pattern (`from_files`, `for_run`) for construction with validation.
- `yaml.safe_load` for YAML.
- No `print()` — use `src/utils/logger.py`.
- No `os.environ.get()` outside `scripts/e2e_lib/secret_resolver.py`.
- Tests live under `tests/unit/test_e2e_<module>.py` (the `test_e2e_` prefix is the convention, not just `test_<module>.py`).

### 7.2 mypy

AGENTS.md says `mypy src/`. `scripts/e2e_lib/` is not mypy-checked. Write full type hints anyway (matching house style) but don't run mypy against new files there.

### 7.3 The Unicode crash on Windows

`tests/conftest.py` prints `\u2713` and `\u26a0` in `pytest_sessionfinish` which crashes on Windows cp1252. All tests pass; only the teardown print fails. The fix is in the stash. Until applied, **expect exit code 1 on Windows local runs** even on green test runs. Linux/RHEL CI is unaffected.

### 7.4 The Architecture-tab infographic edits will go stale once #20 lands

Today's infographic shows the L2b gate with `designed`/`planned`/`not yet built` assumption pills. Per #20's acceptance criteria, those flip to `wired` when L2b actually lands. Do **not** preemptively flip them in #17, #18, or #19 — the pill convention is "current reality," and the gate is not wired until #20 closes.

## 8. Decision log — context the next session shouldn't re-litigate

| Decision | Why | Where it's documented |
|---|---|---|
| L2b is a **new gate**, not a repurposing of `L2_regeneration` | `L2_regeneration` is reserved for a future `valdo regenerate` CLI that re-derives from the mapping. L2b reconciles against SQL truth. Different problems. | Architecture tab card on infographic; #17 description |
| **Option 2 (idempotent SQL files)**, not Alembic, for DDL management | E2E harness must be re-runnable from zero; Alembic's stateful migration history is the wrong shape. | Earlier session conversation; will be in ADR 0008 context |
| **Pydantic for `src/`, `@dataclass` for `scripts/e2e_lib/`** | The directory's existing convention. Both are valid; consistency matters. | §7.1 above |
| **Option E (refactor `src/` to extract a primitive)**, not B (duplicate the reader) | Architecturally correct; single source of truth. Requires AGENTS.md hard rule 1 carve-out. | #21 description |
| **Trust git + idempotency**, not pre-flight DDL drift checks | SHA-comparison checks add complexity for a problem that idempotent bootstrap solves by construction. | Earlier session conversation |
| **Service account has DDL + temp-table privileges in SIT and AIT** | Confirmed by user during session 2. Enables Option 2 (idempotent bootstrap). | §3 of original handover; #18 description |
| **No Java files committed** | User direction in session 2. Read-only references kept untracked. | This document §4 |

## 9. Open questions for the user that may come up next session

These were not resolved during session 2 and the next session may need to ask:

1. **Trunk branch confirmation** — is `feature/issue-11-kill-file-search` still trunk when #21 starts? If something was merged, the rebase base changes.
2. **Stash disposition** — see §6. Specifically the `src/api/*` changes — are those user WIP from #11 or accidental?
3. **AGENTS.md carve-out wording** — #21 includes a proposed paragraph appending to hard rule 1. Is the wording acceptable, or should the carve-out be narrower / broader?
4. **Epic rollup** — five linked issues (#17–#21). Should they be grouped under an epic for visibility? Depends on tier.
5. **Labels, milestone, assignees** — all five issues created without these per session-2 instruction. When do they get applied?

## 10. Quick links

- [#17 — L2b engine + SQL bootstrap](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/17) — has the 13-correction comment, has the "Blocked on #21" comment
- [#18 — SHAW TRANERT SQL artifacts](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/18)
- [#19 — SHAW TRANERT reconciliation YAML + wiring](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/19)
- [#20 — tests + CI + infographic pill flip](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/20)
- [#21 — refactor: extract multi-record reader primitive](https://trgl.gitlab-dedicated.com/app/APPID-33091157/valdo/-/issues/21)
- `docs/handover/TRANERT_DB_TESTING_SESSION_SUMMARY.md` — original design (currently stashed)
- `docs/Valdo-Infographic.html` Architecture tab → "E2E harness gates" section — visual reference
- Java ground truth (untracked): `config/templates/TranertMapper (1).java`, `config/templates/DAOOperations (2).java`
