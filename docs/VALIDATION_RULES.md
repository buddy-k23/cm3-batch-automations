# Validation Rules Reference

## Overview

Validation rules check data quality and enforce constraints. They identify violations and report errors during data processing.

---

## 📋 Available Validation Rule Types

### 1. `not_null`
**Ensure field has a value (not null/empty)**

```json
{
  "type": "not_null"
}
```

**Checks:**
- Field is not null
- Field is not empty
- Field has actual data

**Example:**
```json
{
  "source_column": "customer_id",
  "target_column": "CUSTOMER_ID",
  "data_type": "string",
  "required": true,
  "validation_rules": [
    {"type": "not_null"}
  ]
}
```

**Violations:**
- `null`
- `NaN`
- Empty values

**Use Cases:**
- Required fields
- Primary keys
- Mandatory data

---

### 2. `min_length`
**Minimum string length**

```json
{
  "type": "min_length",
  "parameters": {
    "length": 5
  }
}
```

**Parameters:**
- `length` (required): Minimum number of characters

**Example:**
```json
{
  "type": "min_length",
  "parameters": {"length": 10}
}
```

**Violations:**
- `"ABC"` (length 3, min 10)
- `"12345"` (length 5, min 10)

**Valid:**
- `"ABCDEFGHIJ"` (length 10)
- `"1234567890"` (length 10)

**Use Cases:**
- Account numbers (min 10 digits)
- Postal codes (min 5 characters)
- Phone numbers (min 10 digits)

---

### 3. `max_length`
**Maximum string length**

```json
{
  "type": "max_length",
  "parameters": {
    "length": 100
  }
}
```

**Parameters:**
- `length` (required): Maximum number of characters

**Example:**
```json
{
  "type": "max_length",
  "parameters": {"length": 50}
}
```

**Violations:**
- String longer than 50 characters

**Valid:**
- Any string 50 characters or less

**Use Cases:**
- Database column limits (VARCHAR(50))
- Name fields (max 100 chars)
- Description fields (max 500 chars)

---

### 4. `regex`
**Pattern matching with regular expressions**

```json
{
  "type": "regex",
  "parameters": {
    "pattern": "^[A-Z]{3}[0-9]{4}$"
  }
}
```

**Parameters:**
- `pattern` (required): Regular expression pattern

**Examples:**

Email validation:
```json
{
  "type": "regex",
  "parameters": {
    "pattern": "^[a-z0-9._%+-]+@[a-z0-9.-]+\\.[a-z]{2,}$"
  }
}
```

Transaction ID (TXN + 10 digits):
```json
{
  "type": "regex",
  "parameters": {
    "pattern": "^TXN[0-9]{10}$"
  }
}
```

Date format (YYYY-MM-DD):
```json
{
  "type": "regex",
  "parameters": {
    "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
  }
}
```

Phone number (10 digits):
```json
{
  "type": "regex",
  "parameters": {
    "pattern": "^[0-9]{10}$"
  }
}
```

**Use Cases:**
- Format validation
- Pattern enforcement
- Code structure validation
- Email/phone validation

---

### 5. `range`
**Numeric range validation**

```json
{
  "type": "range",
  "parameters": {
    "min": 0,
    "max": 999999
  }
}
```

**Parameters:**
- `min` (optional): Minimum value (inclusive)
- `max` (optional): Maximum value (inclusive)

**Examples:**

Positive amounts only:
```json
{
  "type": "range",
  "parameters": {
    "min": 0.01,
    "max": 99999.99
  }
}
```

Age range:
```json
{
  "type": "range",
  "parameters": {
    "min": 18,
    "max": 120
  }
}
```

Percentage (0-100):
```json
{
  "type": "range",
  "parameters": {
    "min": 0,
    "max": 100
  }
}
```

**Violations:**
- Value < min
- Value > max
- Non-numeric values (converted to NaN)

**Use Cases:**
- Amount limits
- Age validation
- Percentage validation
- Quantity limits

---

### 6. `in_list`
**Value must be in allowed list**

```json
{
  "type": "in_list",
  "parameters": {
    "values": ["ACTIVE", "INACTIVE", "PENDING"]
  }
}
```

**Parameters:**
- `values` (required): Array of allowed values

**Examples:**

Status codes:
```json
{
  "type": "in_list",
  "parameters": {
    "values": ["ACTIVE", "INACTIVE"]
  }
}
```

Transaction types:
```json
{
  "type": "in_list",
  "parameters": {
    "values": ["DEBIT", "CREDIT", "TRANSFER"]
  }
}
```

Country codes:
```json
{
  "type": "in_list",
  "parameters": {
    "values": ["US", "CA", "MX", "UK", "FR", "DE"]
  }
}
```

**Violations:**
- Any value not in the list

**Use Cases:**
- Status validation
- Type codes
- Category validation
- Enum-like fields

---

## 🔗 Combining Validation Rules

You can apply multiple validation rules to a single field:

```json
"validation_rules": [
  {"type": "not_null"},
  {
    "type": "min_length",
    "parameters": {"length": 5}
  },
  {
    "type": "max_length",
    "parameters": {"length": 20}
  },
  {
    "type": "regex",
    "parameters": {"pattern": "^[A-Z0-9]+$"}
  }
]
```

**All rules must pass** for the field to be valid.

---

## 📝 Complete Examples

### Example 1: Customer ID Validation

```json
{
  "source_column": "customer_id",
  "target_column": "CUSTOMER_ID",
  "data_type": "string",
  "required": true,
  "transformations": [
    {"type": "trim"},
    {"type": "upper"}
  ],
  "validation_rules": [
    {"type": "not_null"},
    {
      "type": "min_length",
      "parameters": {"length": 8}
    },
    {
      "type": "max_length",
      "parameters": {"length": 20}
    },
    {
      "type": "regex",
      "parameters": {"pattern": "^CUST[0-9]{4,16}$"}
    }
  ]
}
```

**Validates:**
- ✅ Not null
- ✅ Between 8-20 characters
- ✅ Starts with "CUST" followed by 4-16 digits

---

### Example 2: Email Validation

```json
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
      "type": "max_length",
      "parameters": {"length": 100}
    },
    {
      "type": "regex",
      "parameters": {
        "pattern": "^[a-z0-9._%+-]+@[a-z0-9.-]+\\.[a-z]{2,}$"
      }
    }
  ]
}
```

---

### Example 3: Amount Validation

```json
{
  "source_column": "amount",
  "target_column": "AMOUNT",
  "data_type": "number",
  "required": true,
  "transformations": [
    {"type": "trim"},
    {"type": "cast", "parameters": {"to_type": "number"}}
  ],
  "validation_rules": [
    {"type": "not_null"},
    {
      "type": "range",
      "parameters": {
        "min": 0.01,
        "max": 99999.99
      }
    }
  ]
}
```

---

### Example 4: Status Code Validation

```json
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
    {"type": "not_null"},
    {
      "type": "in_list",
      "parameters": {
        "values": ["ACTIVE", "INACTIVE", "PENDING", "SUSPENDED"]
      }
    }
  ]
}
```

---

### Example 5: Phone Number Validation

```json
{
  "source_column": "phone",
  "target_column": "PHONE",
  "data_type": "string",
  "required": false,
  "transformations": [
    {"type": "trim"},
    {"type": "replace", "parameters": {"old": "-", "new": ""}},
    {"type": "replace", "parameters": {"old": "(", "new": ""}},
    {"type": "replace", "parameters": {"old": ")", "new": ""}},
    {"type": "replace", "parameters": {"old": " ", "new": ""}}
  ],
  "validation_rules": [
    {
      "type": "regex",
      "parameters": {"pattern": "^[0-9]{10}$"}
    }
  ]
}
```

---

### Example 6: Date Format Validation

```json
{
  "source_column": "transaction_date",
  "target_column": "TRANSACTION_DATE",
  "data_type": "date",
  "required": true,
  "transformations": [
    {"type": "trim"}
  ],
  "validation_rules": [
    {"type": "not_null"},
    {
      "type": "regex",
      "parameters": {"pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}
    }
  ]
}
```

---

## 🎯 Common Validation Patterns

### Pattern 1: Required Text Field
```json
"validation_rules": [
  {"type": "not_null"}
]
```

### Pattern 2: Required Field with Length Limit
```json
"validation_rules": [
  {"type": "not_null"},
  {"type": "max_length", "parameters": {"length": 100}}
]
```

### Pattern 3: Optional Field with Format
```json
"validation_rules": [
  {"type": "regex", "parameters": {"pattern": "^[A-Z0-9]+$"}}
]
```

### Pattern 4: Numeric Range
```json
"validation_rules": [
  {"type": "not_null"},
  {"type": "range", "parameters": {"min": 0, "max": 999999}}
]
```

### Pattern 5: Enum/Status Field
```json
"validation_rules": [
  {"type": "not_null"},
  {"type": "in_list", "parameters": {"values": ["A", "B", "C"]}}
]
```

---

## 📊 Validation Results

When validation runs, you get a report:

```json
{
  "valid": false,
  "errors": [
    "Column 'customer_id' validation failed (not_null): 5 violations",
    "Column 'amount' validation failed (range): 12 violations",
    "Column 'status' validation failed (in_list): 3 violations"
  ],
  "warnings": [
    "Optional column missing: middle_name"
  ]
}
```

---

## 🔍 Common Regex Patterns

### Email
```
^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$
```

### Phone (10 digits)
```
^[0-9]{10}$
```

### Date (YYYY-MM-DD)
```
^[0-9]{4}-[0-9]{2}-[0-9]{2}$
```

### Date (MM/DD/YYYY)
```
^[0-9]{2}/[0-9]{2}/[0-9]{4}$
```

### SSN (XXX-XX-XXXX)
```
^[0-9]{3}-[0-9]{2}-[0-9]{4}$
```

### Zip Code (5 or 9 digits)
```
^[0-9]{5}(-[0-9]{4})?$
```

### Alphanumeric Only
```
^[A-Z0-9]+$
```

### Transaction ID (TXN + 10 digits)
```
^TXN[0-9]{10}$
```

---

## ⚠️ Important Notes

1. **Validation Order**: Rules are checked in the order specified
2. **All Must Pass**: All validation rules must pass for field to be valid
3. **Null Handling**: Most rules skip null values (except `not_null`)
4. **Case Sensitivity**: Apply transformations before validation for case-insensitive checks
5. **Performance**: Complex regex patterns can slow processing on large datasets
6. **Error Reporting**: Validation returns count of violations, not specific rows

---

## 🔍 Validation Implementation

All validation rules are implemented in:
[`src/config/mapping_parser.py`](../src/config/mapping_parser.py)

The `_validate_rule` method (lines 227-255) handles all validation logic.

---

## 📚 Related Documentation

- **Transformation Types**: [`docs/TRANSFORMATION_TYPES.md`](TRANSFORMATION_TYPES.md)
- **Mapping Quick Start**: [`docs/MAPPING_QUICKSTART.md`](MAPPING_QUICKSTART.md)
- **Universal Mapping Guide**: [`docs/UNIVERSAL_MAPPING_GUIDE.md`](UNIVERSAL_MAPPING_GUIDE.md)

---

## 🆕 Business Rules Validation Framework (New!)

The new business rules framework provides advanced validation capabilities beyond the basic mapping validation rules.

### Key Differences

| Feature | Mapping Validation | Business Rules |
|---------|-------------------|----------------|
| **Definition** | Inline in mapping JSON | Separate JSON/Excel file |
| **Scope** | Field-level only | Field + Cross-field + Conditional |
| **Reporting** | Integrated in validation report | Dedicated section in HTML report |
| **Template Support** | No | Yes (Excel/CSV templates) |

### Business Rule Types

#### 1. Field Validation Rules
Similar to mapping validation but with enhanced capabilities:
- `not_null`, `range`, `regex`, `in_list`, `length`
- **Plus**: Severity levels (error/warning/info)
- **Plus**: Custom violation messages

#### 2. Cross-Field Validation Rules
Validate relationships between fields:
- `field_comparison`: Compare two fields (e.g., `end_date > start_date`)
- `depends_on`: Conditional requirements (e.g., "If status=ACTIVE, then balance required")
- `mutually_exclusive`: Only one field can have a value

### Example Business Rule

```json
{
  "rule_id": "R001",
  "rule_name": "Account Number Format",
  "type": "field_validation",
  "severity": "error",
  "field": "ACCT-NUM",
  "operator": "regex",
  "value": "^[0-9]{10}$",
  "message": "Account number must be exactly 10 digits"
}
```

### Creating Rules from Excel

**Template Structure:**
| Rule ID | Rule Name | Type | Severity | Field | Operator | Value |
|---------|-----------|------|----------|-------|----------|-------|
| R001 | Account Format | field_validation | error | ACCT-NUM | regex | ^[0-9]{10}$ |
| R002 | Balance Range | field_validation | warning | BALANCE | range | 0,999999 |

**Convert to JSON:**
```bash
valdo convert-rules \
  -t config/templates/rules.xlsx \
  -o config/rules/rules.json
```

### Using Business Rules

```bash
valdo validate \
  -f data/file.txt \
  -m config/mappings/mapping.json \
  -r config/rules/rules.json \
  -o report.html
```

### When to Use Which

**Use Mapping Validation Rules when:**
- Validating basic field constraints
- Enforcing database schema requirements
- Simple format checks

**Use Business Rules when:**
- Complex cross-field logic
- Business-specific validation
- Need for severity levels
- Template-based rule management
- Detailed violation reporting

---

## 💡 Best Practices

1. **Always validate required fields** with `not_null`
2. **Use `max_length`** to match database column limits
3. **Combine rules** for comprehensive validation
4. **Transform before validating** (e.g., trim before checking length)
5. **Test regex patterns** with sample data before deploying
6. **Document complex patterns** in mapping descriptions
7. **Use `in_list`** for enum-like fields instead of complex regex
8. **Consider performance** when validating large datasets
