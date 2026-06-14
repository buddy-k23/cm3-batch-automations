# CSV Standards for JSON Generation

This project supports standardized CSV templates that can be converted into:

- Mapping JSON (`config/mappings/*.json`)
- Business Rules JSON (`config/rules/*.json`)

Use:

```bash
python scripts/generate_from_csv_templates.py \
  --mapping-csv config/templates/csv/mapping_template.standard.csv \
  --mapping-out config/mappings/my_mapping.json \
  --mapping-name my_mapping \
  --mapping-format pipe_delimited \
  --rules-csv config/templates/csv/business_rules_template.standard.csv \
  --rules-out config/rules/my_rules.json
```

---

## 1) Mapping CSV Standard

Template: `config/templates/csv/mapping_template.standard.csv`

Columns:

- `Field Name` (required)
- `Data Type` (required)
- `Target Name`
- `Required` (Y/N)
- `Position` (fixed-width only)
- `Length` (fixed-width only)
- `Format`
- `Description`
- `Default Value`
- `Valid Values` (pipe-separated list, e.g. `ACTIVE|INACTIVE|CLOSED`)

Notes:

- For delimited files (`pipe_delimited`, `csv`, `tsv`), `Position` and `Length` can be blank.
- For `fixed_width`, provide both `Position` and `Length`.

---

## 2) Business Rules CSV Standard

Template: `config/templates/csv/business_rules_template.standard.csv`

Columns:

- `Rule ID` (required)
- `Rule Name` (required)
- `Description` (required)
- `Type` (required: `field_validation` or `cross_field`)
- `Severity` (required: `error|warning|info`)
- `Operator` (required)
- `Field`
- `Value`
- `Values`
- `Pattern`
- `Min`
- `Max`
- `Left Field`
- `Right Field`
- `Enabled` (`TRUE/FALSE`, `Y/N`, `1/0`)
- `Min Length`
- `Max Length`

### Operator parameter matrix

- Numeric: `> < >= <= == !=` -> requires `Field`, `Value`
- List: `in not_in` -> requires `Field`, `Values` (comma-separated)
- Regex: `regex` -> requires `Field`, `Pattern`
- Range: `range` -> requires `Field`, and `Min` and/or `Max`
- Required: `not_null` -> requires `Field`
- String length: `length` -> requires `Field`, and `Min Length` and/or `Max Length`
- Cross-field: `Type=cross_field` -> requires `Left Field`, `Right Field`, operator comparison

---

## 3) Validation behavior

The generation script validates template headers before conversion and fails fast if required standardized headers are missing.
