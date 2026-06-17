# XML single-record validation — worked example

Validates an **XML** file of repeated record elements against a mapping whose
fields are located by **XPath**, per [ADR 0019](../../docs/adr/0019-xml-parser-design.md).
The XML counterpart to `json_single_record` — same engine, same report shape,
fields located by an XPath (`customer/@id`) relative to each record element.

## When to use

- Your file is XML with a **repeated record element** (e.g. `<record>…</record>`,
  or an ISO 20022 `<CdtTrfTxInf>`). Set the element name via `source.record_tag`
  in the mapping (default `record`).
- Each record has the same shape (one record type per file).

## Security

The parser is **hardened** (ADR 0019): it streams with `lxml.iterparse` and sets
`resolve_entities=False`, `no_network=True`, `load_dtd=False`, and **rejects any
`<!DOCTYPE>`** — so XXE, billion-laughs entity expansion, and external-entity
fetches are refused, not processed. Don't put a DOCTYPE in your file.

## Files in this sample

| File | What it is |
|---|---|
| `input.xml` | 10 `<record>` elements, one per line (regenerate with `python3 build_sample.py`). Records 6/8/9/10 carry seeded defects. |
| `mapping.json` | The contract — each field carries an `xml_xpath`. `CUSTOMER_ID` reads the `customer/@id` **attribute**; `TXN_COUNT` maps the repeated child `transactions/transaction` → an integer count column `TXN_COUNT_count`. `source.record_tag` names the record element. |
| `rules.json` | `nested_required` (CUSTOMER_ID present), `valid_values` (STATUS), `numeric` (AGE), `xml_array_length` (≥1 transaction). |
| `expected_report.json` | The **real** output of running the validation below. |

## How a BA fills the mapping

Each `fields[]` entry needs a `name`, a `data_type`, and an **`xml_xpath`**
relative to the record element. A trailing `@name` step reads an **attribute**;
any other path reads **element text**. For a repeated child, set `data_type:
integer` (and optionally `xml_array: true`) — the parser collapses it to a
`<name>_count` column:

```json
{ "name": "CUSTOMER_ID", "data_type": "string",  "xml_xpath": "customer/@id", "required": true }
{ "name": "STATUS",      "data_type": "string",  "xml_xpath": "customer/status", "valid_values": ["ACTIVE","CLOSED","SUSPENDED"] }
{ "name": "TXN_COUNT",   "data_type": "integer", "xml_xpath": "transactions/transaction", "xml_array": true }
```

Two XML-relevant rule operators:

- **`nested_required`** — flags rows where the XPath is **absent** (the element
  or attribute is missing), distinct from a present-but-empty value.
- **`xml_array_length`** — flags rows whose array-count column is outside
  `[min_len, max_len]` (e.g. "every record must carry ≥1 transaction").

Namespaces are **stripped by default** (write plain local names); set
`source.namespace_aware: true` + a `source.namespaces` prefix→URI map to honor them.

## Run it

```bash
valdo validate \
  --file templates/etl/xml_single_record_sample/input.xml \
  --mapping templates/etl/xml_single_record_sample/mapping.json \
  --rules templates/etl/xml_single_record_sample/rules.json \
  --output reports/xml_validate.html
```

The seeded sample reports **`valid: false`, 4 errors** — `CUSTOMER_ID` attribute
missing (record 9), `STATUS` not in the allowlist (record 8), non-numeric `AGE`
(record 10), and an empty `transactions` element (record 6). That matches
`expected_report.json`.
