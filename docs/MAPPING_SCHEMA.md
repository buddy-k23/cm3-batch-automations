# Mapping Document Schema

## Overview

Mapping documents define how file columns map to database columns for data validation and comparison.

## Schema Definition

### Basic Structure

```json
{
  "mapping_name": "string",
  "version": "string",
  "description": "string",
  "source": {
    "type": "file|database",
    "format": "pipe_delimited|fixed_width|csv",
    "file_path": "string (optional)",
    "table_name": "string (optional)"
  },
  "target": {
    "type": "database|file",
    "table_name": "string (optional)",
    "file_path": "string (optional)"
  },
  "mappings": [
    {
      "source_column": "string",
      "target_column": "string",
      "data_type": "string|number|date|boolean",
      "required": boolean,
      "transformations": [
        {
          "type": "trim|upper|lower|substring|replace|cast",
          "parameters": {}
        }
      ],
      "validation_rules": [
        {
          "type": "not_null|min_length|max_length|regex|range",
          "parameters": {}
        }
      ]
    }
  ],
  "key_columns": ["string"],
  "metadata": {
    "created_by": "string",
    "created_date": "ISO 8601 date",
    "last_modified": "ISO 8601 date"
  }
}
```

## Field Descriptions

### Top Level

- **mapping_name**: Unique identifier for the mapping
- **version**: Version number (semantic versioning recommended)
- **description**: Human-readable description

### Source

- **type**: Source type (`file` or `database`)
- **format**: File format (if source is file)
- **file_path**: Path to source file (optional)
- **table_name**: Source table name (if source is database)

### Target

- **type**: Target type (`database` or `file`)
- **table_name**: Target table name (if target is database)
- **file_path**: Path to target file (optional)

### Mappings

Array of column mappings:

- **source_column**: Column name in source
- **target_column**: Column name in target
- **data_type**: Expected data type
- **required**: Whether column is required
- **transformations**: Array of transformations to apply
- **validation_rules**: Array of validation rules

### Key Columns

List of columns that uniquely identify a row (used for comparison).

## Transformation Types

### trim
Remove leading/trailing whitespace
```json
{"type": "trim"}
```

### upper
Convert to uppercase
```json
{"type": "upper"}
```

### lower
Convert to lowercase
```json
{"type": "lower"}
```

### substring
Extract substring
```json
{
  "type": "substring",
  "parameters": {
    "start": 0,
    "length": 10
  }
}
```

### replace
Replace text
```json
{
  "type": "replace",
  "parameters": {
    "old": "pattern",
    "new": "replacement"
  }
}
```

### cast
Cast to different type
```json
{
  "type": "cast",
  "parameters": {
    "to_type": "string|number|date"
  }
}
```

## Validation Rule Types

### not_null
Value must not be null
```json
{"type": "not_null"}
```

### min_length
Minimum string length
```json
{
  "type": "min_length",
  "parameters": {"length": 5}
}
```

### max_length
Maximum string length
```json
{
  "type": "max_length",
  "parameters": {"length": 100}
}
```

### regex
Match regular expression
```json
{
  "type": "regex",
  "parameters": {"pattern": "^[A-Z]{3}[0-9]{4}$"}
}
```

### range
Numeric range validation
```json
{
  "type": "range",
  "parameters": {
    "min": 0,
    "max": 100
  }
}
```

## Example Mappings

See `config/mappings/` directory for complete examples:

- `customer_mapping.json` - Customer data mapping
- `transaction_mapping.json` - Transaction data mapping
- `product_mapping.json` - Product data mapping

## Usage

```python
from src.config.loader import ConfigLoader
from src.config.mapping_parser import MappingParser

# Load mapping
loader = ConfigLoader()
mapping_dict = loader.load_mapping("customer_mapping.json")

# Parse and validate
parser = MappingParser()
mapping = parser.parse(mapping_dict)

# Use mapping
validator = MappingValidator(mapping.get_column_mapping())
```
