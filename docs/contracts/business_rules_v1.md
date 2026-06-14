# Business Rules Contract v1

This document defines the JSON contract for business rule configuration consumed by `RuleEngine`.

## Top-level shape
- `metadata` (object)
- `rules` (array of rule objects)

## Metadata fields
Recommended:
- `name`
- `description`
- `created_by`
- `created_date`
- `template_path`
- `template_type`

## Rule object (required)
- `id` (string)
- `name` (string)
- `type` (`field_validation` or `cross_field`)
- `severity` (`error` | `warning` | `info`)
- `enabled` (boolean)

## Field validation rule
Required:
- `field` (string)
- `operator` (one of: `not_null`, `in`, `not_in`, `regex`, `range`, `length`, `>`, `>=`, `<`, `<=`, `==`, `!=`)

Optional (operator dependent):
- `value`
- `values`
- `pattern`
- `min` / `max`
- `min_length` / `max_length`
- `when` (condition string)

## Cross-field rule
Required:
- `left_field` (string)
- `right_field` (string)
- `operator` (`>`, `>=`, `<`, `<=`, `==`, `!=`)

Optional:
- `when` (condition string)
