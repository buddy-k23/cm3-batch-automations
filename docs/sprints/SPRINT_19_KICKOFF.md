# Sprint 19 — Kickoff (Engine Breadth: JSON + XML)

**Sprint goal:** Implement the two designed-but-unbuilt formats — the JSON parser (ADR 0018) and the XML parser (ADR 0019) — so Valdo validates JSON/XML batch files, not just fixed-width/CSV/TSV/pipe. Final sprint of the four-sprint push: after this, every architecture-review issue (#408–#432) plus the JSON/XML feature backlog is closed.

**Duration:** 2 weeks · **Capacity:** ~5 points · **Demo:** internal — validate a JSON (NDJSON) and an XML file end-to-end.

**Context:** Sprints 16–18 closed correctness, multi-worker/quality, and structure. The remaining open issues are the two parser implementations whose designs already exist (ADR 0018 ~990 LOC, ADR 0019 ~1050 LOC). Each is an M, split here into 2 S stories (engine core; then template/sample/docs/MCP). The S14-3/S14-4 parser-dispatch fixes were prerequisites — done. The coverage gate is enforced at 80% — new parser code needs tests to keep it green.

---

## Stories

| ID | GitHub | Title | Size | Track |
|---|---|---|---|---|
| **S19-1** | [#395](https://github.com/buddy-k23/valdo/issues/395) | JSON parser engine core (parser + detector + converter + validators) | S | Engine breadth |
| **S19-2** | [#395](https://github.com/buddy-k23/valdo/issues/395) | JSON template + worked sample + docs + MCP auto-discovery | S | Engine breadth |
| **S19-3** | [#396](https://github.com/buddy-k23/valdo/issues/396) | XML parser engine core (hardened lxml + detector + converter + validator) | S | Engine breadth |
| **S19-4** | [#396](https://github.com/buddy-k23/valdo/issues/396) | XML template + worked sample + docs + MCP auto-discovery | S | Engine breadth |

**Total: ~5 pts.** #395 and #396 each land as two cohesive commits.

---

## Sequencing — sequential, commit-per-story

| Day | Story | Notes (follow the ADR precisely) |
|---|---|---|
| 1–4 | **S19-1** (#395) | ADR 0018: `src/parsers/json_parser.py` (NDJSON, one `json.loads`/line, `jsonpath-ng` resolution, `__source_row__`); `format_detector` `.ndjson`/`.jsonl` → JsonParser + `FileFormat.JSON` (plain `.json` array deferred with a clear message); `template_converter` `JSON Path` column → `from_json_template`; `validate_json_array_length` + `validate_nested_required` (absent-vs-null) + rule-engine dispatch. Tests for each. Confirm `jsonpath-ng` availability (transitive dep; add if needed). |
| 5 | **S19-2** (#395) | `templates/etl/json_single_record.yml` + worked sample dir (input.ndjson, mapping, expected_report, build_sample) + README; MCP `templates://etl/list` auto-discovery integration test; `docs/MCP_SERVER.md` + `USAGE_AND_OPERATIONS_GUIDE.md` "Validating JSON files". |
| 6–9 | **S19-3** (#396) | ADR 0019: `src/parsers/xml_parser.py` — streaming `lxml.etree.iterparse(tag=record_tag)`, **hardened** (`resolve_entities=False`, `no_network=True`, `load_dtd=False`, DOCTYPE rejected — XXE/billion-laughs), `__source_row__` from `sourceline`; `format_detector` `.xml`; `template_converter` `XML XPath` column (`@attr` vs element text); namespace-strip-by-default + opt-in; `validate_xml_array_length` + dispatch (reuse `nested_required`). Tests incl. a **security test** proving XXE/entity-expansion rejection. Add `lxml` to requirements (ADR 0019 accepted it). |
| 10 | **S19-4** (#396) | `templates/etl/xml_single_record.yml` + worked sample + README; MCP auto-discovery integration test; docs "Validating XML files". Final commit (kickoff); push; close #395/#396. |

---

## Definition of Done

- [ ] **S19-1:** NDJSON parse + JSONPath resolution (scalar/nested/array) + `__source_row__`; `.ndjson`/`.jsonl` routing; `JSON Path` template column round-trips (+ backward-compat); both validators incl. absent-vs-null; rule-engine dispatch — all tested
- [ ] **S19-2:** JSON template auto-discovered by `templates://etl/list` (zero MCP code change, proven by integration test); worked sample validates against its mapping; docs added
- [ ] **S19-3:** streaming hardened `lxml` parser (security test proves XXE/billion-laughs rejected); `.xml` routing; `XML XPath` attr-vs-text; namespace-strip default + opt-in; `validate_xml_array_length` + dispatch — all tested; `lxml` added to requirements
- [ ] **S19-4:** XML template auto-discovered + worked sample validates + docs added
- [ ] `pytest tests/unit/` **green (0 failed)** and **coverage ≥80%** (enforced); parameterized/safe parsing; no secrets committed
- [ ] Each story = one conventional commit `(S19-<m>, #<issue>)`; #395/#396 closed
- [ ] Kickoff lands as the **final commit**; push

---

## Risks

| Risk | Mitigation |
|---|---|
| Untrusted-XML security (XXE, billion-laughs, external entities) | The lxml parser MUST be hardened per ADR 0019 (`resolve_entities=False`, `no_network=True`, `load_dtd=False`, reject DOCTYPE); a dedicated security test feeds a malicious XXE + a billion-laughs payload and asserts rejection — this is the load-bearing test for #396. |
| `lxml` is a new runtime dep on RHEL | ADR 0019 accepted it; `lxml` ships manylinux wheels (no system libxml build needed) — add to requirements; CI installs it. |
| `jsonpath-ng` not actually available | ADR 0018 says it's a transitive dep; confirm via import — if absent, add it (pure-Python, no compile). |
| New parser code drops coverage below the enforced 80% | Each parser/validator/converter branch gets tests (the ADRs list the test files); run the gate before committing each story. |
| New formats inherit a chunked/non-chunked dispatch bug | The S14-3/S14-4 fixes + the S16-5 registry collapse already closed those; the JSON/XML validators dispatch through the unified `rule_engine`/registry. |
| Plain `.json` (top-level array) confusion | v1 is NDJSON-only per ADR 0018; the detector flags array-shaped JSON with a clear "convert to NDJSON first" message. |

## Out of scope
v2 per-array-element validation, JSONPath filter expressions, XSD-driven schemas, XML mixed content — all deferred per the ADRs. Plain-`.json` streaming (`ijson`) — future follow-up.

## Roles
Dev: `senior-fullstack-fintech-dev` per story. Architect designs already done (ADR 0018/0019). Owner/PM: you.
