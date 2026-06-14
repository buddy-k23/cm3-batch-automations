# Excel Template Structure & API Upload Guide

## 🚨 Issue: API Server Not Running

The error you're seeing (`Failed to fetch`) means the API server is **not currently running** on port 8000.

---

## ✅ Solution: Start the API Server

### Start the Server

```bash
cd /path/to/valdo

# Activate virtual environment
source venv/bin/activate

# Start the API server
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

The server will start and show:
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Started reloader process
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
```

### Access API Documentation

Once running, visit:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

---

## 📋 Excel Template Structure

Your Excel file **MUST** have these columns:

### Required Columns

| Column Name | Required | Description | Example |
|-------------|----------|-------------|---------|
| **Field Name** | ✅ Yes | Field identifier | `CUSTOMER_ID` |
| **Data Type** | ✅ Yes | Data type | `String`, `Number`, `Date` |

### Optional Columns (Recommended)

| Column Name | Required | Description | Example |
|-------------|----------|-------------|---------|
| **Position** | For fixed-width | 1-indexed position | `1` |
| **Length** | For fixed-width | Character length | `18` |
| **Format** | No | Format specification | `CCYYMMDD`, `9(12)V9(6)` |
| **Required** | No | Is field required? | `Y`, `N`, `YES`, `NO` |
| **Description** | No | Field description | `Customer identifier` |
| **Target Name** | No | Database column name | `CUST_ID` |
| **Default Value** | No | Default if empty | `0`, `N/A` |
| **Valid Values** | No | Allowed values list (`|` or `,` separated) | `ACTIVE|INACTIVE|CLOSED` |

---

## 📝 Excel Template Examples

### Example 1: Fixed-Width Format

| Field Name | Position | Length | Data Type | Format | Required | Description |
|------------|----------|--------|-----------|--------|----------|-------------|
| LOCATION-CODE | 1 | 6 | String | | Y | Location code |
| ACCT-NUM | 2 | 18 | String | | Y | Account number |
| CREDIT-LIMIT-AMT | 3 | 13 | Numeric | 9(12) | N | Credit limit |
| EXPIRATION-DATE | 4 | 8 | Date | CCYYMMDD | N | Expiration date |
| CUSTOMER-NAME | 5 | 50 | String | | Y | Customer full name |

**Save as**: `my_template.xlsx`

### Example 2: Pipe-Delimited Format

| Field Name | Data Type | Required | Description | Target Name |
|------------|-----------|----------|-------------|-------------|
| customer_id | String | Y | Customer ID | CUSTOMER_ID |
| first_name | String | Y | First name | FIRST_NAME |
| last_name | String | Y | Last name | LAST_NAME |
| email | String | Y | Email address | EMAIL |
| phone | String | N | Phone number | PHONE |
| status | String | Y | Account status | STATUS |

**Save as**: `customer_template.xlsx`

---

## 🚀 Using the API

### Method 1: Upload via Swagger UI (Easiest)

1. **Start the server** (see above)
2. **Open browser**: http://localhost:8000/docs
3. **Find**: `POST /api/v1/mappings/upload`
4. **Click**: "Try it out"
5. **Upload**: Choose your Excel file
6. **Optional**: Set `mapping_name` and `file_format`
7. **Click**: "Execute"

### Method 2: Upload via cURL

```bash
# Basic upload (auto-detect format)
curl -X 'POST' \
  'http://localhost:8000/api/v1/mappings/upload' \
  -H 'accept: application/json' \
  -H 'Content-Type: multipart/form-data' \
  -F 'file=@p327-target-template.xlsx'

# With mapping name
curl -X 'POST' \
  'http://localhost:8000/api/v1/mappings/upload?mapping_name=my_mapping' \
  -H 'accept: application/json' \
  -H 'Content-Type: multipart/form-data' \
  -F 'file=@p327-target-template.xlsx'

# With format specification
curl -X 'POST' \
  'http://localhost:8000/api/v1/mappings/upload?mapping_name=my_mapping&file_format=fixed_width' \
  -H 'accept: application/json' \
  -H 'Content-Type: multipart/form-data' \
  -F 'file=@p327-target-template.xlsx'
```

### Method 3: Upload via Python

```python
import requests

url = "http://localhost:8000/api/v1/mappings/upload"

# Upload file
with open("p327-target-template.xlsx", "rb") as f:
    files = {"file": f}
    params = {
        "mapping_name": "p327_mapping",
        "file_format": "fixed_width"
    }
    response = requests.post(url, files=files, params=params)

print(response.json())
```

---

## 📤 API Response

### Success Response

```json
{
  "filename": "p327-target-template.xlsx",
  "size": 57927,
  "mapping_id": "p327_mapping",
  "message": "Template converted successfully. Mapping saved as 'p327_mapping'"
}
```

The mapping is saved to: `config/mappings/p327_mapping.json`

### Error Responses

**Invalid file type:**
```json
{
  "detail": "Invalid file type. Only .xlsx, .xls, and .csv files are supported."
}
```

**Missing required columns:**
```json
{
  "detail": "Error converting template: Missing required columns: ['Field Name', 'Data Type']"
}
```

---

## 🔍 Supported Data Types

The converter normalizes these data types:

| Input Values | Normalized To |
|--------------|---------------|
| `String`, `Str`, `Text`, `Varchar`, `Char` | `string` |
| `Number`, `Numeric`, `Num`, `Decimal`, `Float` | `decimal` |
| `Integer`, `Int` | `integer` |
| `Date`, `Datetime`, `Timestamp` | `date` |
| `Boolean`, `Bool` | `boolean` |

---

## 🎯 Format Auto-Detection

The API automatically detects the file format based on your Excel columns:

- **Has `Position` AND `Length` columns** → `fixed_width`
- **No position/length columns** → `pipe_delimited` (default)

You can override by specifying `file_format` parameter.

---

## 📋 Complete API Endpoints

### Upload Template
```
POST /api/v1/mappings/upload
```
Upload Excel/CSV template and convert to mapping

### List Mappings
```
GET /api/v1/mappings/
```
Get all available mappings

### Get Mapping
```
GET /api/v1/mappings/{mapping_id}
```
Get specific mapping by ID

### Validate Mapping
```
POST /api/v1/mappings/validate
```
Validate mapping structure

### Delete Mapping
```
DELETE /api/v1/mappings/{mapping_id}
```
Delete mapping by ID

---

## 🧪 Testing Your Upload

### Step 1: Create Test Excel File

Create `test_template.xlsx` with:

| Field Name | Position | Length | Data Type | Required |
|------------|----------|--------|-----------|----------|
| ID | 1 | 10 | String | Y |
| NAME | 2 | 50 | String | Y |
| AMOUNT | 3 | 15 | Decimal | N |

### Step 2: Start Server

```bash
source venv/bin/activate
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

### Step 3: Upload

```bash
curl -X 'POST' \
  'http://localhost:8000/api/v1/mappings/upload?mapping_name=test_mapping' \
  -H 'accept: application/json' \
  -H 'Content-Type: multipart/form-data' \
  -F 'file=@test_template.xlsx'
```

### Step 4: Verify

```bash
# Check if mapping was created
ls -la config/mappings/test_mapping.json

# View the mapping
cat config/mappings/test_mapping.json
```

---

## 🐛 Troubleshooting

### Issue: "Failed to fetch"

**Cause**: API server not running

**Solution**:
```bash
# Check if server is running
lsof -i :8000

# If not running, start it
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

### Issue: "Missing required columns"

**Cause**: Excel file doesn't have `Field Name` or `Data Type` columns

**Solution**: Ensure your Excel has these exact column names (case-sensitive):
- `Field Name`
- `Data Type`

### Issue: "Invalid file type"

**Cause**: File is not `.xlsx`, `.xls`, or `.csv`

**Solution**: Convert your file to Excel format

### Issue: CORS Error

**Cause**: Accessing API from different origin (browser)

**Solution**: The API should have CORS enabled. Check `src/api/main.py` for CORS middleware configuration.

---

## 📚 Additional Resources

- **API Documentation**: http://localhost:8000/docs (when server is running)
- **Mapping Guide**: [`docs/MAPPING_QUICKSTART.md`](MAPPING_QUICKSTART.md)
- **Universal Mapping Guide**: [`docs/UNIVERSAL_MAPPING_GUIDE.md`](UNIVERSAL_MAPPING_GUIDE.md)
- **Template Converter**: [`src/config/template_converter.py`](../src/config/template_converter.py)

---

## 💡 Quick Tips

1. **Always start the API server first** before making requests
2. **Use Swagger UI** for easy testing: http://localhost:8000/docs
3. **Column names are case-sensitive**: Use exact names `Field Name`, `Data Type`
4. **Format auto-detection works** but you can override with `file_format` parameter
5. **Check the logs** if upload fails - server will show detailed error messages
