# Universal Mapping Structure - User Guide

## Overview

The **Universal Mapping Structure** provides a standardized way to define file mappings for all formats (pipe-delimited, fixed-width, CSV, TSV) **without writing custom scripts**. This eliminates the need to create custom parsing code for each new file format.

## Quick Start

### 1. Create Mapping from Excel Template

```bash
source venv/bin/activate

# Convert Excel template to universal mapping
python src/config/template_converter.py \
  data/mappings/my_template.xlsx \
  config/mappings/my_mapping.json \
  my_mapping_name \
  fixed_width
```

### 2. Validate Mapping

```bash
# Validate the generated mapping
python src/config/universal_mapping_parser.py \
  config/mappings/my_mapping.json
```

### 3. Use Mapping

The universal mapping can now be used with any parser or tool in the system.

---

## Mapping Structure

### Basic Template

```json
{
  "mapping_name": "unique_identifier",
  "version": "1.0.0",
  "description": "Human-readable description",
  
  "source": {
    "type": "file",
    "format": "pipe_delimited|fixed_width|csv|tsv",
    "delimiter": "|",
    "encoding": "UTF-8"
  },
  
  "target": {
    "type": "database",
    "table_name": "TABLE_NAME"
  },
  
  "fields": [
    {
      "name": "field_name",
      "position": 1,
      "length": 10,
      "data_type": "string|number|date|boolean",
      "required": true,
      "transformations": [],
      "validation_rules": []
    }
  ],
  
  "key_columns": ["field_name"]
}
```

---

## File Formats

### Pipe-Delimited Format

**Template**: [`config/templates/pipe_delimited_template.json`](../config/templates/pipe_delimited_template.json)

**Key Properties**:
- `format`: `"pipe_delimited"`
- `delimiter`: `"|"` (or custom delimiter)
- No `position` or `length` required
- Fields extracted by delimiter

**Example**:
```json
{
  "source": {
    "format": "pipe_delimited",
    "delimiter": "|"
  },
  "fields": [
    {
      "name": "customer_id",
      "data_type": "string",
      "required": true
    }
  ]
}
```

### Fixed-Width Format

**Template**: [`config/templates/fixed_width_template.json`](../config/templates/fixed_width_template.json)

**Key Properties**:
- `format`: `"fixed_width"`
- `position`: Required (1-indexed)
- `length`: Required (character count)
- Fields extracted by position

**Example**:
```json
{
  "source": {
    "format": "fixed_width"
  },
  "fields": [
    {
      "name": "LOCATION-CODE",
      "position": 1,
      "length": 6,
      "data_type": "string",
      "required": true
    },
    {
      "name": "ACCT-NUM",
      "position": 2,
      "length": 18,
      "data_type": "string",
      "required": true
    }
  ]
}
```

---

## Excel Template Format

Create an Excel file with the following columns:

| Column Name | Required | Description | Example |
|-------------|----------|-------------|---------|
| Field Name | ✅ Yes | Field identifier | `CUSTOMER_ID` |
| Data Type | ✅ Yes | `String`, `Number`, `Date`, `Boolean` | `String` |
| Position | For fixed-width | 1-indexed position | `1` |
| Length | For fixed-width | Character length | `18` |
| Format | No | Format specification | `CCYYMMDD`, `9(12)V9(6)` |
| Required | No | `Y` or `N` | `Y` |
| Description | No | Field description | `Customer identifier` |
| Target Name | No | Database column name | `CUST_ID` |
| Default Value | No | Default if empty | `0` |
| Valid Values | No | Allowed values list (`|` or `,` separated) | `ACTIVE|INACTIVE|CLOSED` |

**Example Excel Template**:

| Field Name | Position | Length | Data Type | Format | Required | Description |
|------------|----------|--------|-----------|--------|----------|-------------|
| LOCATION-CODE | 1 | 6 | String | | Y | Location code |
| ACCT-NUM | 2 | 18 | String | | Y | Account number |
| CREDIT-LIMIT-AMT | 3 | 13 | Numeric | 9(12) | N | Credit limit |
| EXPIRATION-DATE | 4 | 8 | Date | CCYYMMDD | N | Expiration date |

---

## Field Specifications

### Data Types

- **string**: Text data
- **integer**: Whole numbers
- **decimal**: Numbers with decimals
- **date**: Date values
- **boolean**: True/false values

### Transformations

Apply transformations to field values:

```json
"transformations": [
  {"type": "trim"},
  {"type": "upper"},
  {"type": "lower"},
  {
    "type": "replace",
    "parameters": {"old": "-", "new": ""}
  },
  {
    "type": "cast",
    "parameters": {"to_type": "decimal"}
  }
]
```

**Available Transformations**:
- `trim`: Remove whitespace
- `upper`: Convert to uppercase
- `lower`: Convert to lowercase
- `substring`: Extract substring
- `replace`: Replace text
- `cast`: Convert data type
- `pad_left`: Pad with characters on left
- `pad_right`: Pad with characters on right

### Validation Rules

Add validation rules to fields:

```json
"validation_rules": [
  {"type": "not_null"},
  {
    "type": "min_length",
    "parameters": {"length": 5}
  },
  {
    "type": "max_length",
    "parameters": {"length": 100}
  },
  {
    "type": "regex",
    "parameters": {"pattern": "^[A-Z]{3}[0-9]{4}$"}
  },
  {
    "type": "range",
    "parameters": {"min": 0, "max": 999999}
  },
  {
    "type": "in_list",
    "parameters": {"values": ["ACTIVE", "INACTIVE"]}
  }
]
```

---

## Usage Examples

### Example 1: Convert P327 Excel Template

```bash
# Convert P327 template to universal mapping
python src/config/template_converter.py \
  data/mappings/p327-target-template.xlsx \
  config/mappings/p327_universal.json \
  p327_universal \
  fixed_width

# Output:
# Mapping Summary:
#   Name: p327_universal
#   Format: fixed_width
#   Fields: 252
#   Total record length: 2809 characters
# ✓ Conversion complete!
```

### Example 2: Validate Mapping

```bash
# Validate pipe-delimited mapping
python src/config/universal_mapping_parser.py \
  config/templates/pipe_delimited_template.json

# Output:
# Mapping: pipe_delimited_example
# Format: pipe_delimited
# Fields: 7
# Key columns: ['customer_id']
# ✓ Mapping is valid
```

### Example 3: Use in Python Code

```python
from src.config.universal_mapping_parser import UniversalMappingParser

# Load mapping
parser = UniversalMappingParser(
    mapping_path='config/mappings/my_mapping.json'
)

# Get field positions for fixed-width
if parser.get_format() == 'fixed_width':
    positions = parser.get_field_positions()
    # Returns: [('FIELD1', 0, 10), ('FIELD2', 10, 25), ...]

# Get column names for delimited
else:
    columns = parser.get_column_names()
    delimiter = parser.get_delimiter()

# Get required fields
required = parser.get_required_fields()

# Get transformations for a field
transforms = parser.get_transformations('customer_id')

# Validate mapping
validation = parser.validate_schema()
if validation['valid']:
    print("✓ Mapping is valid")
```

---

## Creating Mappings

### Method 1: From Excel Template (Recommended)

1. **Create Excel file** with field specifications
2. **Convert to JSON**:
   ```bash
   python src/config/template_converter.py \
     template.xlsx \
     config/mappings/output.json \
     mapping_name \
     fixed_width
   ```
3. **Validate**:
   ```bash
   python src/config/universal_mapping_parser.py \
     config/mappings/output.json
   ```

### Method 2: Copy Template

1. **Copy template**:
   ```bash
   cp config/templates/pipe_delimited_template.json \
      config/mappings/my_mapping.json
   ```
2. **Edit fields** in JSON file
3. **Validate** with parser

### Method 3: Create from Scratch

Use the JSON schema as reference: [`config/schemas/universal_mapping_schema.json`](../config/schemas/universal_mapping_schema.json)

---

## Benefits

✅ **No Custom Scripts**: One structure for all file formats  
✅ **Template-Based**: Create from Excel/CSV templates  
✅ **Reusable**: Share and reuse mappings  
✅ **Validated**: Built-in schema validation  
✅ **Self-Documenting**: Field descriptions included  
✅ **Extensible**: Easy to add new formats  

---

## File Locations

| Item | Path |
|------|------|
| **JSON Schema** | `config/schemas/universal_mapping_schema.json` |
| **Pipe Template** | `config/templates/pipe_delimited_template.json` |
| **Fixed-Width Template** | `config/templates/fixed_width_template.json` |
| **Parser** | `src/config/universal_mapping_parser.py` |
| **Converter** | `src/config/template_converter.py` |
| **Mappings** | `config/mappings/*.json` |

---

## Troubleshooting

### Missing Required Columns in Excel

**Error**: `Missing required columns: ['Field Name', 'Data Type']`

**Solution**: Ensure Excel template has columns named exactly:
- `Field Name` (required)
- `Data Type` (required)
- `Position` (for fixed-width)
- `Length` (for fixed-width)

### Invalid Data Type

**Error**: Field data type not recognized

**Solution**: Use standard data types:
- `String`, `Number`, `Date`, `Boolean`
- `Integer`, `Decimal`

### Mapping Validation Failed

**Error**: `Field X missing position for fixed-width format`

**Solution**: For fixed-width format, all fields must have `position` and `length`.

---

## Fixed-Width v2 Normalization (Task 1)

For upcoming multi-record-type fixed-width support, legacy fixed-width mappings are normalized internally to a v2 shape with:
- `record_types` (legacy becomes one `DETAIL` type with `classification.kind=default`)
- `file_rules` defaults (header/trailer remain optional unless configured)

See:
- `docs/contracts/fixed_width_multitype_v2.md`
- `docs/adr/0003-fixed-width-v2-normalization.md`

## Next Steps

1. **Create your first mapping** from an Excel template
2. **Validate the mapping** using the parser
3. **Use the mapping** with existing parsers
4. **Share templates** with your team

For more examples, see:
- [`config/templates/`](../config/templates/)
- [`config/mappings/`](../config/mappings/)
