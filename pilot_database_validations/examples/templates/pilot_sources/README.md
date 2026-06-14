# Pilot Source Templates - Quick Guide

This folder contains CSV templates to onboard new source systems into the validation framework.

## Folder Layout

- `starter/` → generic starter templates for new teams/systems
- `<source_table_name>/` → source-specific templates generated from current contracts

Each source folder includes:
- `mapping-template.csv`
- `rules-template.csv`
- `file-config-template.csv`
- `sample-source-data.csv`
- `source-query.sql`  ← put the Oracle source query here

## What to start with

### If you are new
Use:
1. `starter/mapping-template.csv`
2. `starter/rules-template.csv`
3. `starter/file-config-template.csv`
4. `starter/source-query.sql`

### If you are working on an existing onboarded source
Use the matching source folder, e.g.:
- `shaw_src_p327/`
- `shaw_src_tranert/`

## Minimum required fields

### mapping-template.csv
- `target_field`
- `source_field`
- `data_type`
- `required`

### rules-template.csv
- `rule_id`
- `scope`
- `severity`
- `priority`
- `expression`
- `message_template`

### file-config-template.csv
- `format`
- `header_enabled`

## Notes

- Keep rule IDs stable once used in reports.
- Use `starter/invalid-rules-template.csv` as a reference for common validation errors.
- Prefer explicit source fields over placeholders.
- After editing templates, run contract generation and validation before pilot runs.

## Suggested onboarding flow

1. Copy starter templates (or source-specific templates)
2. Fill mapping/rules/file-config values
3. Add SQL in `source-query.sql` (per source folder)
4. Generate contracts (`mapping.json`, `rules.json`) and inject query
5. Validate contracts against schemas
6. Run E2E pilot and review summary + promotion evidence
