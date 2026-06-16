# ADR 0019: XML parser design

- Status: Proposed
- Date: 2026-06-15
- Sprint: 8.5 (S8.5-1, [#378](https://github.com/buddy-k23/valdo/issues/378))
- Related: [ADR 0018](0018-json-parser-design.md),
  [ADR 0003](0003-fixed-width-v2-normalization.md),
  [ADR 0005](0005-multi-record-pipeline-dispatch.md),
  [ADR 0007](0007-template-converter-preserve-string-literals.md),
  [ADR 0014](0014-record-reader-strategy-seam.md),
  `src/parsers/base_parser.py`,
  `src/parsers/format_detector.py`,
  `src/parsers/pipe_delimited_parser.py`,
  `src/parsers/fixed_width_parser.py`,
  `src/pipeline/etl_config.py` (`SourceConfig`, `OutputFileConfig`,
  `InputFileConfig`),
  `src/config/template_converter.py`,
  `src/validators/field_validator.py`,
  `templates/etl/csv_file_comparison.yml`,
  `templates/etl/fixed_width_single_record.yml`,
  `src/mcp/resources/etl_templates.py` (S7-2 auto-discovery).

## Context

This ADR is the XML counterpart to [ADR 0018](0018-json-parser-design.md)
(JSON parser design, S8-3), and was flagged there as the explicit
follow-up: *"XML ADR (#378): explicitly model after this ADR — template
column rename to `XML XPath`, parser swap, same flatten-at-parse + same two
validators (`xml_array_length`, `nested_required`). Estimated half the
net-new code because the validator + template-converter work is already
done here."* This ADR holds to that scope and reuses ADR 0018's machinery
verbatim wherever XML genuinely parallels JSON, and calls out — loudly —
the three places XML does **not** parallel JSON: namespaces, the
attribute-vs-element-text distinction, and the untrusted-XML security
surface (XXE, billion-laughs, external-entity expansion) that JSON simply
does not have.

Valdo today validates and compares **flat, row-oriented files** —
fixed-width (`FixedWidthParser`), pipe / CSV / TSV (`PipeDelimitedParser`),
multi-record fixed-width via the umbrella YAML pattern (ADR 0005) — and,
after S8-3, **NDJSON via JSONPath flattening** (ADR 0018). Every parser
conforms to the same minimal contract in `src/parsers/base_parser.py`
(`parse() -> pd.DataFrame` and `validate_format() -> bool`), and every
mapping is a *flat list of fields* emitted by `TemplateConverter` from a
BA-authored CSV/Excel template. ADR 0018 established the pattern for adding
a nested format: a new locator column in the template (`JSON Path`), a new
parser that resolves that locator and **flattens to one column per field at
parse time**, and one or two new predicates in `field_validator.py`.

XML is the next nested batch-output format the platform asks us to validate.
Internal fintech feeds still emit XML heavily — ISO 20022 payment messages,
regulatory filings (FFIEC / call-report extracts), legacy core-banking
event envelopes, and partner B2B exchanges. The engine has **no XML
parser**, **no XML template under `templates/etl/`**, and — critically —
**no hardened ingestion path for untrusted XML**. Standing this up requires
answering the five questions from #378 before any code lands; this ADR makes
the calls so the implementation issue (a separate M/L follow-up — scope
sketched in §5) becomes a mechanical execution.

What XML breaks that FW / CSV / JSON don't:

- **Hierarchy (shared with JSON).** An XML record carries nested elements
  (`<customer><address><zip>…`) and repeated child elements
  (`<transaction>` repeated N times). This is the same locator problem JSON
  has, and the same flatten-at-parse answer applies — XPath substitutes for
  JSONPath. This is the part of the work ADR 0018 already paid for.
- **Attributes vs element text (new — no JSON analogue).** A value in XML
  can live in element *text* (`<id>42</id>`) **or** in an *attribute*
  (`<account id="42"/>`). JSON has exactly one place a scalar lives; XML has
  two, and the BA must be able to declare which. XPath already expresses
  this natively: `account/@id` (attribute) vs `account/id/text()` or just
  `account/id` (element text). The mapping locator must carry the
  distinction; the parser must honour it.
- **Namespaces (new — no JSON analogue).** XML elements can be
  namespace-qualified (`<ns:Document xmlns:ns="urn:iso:std:iso:20022…">`).
  ISO 20022 in particular is namespace-heavy. The same logical field is
  `pmt:Amt` in one file and `Amt` in another depending on whether the
  default namespace is declared. The mapping and parser must take a position
  on whether the BA writes namespace prefixes in their XPath or whether the
  parser strips namespaces so the BA never sees them.
- **Mixed content (new — no JSON analogue).** An element may interleave text
  and child elements (`<note>see <ref>R-12</ref> for detail</note>`). JSON
  cannot represent this. v1 deliberately does not support extracting from
  mixed-content elements as a single scalar — the BA selects either the
  direct text node or a specific child via XPath; whole-subtree text
  serialisation is out of scope (see §3).
- **The security surface (new — and the single biggest XML-only concern).**
  An NDJSON line is `json.loads()` of a self-contained string: no external
  references, no entity expansion, bounded memory. An XML document can
  declare a DTD, reference **external entities** (read local files / make
  network calls — XXE), and define **recursively-expanding entities**
  (the "billion laughs" / quadratic-blowup DoS). Fintech batch files arrive
  from upstream systems and partners and **must be treated as untrusted**.
  This forces a hard requirement on parser choice (§schema source / §record
  boundary are subordinate to it): whatever parses the XML must have entity
  resolution, DTD loading, and network access **disabled by construction**.

## Decision

### 1. Schema source — **Picked: A (XPath expressions in a CSV mapping template)**

Directly parallel to ADR 0018 §1. The mapping template stays a **CSV/Excel
workbook**, identical in shape to the FW/CSV/JSON `TemplateConverter` input,
with **one new column: `XML XPath`**. The BA writes
`/Document/CstmrCdtTrf/PmtInf/Amt`, `account/@id`,
`transactions/transaction` next to each field row. `TemplateConverter`
(already the single ingestion point — `src/config/template_converter.py`,
ADR 0007) gains a `from_xml_template()` branch that recognises an `XML XPath`
column and emits a mapping JSON whose per-field entries carry `xml_xpath`
(parallel to ADR 0018's `json_path`).

**Why XPath-in-CSV and not XSD-driven (Rejected — B):** an XSD is a *structural*
contract (element ordering, types, cardinality) authored by the source-system
owner, not the validation BA. Adopting XSD as the mapping source would (a) fork
`TemplateConverter` into a second ingestion shape — exactly the two-parallel-
pipelines cost ADR 0018 §1 rejected for JSON Schema — (b) couple Valdo's
mapping to whatever XSD dialect/version the upstream emits, and (c) still not
encode the validation BA's intent (required/optional *for our gate*,
`valid_values`, business rules), which is the whole point of the workbook. An
XSD validates "is this well-formed per the producer's schema"; Valdo validates
"does this satisfy *our* business gate" — a different, narrower contract that
the BA owns. XSD-conformance can ship later as a *separate* pre-gate
(`valdo validate-xsd`) that wraps `lxml`'s built-in `XMLSchema`; it is not the
mapping source.

**Rejected — C (sample-driven inference):** identical reasoning to ADR 0018
§1's rejection of inference for JSON. BAs author from a spec workbook, not from
samples. Inference can *draft* an `XML XPath` column (an XML variant of the
existing `valdo infer-mapping`) but cannot encode required/optional,
`valid_values`, or business intent. It is a starting point, not the contract.

The BA workflow is unchanged in shape: open the existing onboarding workbook,
fill in the `XML XPath` column for each field, upload through the same
`/api/v2/mappings/upload` endpoint. Zero new tooling — exactly as JSON.

### 2. Record boundary — **Picked: streaming via `lxml.etree.iterparse` on a repeated record element**

The parser treats the file as a sequence of **repeated record elements** —
the BA names the record element (default `record`, overridable in the template
header, e.g. ISO 20022's `CdtTrfTxInf`). The parser uses
**`lxml.etree.iterparse(file, tag=record_tag)`** to stream one record element
at a time, runs the per-field XPath selectors against that element subtree,
emits one flat DataFrame row, then **clears the element** (`elem.clear()` plus
trimming preceding siblings) to keep memory O(1) per record.

This is the XML analogue of ADR 0018's NDJSON-per-line decision — "one record
unit streamed at a time, O(1) memory, line/record-addressable error reporting"
— adapted to XML's tree shape.

Defence against alternatives:

- **Parse the whole document into a DOM (`etree.parse` / `parse + findall`):**
  forces the entire file into memory, incompatible with the chunked validator
  pattern (`src/parsers/chunked_validator.py`) already used for large FW/CSV
  inputs, and is the exact memory cliff ADR 0018 rejected the JSON-array shape
  for. `iterparse` is the streaming equivalent of NDJSON.
- **stdlib `xml.etree.ElementTree.iterparse`:** would avoid the `lxml`
  dependency, but see §security below — `ElementTree` cannot be hardened
  against XXE/entity-expansion to the standard required for untrusted fintech
  files, and `iterparse` there does not give us full XPath. **Rejected on
  security grounds**, which is the load-bearing reason for the `lxml` call.
- **Element-per-line / NDXML:** there is no widely-emitted line-delimited XML
  convention to mirror NDJSON. XML's natural record unit is the repeated
  element, so we anchor on that.

Record addressability: `iterparse` exposes `elem.sourceline` (the source file
line where the record element opened). The parser injects this as
`__source_row__` (1-indexed), preserving the exact convention
`FixedWidthParser` and `PipeDelimitedParser` use (`df.insert(0, '__source_row__',
…)`). Error reports stay source-addressable — the BA's mental model is
unchanged from FW/CSV/JSON.

### 3. Namespace handling — **Picked: strip namespaces by default; honour them only when the BA opts in**

By default the parser **strips namespaces** so the BA writes plain element
names in their XPath (`PmtInf/Amt`, never `{urn:iso…}Amt` or `pmt:Amt`). The
parser normalises each element's tag to its local name during the `iterparse`
walk (drop the `{namespace}` Clark-notation prefix), so XPath selectors match
on local name alone.

Opt-in: a template-header flag (`namespace_aware: true` plus a
`namespaces:` prefix→URI map) switches the parser into namespace-honouring
mode, where the BA's XPath may carry registered prefixes (`pmt:Amt`) resolved
against the declared map. This is for the rare case where two same-local-name
elements in different namespaces must be disambiguated in one document.

**Why strip-by-default:**

- **BA ergonomics.** The validation BA thinks in business field names, not URI
  schemes. Forcing `{urn:iso:std:iso:20022:tech:xsd:pain.001.001.09}Amt` into
  the workbook is hostile and brittle — the URI changes with every ISO 20022
  minor version, silently breaking every mapping. Local-name matching survives
  schema-version bumps that don't rename elements.
- **Parallels JSON.** JSON has no namespace concept, so the BA never thinks
  about one. Strip-by-default keeps the XML BA in the same headspace as the
  JSON BA — the locator is "the path of element names," nothing more.
- **Escape hatch retained.** The genuine disambiguation case (two `Amt`
  elements in different namespaces) is handled by the opt-in flag, so we don't
  paint ourselves into a corner — we just don't tax the 95% case for the 5%.

**Rejected — namespace-aware always:** correct and rigorous, but pushes URI
bookkeeping onto every BA for every field, and couples mappings to schema-
version URIs. The cost lands on the common case to serve the rare one. Rejected
as the default; retained as the opt-in.

### 4. Attribute vs element-text mapping — **Picked: the XPath itself carries the distinction (`@attr` vs element/text)**

The BA declares value location **in the XPath**, using standard XPath syntax —
no extra column, no extra flag:

- **Element text (default):** `customer/id` or, explicitly, `customer/id/text()`
  — the parser takes the element's text content.
- **Attribute:** `account/@id` — the leading `@` on the final step tells the
  parser to read the named attribute rather than element text. The parser
  detects the trailing `@name` step and resolves `elem.get("name")` against the
  matched element.

This is the natural fit because XPath is *designed* to address both nodes and
attributes uniformly; we let the locator language do the work rather than
inventing a Valdo-specific "is this an attribute?" column. It also keeps the
template shape identical to JSON's (one locator column, no XML-specific second
column), which is the ADR 0018 parallel.

**Rejected — a separate `Source` column (`element` | `attribute`):** redundant
with information the XPath already encodes, and a second source of truth the BA
can get out of sync with the path. Keeping it in the XPath means one locator,
one mental model.

**Mixed content:** explicitly **out of scope for v1** (see Context). The BA
selects either a direct text node or a specific child element; the parser does
**not** serialise an element's full mixed subtree into one scalar. If a BA's
XPath lands on a mixed-content element, v1 takes the element's *direct* text
(`elem.text`, the text before the first child) and the template README
documents this; whole-subtree extraction is a deferred follow-up.

### 5. Recommendation — one concrete design + `lxml` vs `ElementTree` + scope

**Parser library — Picked: `lxml`, with a hardened, security-locked parser
configuration. Rejected: stdlib `xml.etree.ElementTree`.**

This is the load-bearing decision of the ADR, and unlike ADR 0018's
"no new dependency" NDJSON call, here we **accept a new dependency
(`lxml`)** because the security requirement forces it:

- **`lxml` is faster** (libxml2 C core) and gives us first-class streaming
  `iterparse` *with* full XPath (§2, §4) and built-in XSD validation for the
  future `validate-xsd` pre-gate (§1). stdlib `ElementTree` has only a
  limited XPath subset and a weaker `iterparse`.
- **Security is the deciding factor.** Fintech batch files are **untrusted**.
  The three XML-specific attacks all live in entity/DTD processing:
  - **XXE (external entity injection):** a malicious file declares
    `<!ENTITY xxe SYSTEM "file:///etc/passwd">` (or an `http://` URL) and
    exfiltrates local files / triggers SSRF when the entity is expanded.
  - **Billion laughs / quadratic entity expansion:** nested entity
    definitions expand to gigabytes, exhausting memory (DoS).
  - **External DTD retrieval:** the parser fetches a remote DTD, leaking that
    the file was processed and enabling SSRF.
  - stdlib `ElementTree` has **known, documented security caveats** for
    untrusted XML (the Python docs themselves warn it is not secure against
    maliciously constructed data, and the historic mitigation library
    `defusedxml` exists precisely because the stdlib defaults are unsafe).
    `lxml` lets us construct a parser with **all of these disabled by
    construction**, which is auditable and explicit:

    ```python
    # src/parsers/xml_parser.py — the security-locked parser, used everywhere
    _SAFE_PARSER = etree.XMLParser(
        resolve_entities=False,   # do not expand entities (kills XXE + billion-laughs)
        no_network=True,          # never fetch external DTDs/entities (kills SSRF)
        dtd_validation=False,     # do not load/validate against any DTD
        load_dtd=False,           # do not load the internal/external DTD subset
        huge_tree=False,          # keep libxml2's built-in size/depth guards on
    )
    ```

    The parser **rejects** any document containing a `DOCTYPE`/DTD with a clear
    error (rather than silently ignoring entities), so a file that *tries* to
    use entities fails loudly instead of validating against a partially-stripped
    document. This is a strictly stronger posture than `defusedxml`-on-stdlib and
    keeps a single hardened code path.

So: **`lxml` adds a dependency, but it is the only option that gives us a
streaming, XPath-capable, *and* provably-hardened parser in one library.**
The dependency cost is accepted explicitly and called out in §Negative.

**Concrete v1 design (one design, no alternatives left open):**

1. `XmlParser(BaseParser)` streams record elements with
   `lxml.etree.iterparse(tag=record_tag)` using `_SAFE_PARSER`, resolves each
   field's `xml_xpath` against the record subtree (local-name matching by
   default, attribute via trailing `@name`), flattens to one DataFrame column
   per field, injects `__source_row__` from `elem.sourceline`, and clears each
   element after processing. Output DataFrame shape is **identical** to
   FW/CSV/JSON: one column per mapped field plus `__source_row__`.
2. Repeated child elements collapse to a **count** column
   (`transactions/transaction` → `transactions_count: int`), exactly as ADR
   0018 collapses `transactions[*]` to a count. Per-element rules across
   repeated children are the v2 follow-up (ADR 0005 umbrella shape), identical
   to JSON's deferral.
3. **Validators reuse ADR 0018's surface entirely.** Every existing predicate
   in `field_validator.py` works as-is on the flattened scalar columns. Two new
   predicates — and these are *literally the JSON ones generalised*, not new
   work:
   - `validate_xml_array_length(df, field, min_len, max_len=None)` — the XML
     twin of `validate_json_array_length`; flags records whose repeated-child
     count is outside `[min_len, max_len]`. ("Every payment file must carry at
     least one transaction"; "no more than 500.")
   - `validate_nested_required(df, field)` — **reused unchanged from ADR
     0018.** XML has the same three-state problem JSON does: element/attribute
     present-with-value, present-but-empty, absent. The parser writes the same
     sentinels (`pd.NA` absent, `None`/`""` present-empty) so the *same*
     predicate distinguishes them. No XML-specific version needed.

   Because `nested_required` already exists from ADR 0018, the XML net-new
   validator work is **one method** (`validate_xml_array_length`) plus one
   rule-engine dispatch entry — this is the "half the net-new code" ADR 0018
   §Follow-ups predicted.

**Implementation scope** (LOC grounded in the actual parsers read for this ADR:
`FixedWidthParser` ≈ 102 LOC, `PipeDelimitedParser` ≈ 118 LOC,
`format_detector.py` ≈ 237 LOC, `template_converter.py` ≈ 361 LOC):

| File | Action | Estimated LOC |
|---|---|---|
| `src/parsers/xml_parser.py` | **New** — `XmlParser(BaseParser)` with hardened `_SAFE_PARSER`, `iterparse` streaming, namespace-strip-by-default (+ opt-in namespace map), XPath resolution incl. `@attr` handling, repeated-child count columns, `__source_row__` from `sourceline`, per-record `elem.clear()`. Heavier than `JsonParser` (~180) by the namespace-strip walk + the security config + DOCTYPE rejection. | **~220** |
| `src/parsers/format_detector.py` | Add `.xml` → `XmlParser` extension routing; add `_score_xml` (first non-blank line starts with `<?xml` or a `<` element); extend `FileFormat` enum with `XML`. Note: `.xml` is unambiguous by extension, so this is extension-first like S8-2's `.csv`/`.tsv`/`.psv`. | **~25** |
| `src/parsers/__init__.py` | Export `XmlParser`. | **~3** |
| `src/config/template_converter.py` | Add `'XML XPath'` to `OPTIONAL_COLUMNS` + the snake_case `col_map`; new `from_xml_template()` entrypoint; `_detect_format` recognises an `XML XPath` column → `'xml'`; `_convert_row_to_field` emits `xml_xpath` when the column is populated; header passthrough for `record_tag` / `namespace_aware` / `namespaces`. Mirrors ADR 0018's `from_json_template()` (~60). | **~65** |
| `src/validators/field_validator.py` | Add `validate_xml_array_length` (twin of `validate_json_array_length`). `validate_nested_required` is **reused from ADR 0018 — zero new LOC**. | **~25** |
| `src/validators/rule_engine.py` | Dispatch the one new operator (`xml_array_length`). `nested_required` operator already dispatched (ADR 0018). | **~10** |
| `src/pipeline/etl_config.py` | One-line docstring note in `OutputFileConfig.is_multi_record` that XML mappings are flat (single-record) in v1 and use the umbrella shape only when expanded in v2. No schema break. | **~5** |
| `templates/etl/xml_single_record.yml` | **New** BA-facing template mirroring `json_single_record.yml`. Header carries `record_tag`, optional `namespace_aware` / `namespaces`. Auto-discovered by `templates://etl/list` (S7-2) with zero MCP code changes. | **~75** |
| `templates/etl/xml_single_record_sample/` | **New** worked sample: `input.xml` (10 `<record>` elements incl. one attribute-valued field + one repeated child), `mapping.json` (hand-curated, `xml_xpath` locators), `expected_report.json`, `build_sample.py`. Mirrors the JSON/FW sample directories. | **~160** (mostly fixture data) |
| `templates/etl/xml_single_record_README.md` | **New** — one-page README in the established shape; documents namespace-strip default, `@attr` syntax, DOCTYPE rejection, mixed-content limitation. | **~55** |
| `tests/unit/test_xml_parser.py` | **New** — happy-path `iterparse`, XPath resolution (element text / `@attr` / nested), namespace strip + opt-in honour, repeated-child count, `__source_row__` from `sourceline`, **security: XXE blocked, billion-laughs blocked, external-DTD blocked, DOCTYPE rejected**, malformed-XML handling. | **~170** |
| `tests/unit/test_template_converter_xml.py` | **New** — round-trip a CSV template with an `XML XPath` column; assert emitted mapping carries `xml_xpath`; assert backward compat (templates without the column still emit FW/JSON mappings). | **~75** |
| `tests/unit/test_field_validator_xml.py` | **New** — coverage for `validate_xml_array_length`; assert `validate_nested_required` works on XML-sourced absent-vs-empty columns (reuse, not re-implement). | **~50** |
| `tests/integration/test_mcp_template_resources.py` | Extend — assert `xml_single_record` appears in `templates://etl/list` (zero-code MCP integration is the AC). | **~15** |
| `docs/MCP_SERVER.md` | Add XML template to the discovered-templates table. | **~10** |
| `docs/USAGE_AND_OPERATIONS_GUIDE.md` | Add "Validating XML files" section mirroring the JSON section; call out the untrusted-XML security posture and DOCTYPE rejection. | **~85** |
| `requirements*.txt` / `pyproject.toml` | Add `lxml` dependency (security-justified above). | **~2** |
| **Total** | | **~1050 LOC** |

**Estimated effort:** ~2.5 dev-days (one M-sized issue) — close to *half* the
net-new logic of ADR 0018's JSON work despite a slightly higher raw LOC total,
because the LOC here is concentrated in **fixture data, security tests, and
docs** rather than new validation logic. The reused pieces (`nested_required`,
the whole `field_validator` surface, the `TemplateConverter`/locator-column
pattern, the flatten-at-parse architecture, the MCP auto-discovery) are exactly
what ADR 0018 paid for. Breakdown:

1. Parser + hardened config + format detector + security tests (1.25 days — the
   security tests are the bulk of the *new* effort vs JSON).
2. `TemplateConverter` extension + tests (0.5 day).
3. One new validator + rule-engine dispatch + tests (0.25 day — half of ADR
   0018's because `nested_required` is reused).
4. Template + sample + README + docs (0.5 day).

**`SourceConfig` / `TemplateConverter` plumbing that needs extension:**

- `TemplateConverter.OPTIONAL_COLUMNS` (`src/config/template_converter.py`):
  add `"XML XPath"` to the recognised set, plus the snake_case `col_map`
  entry. The `dtype=str` contract (ADR 0007) carries through unchanged.
- `TemplateConverter._detect_format`: add the `XML XPath`-column branch →
  `'xml'`, parallel to the JSON-Path branch from ADR 0018.
- `OutputFileConfig.is_multi_record` (`src/pipeline/etl_config.py`): no code
  change for v1 — XML mappings are flat. Docstring grows one line.
- `FormatDetector` (`src/parsers/format_detector.py`): `.xml` is extension-
  authoritative (S8-2 pattern); add it to the routing alongside a light
  `_score_xml` content check for un-extensioned XML.
- S7-2 auto-discovery (`src/mcp/resources/etl_templates.py`): zero code
  change. Dropping `templates/etl/xml_single_record.yml` into the directory is
  enough — the resource walks the directory at every read.

## Consequences

### Positive

- The BA workflow is unchanged in shape: same workbook, one new `XML XPath`
  column, same upload endpoint, same `TemplateConverter` ingestion — exactly
  the JSON adoption story. Adoption cost is ~zero.
- Every existing per-field validator works as-is on flattened XML columns, and
  `validate_nested_required` is reused **verbatim** from ADR 0018. The only new
  predicate is `validate_xml_array_length`. This is the "half the net-new code"
  ADR 0018 predicted.
- `lxml.iterparse` streaming means O(1) memory per record and `__source_row__`
  line-addressability end to end, the XML analogue of NDJSON's per-line streaming.
- **Untrusted XML is safe by construction.** The single hardened `_SAFE_PARSER`
  disables entity resolution, DTD loading, and network access, and the parser
  rejects DOCTYPE outright — a stronger, auditable posture than stdlib +
  `defusedxml`. This closes the XXE / billion-laughs / external-entity surface
  that is XML-specific and that JSON never had.
- The umbrella-YAML pattern (ADR 0005) and the record-reader strategy seam
  (ADR 0014) are **not** touched by v1 — `XmlParser` is a flat single-record
  parser. v2 per-repeated-child rules slot in as a new umbrella variant, not a
  rewrite, identical to the JSON v2 path.
- The MCP `templates://etl/*` surface (S7-2) picks the new template up
  automatically. No agent-facing code changes.

### Negative / risks

- **New dependency: `lxml`.** Unlike JSON (which added zero dependencies),
  XML requires `lxml` (a compiled libxml2 binding). This is the price of a
  streaming-XPath-and-secure parser in one library. Risk: build/packaging
  weight (wheels exist for all target platforms, so low) and a transitive C
  dependency to track for CVEs. Accepted deliberately — the security
  requirement makes stdlib `ElementTree` non-viable, and `lxml` wheels are
  ubiquitous. Mitigation: pin `lxml` and track it in the dependency-CVE scan.
- **Namespace-strip-by-default can collide.** Two same-local-name elements in
  different namespaces in the *same* document would both match a stripped
  XPath. Mitigation: the opt-in `namespace_aware: true` mode exists precisely
  for this, and the parser warns when local-name matching is ambiguous (more
  than one element matches a leaf XPath that should be scalar).
- **Per-repeated-child validation is deferred to v2** — identical to JSON's
  deferral. A rule like "every `<transaction>` must have a non-empty `<amount>`"
  cannot be expressed in v1, only "every record must carry at least one
  `<transaction>`." Same trade-off, same v2 (ADR 0005 umbrella) path.
- **Mixed content is not extractable as a single scalar in v1.** A BA XPath
  landing on a mixed-content element gets the element's *direct* text only.
  Documented in the template README. Whole-subtree serialisation is a follow-up.
- **DOCTYPE rejection is strict.** Legitimate-but-rare XML that genuinely needs
  internal entity definitions will be rejected with a clear error rather than
  parsed. This is intentional (fail-closed on the security surface). If a real
  internal use-case surfaces a trusted, entity-using feed, an explicit
  `allow_internal_entities` opt-in (still no external/network) can be added as a
  follow-up — but it is not in v1.

### Follow-ups

- **Implementation issue (M/L):** scoped above (~1050 LOC, ~2.5 dev-days).
  Title: `feat(parsers): lxml XML parser + XML XPath template column`.
  Acceptance: hardened streaming parser (XXE/billion-laughs/external-DTD/
  DOCTYPE all proven blocked by test) + converter extension + one new validator
  (`xml_array_length`) + reused `nested_required` + one template + full sample +
  docs + MCP auto-discovery pickup proven by integration test. **This ADR does
  not file that issue — it is filed separately after ADR review by the issue
  owner; do not include in this sprint.**
- **v2 repeated-child fan-out:** size as L; reuses the ADR 0005 umbrella pattern
  with one detail-record type per repeated-element XPath. Shared design with the
  JSON v2 array fan-out — they should land together.
- **`valdo validate-xsd` pre-gate:** wrap `lxml`'s `etree.XMLSchema` as a
  separate, optional structural pre-gate (well-formedness + producer-XSD
  conformance) that runs *before* the business-rule gate. Distinct from the
  mapping source (§1 rejected XSD *as the mapping*); this is XSD *as a gate*.
  Defer until a feed requires it.
- **Internal-entity opt-in (`allow_internal_entities`):** add only if a trusted
  feed genuinely requires internal entity definitions. Still no external/network
  resolution. Defer until a concrete use-case forces it.
