# L2b Session 7 — Resume Prompt

Use this as the opening message to the session-7 agent. It encapsulates
everything they need to pick up where session 6 left off, plus the
guardrails specific to the open question they need to resolve.

---

## Opening message (paste verbatim to the new agent)

I'm resuming the L2b SQL-Truth gate work on issue #18 (SHAW TRANERT SQL
artifacts), continuing from session 6. The branch is local-only,
unpushed, with 8 commits landed and one in-progress fix sitting
uncommitted in the working tree. The bootstrap has been run live
against SIT three times and surfaced a real cross-schema privilege
issue that is the open question for this session.

### Before anything else

Read these in order:

1. **`docs/handover/L2B_SESSION_6_HANDOVER.md` in full** — the
   predecessor handover. Captures the eight session-6 decisions, the
   eight commits + one in-progress fix, three live SIT bootstrap
   attempts and what each one proved/disproved, the schema topology
   (`app_int` vs `uzapp_ad0`), the open question on cross-schema view
   privileges, and the recommended resume sequence.

2. **`docs/handover/L2B_SESSION_5_HANDOVER.md`** for predecessor
   context (the engine surface area, the 13-correction list reference
   to #17, the L2b reconciliation YAML shape for #19).

3. **`docs/handover/L2B_SESSION_2_HANDOVER.md`** for the original
   five-issue dependency chain (#17 → #18 → #19 → #20 with #21 as
   prerequisite) and the stash contents in §6.

4. **AGENTS.md** for the project conventions. Pay special attention
   to hard rule #1 (no `src/` modifications for harness work — the
   carve-out does not apply to #18) and hard rule #3 (no secrets in
   code/config, no env-var reads outside `secret_resolver.py`).

5. **Issue #18 end-to-end** for the canonical scope. The 13-correction
   list comment on #17 is also authoritative for what each record
   type's expected view must produce.

6. **The Java ground truth** (gitignored, in `_ref/` and
   `config/templates/`): `TranertMapper (1).java`,
   `DAOOperations (2).java`,
   `shaw-interfaces-common.properties`,
   `shaw-sql-statements.properties`. Read via `run_command` + Python
   (the `read_file` tool refuses gitignored paths).

7. **The three SIT audit logs** at
   `reports/shaw_tranert_smoke/20260529T*.jsonl`. They are the canonical
   record of what executed against Oracle and what failed.

### Verify state before doing anything

```powershell
git log -1 --oneline
# Expect: fa5a60a fix(sql): schema-qualify SHAW TRANERT harness objects to app_int (Policy A)

git status --short
# Expect exactly four M files (the in-progress fix):
#   M config/e2e/sources/SHAW/sql/tranert/00_bootstrap/020_expected_views.sql
#   M config/e2e/sources/SHAW/sql/tranert/20_query/cost2.sql
#   M scripts/e2e_lib/shaw_tranert_smoke.py
#   M tests/unit/test_e2e_shaw_tranert_smoke.py
# Plus three untracked leavings (Java + Excel-lock):
#   ?? "config/templates/DAOOperations (2).java"
#   ?? "config/templates/TranertMapper (1).java"
#   ?? mappings/excel/~$TRANERT_SHAW_Mappings.xlsx

git stash list
# Expect: stash@{0} unchanged from sessions 2-6

python -m pytest tests/unit/test_e2e_shaw_tranert_smoke.py tests/unit/test_e2e_extract_tranert_properties.py tests/unit/test_e2e_sql_bootstrap.py tests/unit/test_e2e_reconciliation_spec.py tests/unit/test_e2e_multi_record_file_parser.py tests/unit/test_e2e_db_truth_comparator.py tests/unit/test_multi_record_reader.py tests/unit/test_multi_record_validator.py --no-cov -q --tb=line
# Expect: 272 passed (the four in-progress edits + their new tests)
```

If any of these are off, **STOP** and report what you found. Do not
proceed with code changes until the state is confirmed clean.

### The open question for this session

The third SIT bootstrap attempt (audit:
`reports/shaw_tranert_smoke/20260529T214737Z.jsonl`) failed with
**ORA-01031: insufficient privileges** when Oracle compiled
`app_int.V_SHAW_TRANERT_CONTACTS_MERGED`. The view's APPS leg
references `uzapp_ad0`-owned tables (`coll_contact`,
`CONTACT_ACCOUNT`, `LOAN_CUST_INFO`); creating a view in `app_int`
that reads from `uzapp_ad0.*` requires `app_int` to have been granted
`SELECT WITH GRANT OPTION` on those tables, which it has not.

**The user proposed connecting as `app_int` directly** (they have the
password) as a way around this. The session-7 plan in the handover
recommends this as the first move. Three options the agent should
weigh, in order of preference:

| # | Approach | Pros | Cons |
|---|---|---|---|
| I | Connect as `app_int` | Self-contained; no DBA ticket; matches the Java's known-working setup | Larger blast radius than `uzapp_ad0` — bounded by the whitelist classifier but defence-in-depth weakens |
| II | DBA grant `SELECT WITH GRANT OPTION` from `uzapp_ad0` to `app_int` on 8 source tables | No code change | Operational dependency on DBA |
| III | Move harness views to `uzapp_ad0` schema | Self-contained; classifier enforces it | Asymmetric (tables in one schema, views in another) |

### Resume sequence

Per session-6 handover §8:

1. **Choose the app_int connection mechanism.** Ask me to confirm
   whether to:
   - **(a)** Overwrite the existing `ORACLE_USER_SIT` /
     `ORACLE_PASSWORD_SIT` in `.env` with the `app_int` values, OR
   - **(b)** Add a `--as-app_int` CLI flag to the smoke runner that
     reads from `ORACLE_USER_SIT_APP_INT` /
     `ORACLE_PASSWORD_SIT_APP_INT`, audit-logged. Recommended by
     handover §7.1.
2. **Run `bootstrap` against SIT as app_int.** Expect 7 tables + 168
   INSERTs + 10 views to succeed. If any view still fails, treat the
   audit log as the diagnostic and surface the root cause for review.
3. **Run `validate` against SIT as app_int.** Confirms each view body
   parses (`SELECT * WHERE ROWNUM <= 1` against each).
4. **Commit the in-progress fix + app_int connection mechanism** as a
   single `fix(sql)` commit. Cite the successful SIT validation in the
   commit message (specific audit-log timestamps).
5. **Author commit 5b — the per-record-type EXPECTED_* views.** Six
   expected views (`EXPECTED_BATCH_HEADER_VIEW`, `EXPECTED_32000_VIEW`,
   `EXPECTED_32005_VIEW`, `EXPECTED_32010_VIEW`, `EXPECTED_32025_VIEW`,
   `EXPECTED_32040_VIEW`, `EXPECTED_32075_VIEW` — 7 actually, the
   header view + 6 detail views). Each reproduces the Java's per-row
   business logic per the 13-correction list on #17. **User wanted
   this split into 5b1 (simple: 32000, 32040, 32075, header) and 5b2
   (complex: 32005, 32010, 32025).** Confirm the split still stands.
6. **Author commit 6 — parse-smoke test.** A new
   `tests/unit/test_e2e_shaw_tranert_sql_smoke.py` that walks every
   `.sql` file under `config/e2e/sources/SHAW/sql/tranert/` and
   verifies the bootstrap splitter parses it cleanly + (when SIT is
   reachable) every view body actually compiles. The session-6
   smoke runner's `validate` subcommand is the existing implementation
   of the live-SIT half; the offline half is new.

### Post-handover state deltas to verify

The session-6 handover may itself reference state that has drifted if
I (the user) made changes between then and now. Cross-check:

1. The four in-progress M files in the working tree (per the status
   check above). If any of these are unmodified, I likely committed
   the fix between session-6 handover and this session. Reconcile
   before proceeding.
2. The audit logs under `reports/shaw_tranert_smoke/`. If more than
   the three timestamps mentioned in the session-6 handover are
   present, I ran additional bootstrap attempts; check the latest one
   for what now works/fails.

### Stop and ask before

- Touching `stash@{0}` (carried forward unchanged from session 2).
- Modifying anything under `src/` (AGENTS.md hard rule #1).
- Creating any lookup CSV whose contents you would have to invent.
- Force-pushing anything.
- Creating any new issues or MRs.
- Deviating from #18's literal scope without confirmation.
- Choosing between Options I/II/III for the cross-schema fix.
- Running anything destructive against SIT (DROP, DELETE, UPDATE).
  Note the whitelist classifier refuses these by design; you'd have
  to bypass it.

### Windows shell gotchas carried forward (proven in sessions 3-6)

- Multi-paragraph commit messages: write to `_commit_msg_tmp.txt`
  via `create_file_with_contents`, then `git commit -F
  _commit_msg_tmp.txt`, then delete. `.git/` is excluded by Duo's
  context filter; `$env:TEMP\…` absolute paths don't resolve
  correctly through the editor tool.
- Don't put `|`, `>`, or `<` in commit titles passed via `-m`.
- Chained shell commands with `&` in cmd suppress intermediate exit
  codes. Run one tool at a time.
- `findstr` filters expand glob metacharacters unless quoted.
- `yaml.safe_dump`, not hand-rolled YAML emitters — Windows paths
  contain `\U` and `\T` sequences that YAML's double-quoted scalar
  grammar misinterprets.
- `UniversalMappingParser`, not `MappingDocument` — for any code that
  needs per-record-type fixed-width slicing.
- For mypy on new files in `scripts/e2e_lib/`: use `python -m mypy
  --follow-imports=silent --explicit-package-bases <file>` to strip
  out transitive `src/` typing-debt noise.
- Black on the modified files BEFORE committing — black inserts
  CRLF in re-formatted files on Windows, so `git status` warnings
  about LF → CRLF are routine. Live with them; the `.gitattributes`
  normalisation handles correctness.
- `read_file` tool **refuses gitignored paths**. To read anything in
  `_ref/` (the four property files + Excel) or other gitignored
  locations, use `run_command` with Python's `pathlib.Path.read_text`
  + similar.

### Pre-existing trunk test failures (30 in 7 files)

Documented in session-4 §4.5 and confirmed unchanged through session 6.
None touch any L2b module. Use the file-move-out-and-back regression-
check trick from session 5 if you need to confirm your changes don't
add new failures.

### Things I am NOT asking you to do this session

- Rewrite anything under `src/`.
- File new issues or MRs without my confirmation.
- Push the branch to origin.
- Address the long-term least-privilege concern (dedicated
  `valdo_harness_sit` user). Session-7 uses `app_int` if that's the
  decision; the long-term hardening is session 8+.
- Drop or alter the partial state on SIT. The 7 `app_int.LKP_VALDO_SHAW_*`
  tables and 6 `app_int.V_SHAW_TRANERT_*` views that survived previous
  bootstrap attempts are harmless — re-running `bootstrap` is
  idempotent and will fix any partial state.

### Reading list summary (in priority order)

1. `docs/handover/L2B_SESSION_6_HANDOVER.md` — most important.
2. `docs/handover/L2B_SESSION_5_HANDOVER.md` — predecessor.
3. `docs/handover/L2B_SESSION_2_HANDOVER.md` — original L2b doc.
4. `AGENTS.md` — hard rules.
5. Issue #18 description + the 13-correction list on issue #17.
6. `scripts/e2e_lib/shaw_tranert_smoke.py` — surface area you'll touch.
7. `config/e2e/sources/SHAW/sql/tranert/00_bootstrap/020_expected_views.sql`
   — the views file you'll extend (commit 5b).
8. `_ref/shaw-sql-statements.properties` — source of truth for view
   bodies you'll author. Read via Python (gitignored).

### Acknowledge when you've read the above

Reply with a one-paragraph summary of your understanding before doing
anything — especially:

- What state the branch is in.
- What the open question is.
- Which option (I/II/III) you recommend and why.
- What the first three concrete actions of this session will be.

Then wait for my confirmation before running any commands.
