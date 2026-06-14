# How to Create Mapping Files

## Quick Overview

**Mapping files are JSON configurations** that define how to map data from source files to Oracle database tables. You already have several examples in [`config/mappings/`](../config/mappings/).

---

## 🚀 Quick Start - 3 Methods

### Method 1: Copy Existing Mapping (Fastest)

```bash
# Copy an existing mapping as a template
cp config/mappings/transaction_mapping.json config/mappings/my_new_mapping.json

# Edit the file
code config/mappings/my_new_mapping.json
```

### Method 2: From Excel Template (Recommended)

```bash
# Convert Excel template to JSON mapping
python src/config/template_converter.py \
  data/mappings/my_template.xlsx \
  config/mappings/my_mapping.json \
  my_mapping_name \
  fixed_width
```

### Method 3: Create from Scratch

Use the template below and save to `config/mappings/your_mapping.json`

---

## 📋 Mapping File Structure

### Basic Template

```json
{
  "mapping_name": "your_unique_name",
  "version": "1.0.0",
  "description": "Description of what this mapping does",
  
  "source": {
    "type": "file",
    "format": "pipe_delimited",
    "file_path": "data/samples/your_file.txt",
    "delimiter": "|",
    "encoding": "UTF-8"
  },
  
  "target": {
    "type": "database",
    "table_name": "YOUR_ORACLE_TABLE"
  },
  
  "mappings": [
    {
      "source_column": "field_from_file",
      "target_column": "ORACLE_COLUMN_NAME",
      "data_type": "string",
      "required": true,
      "transformations": [
        {"type": "trim"},
        {"type": "upper"}
      ],
      "validation_rules": [
        {"type": "not_null"}
      ]
    }
  ],
  
  "key_columns": ["primary_key_field"],
  
  "metadata": {
    "created_by": "your_name",
    "created_date": "2026-02-07T00:00:00Z"
  }
}
```

---

## 📝 Step-by-Step Example

### Scenario: Map Customer File to CUSTOMER_DATA Table

**Your Oracle Table:**
```sql
CREATE TABLE CUSTOMER_DATA (
    CUSTOMER_ID VARCHAR2(20) PRIMARY KEY,
    FIRST_NAME VARCHAR2(50),
    LAST_NAME VARCHAR2(50),
    EMAIL VARCHAR2(100),
    PHONE VARCHAR2(20),
    STATUS VARCHAR2(10)
);
```

**Your File (pipe-delimited):**
```
cust001|John|Doe|john.doe@email.com|555-1234|active
cust002|Jane|Smith|jane.smith@email.com|555-5678|inactive
```

**Create Mapping:**

```json
{
  "mapping_name": "customer_file_to_db",
  "version": "1.0.0",
  "description": "Maps customer data from pipe-delimited file to CUSTOMER_DATA table",
  
  "source": {
    "type": "file",
    "format": "pipe_delimited",
    "file_path": "data/customers.txt",
    "delimiter": "|"
  },
  
  "target": {
    "type": "database",
    "table_name": "CUSTOMER_DATA"
  },
  
  "mappings": [
    {
      "source_column": "customer_id",
      "target_column": "CUSTOMER_ID",
      "data_type": "string",
      "required": true,
      "transformations": [
        {"type": "trim"}
      ],
      "validation_rules": [
        {"type": "not_null"},
        {"type": "max_length", "parameters": {"length": 20}}
      ]
    },
    {
      "source_column": "first_name",
      "target_column": "FIRST_NAME",
      "data_type": "string",
      "required": true,
      "transformations": [
        {"type": "trim"}
      ],
      "validation_rules": [
        {"type": "not_null"},
        {"type": "max_length", "parameters": {"length": 50}}
      ]
    },
    {
      "source_column": "last_name",
      "target_column": "LAST_NAME",
      "data_type": "string",
      "required": true,
      "transformations": [
        {"type": "trim"}
      ],
      "validation_rules": [
        {"type": "not_null"}
      ]
    },
    {
      "source_column": "email",
      "target_column": "EMAIL",
      "data_type": "string",
      "required": true,
      "transformations": [
        {"type": "trim"},
        {"type": "lower"}
      ],
      "validation_rules": [
        {"type": "not_null"},
        {
          "type": "regex",
          "parameters": {"pattern": "^[a-z0-9._%+-]+@[a-z0-9.-]+\\.[a-z]{2,}$"}
        }
      ]
    },
    {
      "source_column": "phone",
      "target_column": "PHONE",
      "data_type": "string",
      "required": false,
      "transformations": [
        {"type": "trim"}
      ],
      "validation_rules": []
    },
    {
      "source_column": "status",
      "target_column": "STATUS",
      "data_type": "string",
      "required": true,
      "transformations": [
        {"type": "trim"},
        {"type": "upper"}
      ],
      "validation_rules": [
        {
          "type": "in_list",
          "parameters": {"values": ["ACTIVE", "INACTIVE"]}
        }
      ]
    }
  ],
  
  "key_columns": ["customer_id"],
  
  "metadata": {
    "created_by": "data_team",
    "created_date": "2026-02-07T00:00:00Z"
  }
}
```

**Save as:** `config/mappings/customer_mapping.json`

---

## 🔧 Common Configurations

### Pipe-Delimited File

```json
"source": {
  "type": "file",
  "format": "pipe_delimited",
  "delimiter": "|",
  "file_path": "data/file.txt"
}
```

### Fixed-Width File

```json
"source": {
  "type": "file",
  "format": "fixed_width",
  "file_path": "data/file.txt"
},
"mappings": [
  {
    "source_column": "account_number",
    "target_column": "ACCT_NUM",
    "position": 1,
    "length": 18,
    "data_type": "string"
  }
]
```

### CSV File

```json
"source": {
  "type": "file",
  "format": "csv",
  "delimiter": ",",
  "file_path": "data/file.csv"
}
```

---

## 🎯 Data Types

| Type | Description | Example |
|------|-------------|---------|
| `string` | Text data | `"John Doe"` |
| `number` | Numeric data | `123.45` |
| `date` | Date values | `"2026-02-07"` |
| `boolean` | True/false | `true` |

---

## 🔄 Common Transformations

```json
"transformations": [
  {"type": "trim"},                    // Remove whitespace
  {"type": "upper"},                   // Convert to uppercase
  {"type": "lower"},                   // Convert to lowercase
  {
    "type": "replace",                 // Replace text
    "parameters": {"old": "-", "new": ""}
  },
  {
    "type": "cast",                    // Convert data type
    "parameters": {"to_type": "number"}
  },
  {
    "type": "substring",               // Extract substring
    "parameters": {"start": 0, "length": 10}
  }
]
```

---

## ✅ Common Validation Rules

```json
"validation_rules": [
  {"type": "not_null"},                // Field must have value
  {
    "type": "min_length",              // Minimum length
    "parameters": {"length": 5}
  },
  {
    "type": "max_length",              // Maximum length
    "parameters": {"length": 100}
  },
  {
    "type": "regex",                   // Pattern matching
    "parameters": {"pattern": "^[A-Z]{3}[0-9]{4}$"}
  },
  {
    "type": "range",                   // Numeric range
    "parameters": {"min": 0, "max": 999999}
  },
  {
    "type": "in_list",                 // Must be in list
    "parameters": {"values": ["ACTIVE", "INACTIVE"]}
  }
]
```

---

## 🧪 Testing Your Mapping

### 1. Validate Mapping Structure

```bash
python src/config/universal_mapping_parser.py \
  config/mappings/your_mapping.json
```

### 2. Reconcile with Database

```bash
valdo reconcile -m config/mappings/your_mapping.json
```

This will check:
- ✅ Target table exists
- ✅ All target columns exist
- ✅ Data types are compatible
- ✅ Required fields match

### 3. Test with Sample Data

```bash
# Parse a file using the mapping
valdo parse -f data/your_file.txt -o output.csv
```

---

## 📁 File Locations

| Item | Path |
|------|------|
| **Your Mappings** | `config/mappings/*.json` |
| **Examples** | [`config/mappings/transaction_mapping.json`](../config/mappings/transaction_mapping.json) |
| **Templates** | `config/templates/*.json` |
| **Full Guide** | [`docs/UNIVERSAL_MAPPING_GUIDE.md`](UNIVERSAL_MAPPING_GUIDE.md) |

---

## 🎓 Real Examples in Your Project

### Example 1: Transaction Mapping
[`config/mappings/transaction_mapping.json`](../config/mappings/transaction_mapping.json)
- Maps transaction file to TRANSACTION table
- Includes validation for transaction types
- Amount range validation

### Example 2: Customer Mapping
[`config/mappings/customer_mapping.json`](../config/mappings/customer_mapping.json)
- Maps customer data
- Email validation
- Phone number formatting

### Example 3: P327 Mapping
[`config/mappings/p327_mapping.json`](../config/mappings/p327_mapping.json)
- Complex fixed-width format
- 252 fields
- Production example

---

## 💡 Tips

1. **Start Simple**: Copy an existing mapping and modify it
2. **Test Early**: Validate mapping before processing large files
3. **Use Reconciliation**: Always reconcile with database schema
4. **Document**: Add good descriptions and metadata
5. **Version Control**: Increment version when making changes

---

## ❓ Common Questions

**Q: Do I need to create Oracle tables first?**  
A: Yes, the target Oracle table should exist before using the mapping.

**Q: Can I map one file to multiple tables?**  
A: No, each mapping is for one file → one table. Create multiple mappings.

**Q: What if my file has extra columns?**  
A: Only columns defined in the mapping will be processed. Extra columns are ignored.

**Q: Can I use the same mapping for different files?**  
A: Yes, as long as the files have the same structure.

---

## 🚀 Next Steps

1. **Copy an example**: `cp config/mappings/transaction_mapping.json config/mappings/my_mapping.json`
2. **Edit for your needs**: Update table name, columns, validations
3. **Validate**: `python src/config/universal_mapping_parser.py config/mappings/my_mapping.json`
4. **Reconcile**: `valdo reconcile -m config/mappings/my_mapping.json`
5. **Use it**: `valdo parse -f your_file.txt`

For detailed information, see the [Universal Mapping Guide](UNIVERSAL_MAPPING_GUIDE.md).
