# Oracle Database Schema Requirements

## Overview

The Valdo application is designed to work with **your existing Oracle tables**. It does **NOT** require you to create specific tables to function. The application's primary purpose is to:

- Extract data from your existing Oracle tables
- Compare data between files or tables
- Reconcile mapping documents with your database schema
- Validate data against your existing table structures

---

## Required Tables: NONE

**The application works with whatever tables you already have in your Oracle database.**

The application uses Oracle system views to introspect your schema:
- `USER_TABLES` - To check if tables exist
- `USER_TAB_COLUMNS` - To get column information
- `USER_SEGMENTS` - To get table size information

---

## Optional Table: Transaction Log

If you want to use the **transaction logging feature**, you can optionally create a transaction log table.

### TRANSACTION_LOG Table (Optional)

This table is used by the `TransactionLogger` class to maintain an audit trail of database operations.

#### Create Table SQL

```sql
CREATE TABLE TRANSACTION_LOG (
    log_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operation VARCHAR2(50) NOT NULL,
    table_name VARCHAR2(100) NOT NULL,
    row_count NUMBER,
    status VARCHAR2(20) NOT NULL,
    details VARCHAR2(4000),
    log_timestamp TIMESTAMP DEFAULT SYSTIMESTAMP
);

-- Optional: Add indexes for better query performance
CREATE INDEX idx_transaction_log_timestamp ON TRANSACTION_LOG(log_timestamp);
CREATE INDEX idx_transaction_log_table ON TRANSACTION_LOG(table_name);
CREATE INDEX idx_transaction_log_status ON TRANSACTION_LOG(status);

-- Optional: Add comments
COMMENT ON TABLE TRANSACTION_LOG IS 'Audit trail for Valdo operations';
COMMENT ON COLUMN TRANSACTION_LOG.operation IS 'Type of operation: INSERT, UPDATE, DELETE, EXTRACT, etc.';
COMMENT ON COLUMN TRANSACTION_LOG.status IS 'Operation status: SUCCESS, FAILED, ROLLED_BACK';
```

#### Automatic Creation

The application can create this table automatically:

```python
from src.database.connection import OracleConnection
from src.database.transaction import TransactionLogger

# Connect to database
conn = OracleConnection.from_env()

# Create transaction logger
logger = TransactionLogger(conn, log_table="TRANSACTION_LOG")

# Create the table (if it doesn't exist)
logger.create_log_table()
```

#### Usage Example

```python
from src.database.transaction import TransactionLogger

logger = TransactionLogger(conn)

# Log a successful operation
logger.log_transaction(
    operation="EXTRACT",
    table_name="CUSTOMER_DATA",
    row_count=1000,
    status="SUCCESS",
    details="Extracted customer data to file"
)

# Log a failed operation
logger.log_transaction(
    operation="INSERT",
    table_name="ORDERS",
    row_count=0,
    status="FAILED",
    details="ORA-00001: unique constraint violated"
)
```

#### Query Transaction Log

```sql
-- View recent transactions
SELECT * FROM TRANSACTION_LOG
ORDER BY log_timestamp DESC
FETCH FIRST 100 ROWS ONLY;

-- View failed operations
SELECT * FROM TRANSACTION_LOG
WHERE status = 'FAILED'
ORDER BY log_timestamp DESC;

-- View operations by table
SELECT 
    table_name,
    COUNT(*) as total_operations,
    SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) as successful,
    SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as failed,
    SUM(row_count) as total_rows
FROM TRANSACTION_LOG
GROUP BY table_name
ORDER BY total_operations DESC;

-- View operations by date
SELECT 
    TRUNC(log_timestamp) as operation_date,
    COUNT(*) as operations,
    SUM(row_count) as rows_processed
FROM TRANSACTION_LOG
GROUP BY TRUNC(log_timestamp)
ORDER BY operation_date DESC;
```

---

## Your Existing Tables

The application is designed to work with your business tables. Here are common use cases:

### Example: Customer Data Table

```sql
-- Your existing table (example)
CREATE TABLE CUSTOMER_DATA (
    customer_id NUMBER PRIMARY KEY,
    first_name VARCHAR2(50),
    last_name VARCHAR2(50),
    email VARCHAR2(100),
    phone VARCHAR2(20),
    created_date DATE
);
```

**Application Usage:**

```bash
# Extract data from your table
valdo extract -t CUSTOMER_DATA -o customers.txt -l 1000

# Get table statistics
python -c "
from src.database.connection import OracleConnection
from src.database.extractor import DataExtractor

conn = OracleConnection.from_env()
extractor = DataExtractor(conn)
stats = extractor.get_table_stats('CUSTOMER_DATA')
print(stats)
"
```

### Example: Transaction Table

```sql
-- Your existing table (example)
CREATE TABLE TRANSACTIONS (
    transaction_id NUMBER PRIMARY KEY,
    account_number VARCHAR2(20),
    transaction_date DATE,
    amount NUMBER(10,2),
    transaction_type VARCHAR2(10),
    status VARCHAR2(20)
);
```

**Application Usage:**

```bash
# Extract with filter
valdo extract \
  -t TRANSACTIONS \
  -o recent_transactions.txt \
  --where "transaction_date >= SYSDATE - 30"
```

---

## Required Permissions

Your Oracle user needs these permissions to use the application:

### Minimum Permissions (Read-Only)

```sql
-- Grant SELECT on your tables
GRANT SELECT ON CUSTOMER_DATA TO cm3int;
GRANT SELECT ON TRANSACTIONS TO cm3int;

-- Grant access to system views (usually granted by default)
GRANT SELECT ON USER_TABLES TO cm3int;
GRANT SELECT ON USER_TAB_COLUMNS TO cm3int;
GRANT SELECT ON USER_SEGMENTS TO cm3int;
```

### Full Permissions (With Transaction Logging)

```sql
-- All read permissions above, plus:

-- Create table permission (for TRANSACTION_LOG)
GRANT CREATE TABLE TO cm3int;

-- Or grant specific permissions on existing log table
GRANT INSERT, SELECT ON TRANSACTION_LOG TO cm3int;
```

### Check Current Permissions

```sql
-- Check table privileges
SELECT * FROM USER_TAB_PRIVS
WHERE grantee = 'CM3INT';

-- Check system privileges
SELECT * FROM USER_SYS_PRIVS
WHERE username = 'CM3INT';
```

---

## Database Setup Checklist

### For Basic Usage (Extract, Compare, Validate)

- [x] Oracle database accessible (on-prem or Docker)
- [x] User account created (e.g., `cm3int`)
- [x] SELECT permission on your business tables
- [x] Access to `USER_TABLES`, `USER_TAB_COLUMNS`, `USER_SEGMENTS` views
- [x] Connection details configured in `.env` file

### For Transaction Logging (Optional)

- [ ] CREATE TABLE permission OR
- [ ] Pre-created `TRANSACTION_LOG` table
- [ ] INSERT permission on `TRANSACTION_LOG`
- [ ] SELECT permission on `TRANSACTION_LOG` (for querying logs)

---

## Quick Start

### 1. Configure Connection

```bash
# .env file
ORACLE_USER=cm3int
ORACLE_PASSWORD=your_password
ORACLE_DSN=localhost:1521/ORCLPDB1
```

### 2. Test Connection

```bash
python test_oracle_connection.py
```

### 3. List Your Tables

```python
from src.database.connection import OracleConnection
from src.database.query_executor import QueryExecutor

conn = OracleConnection.from_env()
executor = QueryExecutor(conn)

# Get all tables
query = "SELECT table_name FROM user_tables ORDER BY table_name"
df = executor.execute_query(query)
print(df)
```

### 4. Extract Data

```bash
# Extract from your existing table
valdo extract -t YOUR_TABLE_NAME -o output.txt -l 100
```

---

## Summary

✅ **No tables required** - Works with your existing Oracle tables  
✅ **Optional logging** - `TRANSACTION_LOG` table for audit trail  
✅ **Flexible** - Designed to adapt to your schema  
✅ **Read-only by default** - Safe for production databases  

The application is a **tool for working with your data**, not a system that requires its own database schema.
