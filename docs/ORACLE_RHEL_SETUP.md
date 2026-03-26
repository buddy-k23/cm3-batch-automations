# Oracle Connection Setup on RHEL Linux

This guide covers setting up Oracle database connections on Red Hat Enterprise Linux (RHEL) servers for both on-premise Oracle installations and Docker-based Oracle containers.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Option 1: Using python-oracledb (Recommended)](#option-1-using-python-oracledb-recommended)
3. [Option 2: Using cx_Oracle with Oracle Client](#option-2-using-cxoracle-with-oracle-client)
4. [Connecting to Oracle Docker Container](#connecting-to-oracle-docker-container)
5. [Connecting to On-Premise Oracle](#connecting-to-on-premise-oracle)
6. [Configuration](#configuration)
7. [Testing Connection](#testing-connection)
8. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### System Requirements
- RHEL 7, 8, or 9
- Python 3.8 or higher
- Network access to Oracle database (on-prem or Docker)

### Check Python Version
```bash
python3 --version
```

---

## Option 1: Using python-oracledb (Recommended)

**Best for:** Docker Oracle, modern deployments, no Oracle Client installation desired

### Installation

```bash
# Activate virtual environment
source venv/bin/activate

# Install python-oracledb
pip install oracledb
```

### Advantages
✅ No Oracle Client installation required  
✅ Works with both Docker and on-premise Oracle  
✅ Simpler setup and maintenance  
✅ Thin mode (pure Python) by default  
✅ Smaller footprint

### Configuration

Create or update `.env` file:

```bash
# Oracle Database Configuration
ORACLE_USER=your_username
ORACLE_PASSWORD=your_password
ORACLE_DSN=hostname:port/service_name

# Examples:
# Docker: localhost:1521/ORCLPDB1
# On-prem: dbserver.company.com:1521/PROD
```

---

## Option 2: Using cx_Oracle with Oracle Client

**Best for:** Legacy systems, thick mode requirements, existing Oracle Client installations

### Step 1: Install Oracle Instant Client

#### Download Oracle Instant Client

1. Go to: https://www.oracle.com/database/technologies/instant-client/linux-x86-64-downloads.html
2. Download these packages:
   - `oracle-instantclient-basic-21.x.x.x.x-1.x86_64.rpm`
   - `oracle-instantclient-sqlplus-21.x.x.x.x-1.x86_64.rpm` (optional, for testing)

#### Install RPM Packages

```bash
# Install as root
sudo yum install oracle-instantclient-basic-21.*.rpm
sudo yum install oracle-instantclient-sqlplus-21.*.rpm

# Or using dnf (RHEL 8+)
sudo dnf install oracle-instantclient-basic-21.*.rpm
sudo dnf install oracle-instantclient-sqlplus-21.*.rpm
```

#### Alternative: Manual Installation

```bash
# Create directory
sudo mkdir -p /opt/oracle

# Extract ZIP files
cd /opt/oracle
sudo unzip instantclient-basic-linux.x64-21.x.x.x.zip
sudo unzip instantclient-sqlplus-linux.x64-21.x.x.x.zip

# Create symbolic link
cd /opt/oracle/instantclient_21_x
sudo ln -s libclntsh.so.21.1 libclntsh.so
```

### Step 2: Set Environment Variables

Add to `/etc/profile.d/oracle.sh` (system-wide) or `~/.bashrc` (user-specific):

```bash
# Oracle Instant Client
export ORACLE_HOME=/usr/lib/oracle/21/client64
export LD_LIBRARY_PATH=$ORACLE_HOME/lib:$LD_LIBRARY_PATH
export PATH=$ORACLE_HOME/bin:$PATH

# If manual installation:
# export ORACLE_HOME=/opt/oracle/instantclient_21_x
# export LD_LIBRARY_PATH=$ORACLE_HOME:$LD_LIBRARY_PATH
# export PATH=$ORACLE_HOME:$PATH
```

Reload environment:

```bash
source ~/.bashrc
# or
source /etc/profile.d/oracle.sh
```

### Step 3: Install cx_Oracle

```bash
source venv/bin/activate
pip install cx_Oracle
```

### Step 4: Verify Installation

```bash
# Check Oracle Client
echo $ORACLE_HOME
ls -la $ORACLE_HOME/lib/libclntsh.so*

# Test with sqlplus (if installed)
sqlplus -v
```

---

## Connecting to Oracle Docker Container

### Scenario 1: Docker on Same RHEL Server

```bash
# Check Docker container
docker ps | grep oracle

# Get container details
docker inspect oracle-db | grep IPAddress
```

**Configuration (.env):**

```bash
# Using localhost (if port is mapped)
ORACLE_USER=cm3int
ORACLE_PASSWORD=your_password
ORACLE_DSN=localhost:1521/ORCLPDB1

# Or using container IP
ORACLE_DSN=172.17.0.2:1521/ORCLPDB1
```

### Scenario 2: Docker on Remote Server

```bash
# Ensure port 1521 is accessible
telnet docker-host.company.com 1521
```

**Configuration (.env):**

```bash
ORACLE_USER=cm3int
ORACLE_PASSWORD=your_password
ORACLE_DSN=docker-host.company.com:1521/ORCLPDB1
```

### Scenario 3: Docker with Custom Network

```bash
# If application runs in Docker network
docker network inspect bridge
```

**Configuration (.env):**

```bash
# Use container name as hostname (if in same network)
ORACLE_DSN=oracle-db:1521/ORCLPDB1
```

---

## Connecting to On-Premise Oracle

### Using TNS Names (tnsnames.ora)

#### Step 1: Create tnsnames.ora

```bash
# Create TNS admin directory
mkdir -p $ORACLE_HOME/network/admin

# Create tnsnames.ora
cat > $ORACLE_HOME/network/admin/tnsnames.ora << 'EOF'
PROD =
  (DESCRIPTION =
    (ADDRESS = (PROTOCOL = TCP)(HOST = dbserver.company.com)(PORT = 1521))
    (CONNECT_DATA =
      (SERVER = DEDICATED)
      (SERVICE_NAME = PROD)
    )
  )

DEV =
  (DESCRIPTION =
    (ADDRESS = (PROTOCOL = TCP)(HOST = devdb.company.com)(PORT = 1521))
    (CONNECT_DATA =
      (SERVICE_NAME = DEV)
    )
  )
EOF
```

#### Step 2: Set TNS_ADMIN

```bash
export TNS_ADMIN=$ORACLE_HOME/network/admin
```

#### Step 3: Configure Application

**Using TNS alias (.env):**

```bash
ORACLE_USER=app_user
ORACLE_PASSWORD=your_password
ORACLE_DSN=PROD  # TNS alias
```

### Using Easy Connect String

**Configuration (.env):**

```bash
ORACLE_USER=app_user
ORACLE_PASSWORD=your_password

# Basic format
ORACLE_DSN=hostname:port/service_name

# With server type
ORACLE_DSN=hostname:port/service_name:dedicated

# Examples
ORACLE_DSN=dbserver.company.com:1521/PROD
ORACLE_DSN=10.10.10.50:1521/ORCL
```

---

## Configuration

### Environment Variables

Create `.env` file in project root:

```bash
# Oracle Database Configuration
ORACLE_USER=your_username
ORACLE_PASSWORD=your_password
ORACLE_DSN=hostname:port/service_name

# Optional: Only needed for cx_Oracle thick mode
ORACLE_HOME=/usr/lib/oracle/21/client64

# API Configuration
API_PORT=8000
API_HOST=0.0.0.0
API_WORKERS=4

# Logging
LOG_LEVEL=INFO
LOG_DIR=logs
```

### Secure the .env File

```bash
chmod 600 .env
chown your_user:your_group .env
```

### System-wide Configuration (Optional)

For system services, add to `/etc/environment`:

```bash
ORACLE_USER=app_user
ORACLE_DSN=dbserver.company.com:1521/PROD
```

**Note:** Never store passwords in system files. Use secrets management.

---

## Testing Connection

### Using Test Script

```bash
cd /path/to/cm3-batch-automations
source venv/bin/activate
python test_oracle_connection.py
```

### Using Python Directly

```python
import oracledb

# Test connection
connection = oracledb.connect(
    user="your_username",
    password="your_password",
    dsn="hostname:1521/service_name"
)

cursor = connection.cursor()
cursor.execute("SELECT 'Connected!' FROM DUAL")
result = cursor.fetchone()
print(result[0])

connection.close()
```

### Using sqlplus (if installed)

```bash
# Test with Easy Connect
sqlplus username/password@hostname:1521/service_name

# Test with TNS alias
sqlplus username/password@PROD

# Test from Docker host to container
sqlplus username/password@localhost:1521/ORCLPDB1
```

---

## Troubleshooting

### Issue: "DPI-1047: Cannot locate a 64-bit Oracle Client library"

**Solution:**

```bash
# Check if Oracle Client is installed
ls -la /usr/lib/oracle/*/client64/lib/libclntsh.so*

# Verify LD_LIBRARY_PATH
echo $LD_LIBRARY_PATH

# Add to LD_LIBRARY_PATH
export LD_LIBRARY_PATH=/usr/lib/oracle/21/client64/lib:$LD_LIBRARY_PATH

# Make permanent in ~/.bashrc
echo 'export LD_LIBRARY_PATH=/usr/lib/oracle/21/client64/lib:$LD_LIBRARY_PATH' >> ~/.bashrc
```

### Issue: "ORA-12541: TNS:no listener"

**Causes:**
- Oracle listener not running
- Wrong hostname/port
- Firewall blocking connection

**Solutions:**

```bash
# Check if port is open
telnet hostname 1521
nc -zv hostname 1521

# Check firewall
sudo firewall-cmd --list-ports
sudo firewall-cmd --add-port=1521/tcp --permanent
sudo firewall-cmd --reload

# For Docker, check port mapping
docker port oracle-db
```

### Issue: "ORA-12514: TNS:listener does not currently know of service"

**Solution:**

```bash
# Check service name
# Connect to database and run:
SELECT value FROM v$parameter WHERE name = 'service_names';

# Update DSN with correct service name
ORACLE_DSN=hostname:1521/correct_service_name
```

### Issue: "ORA-01017: invalid username/password"

**Solution:**

```bash
# Verify credentials
# Check if account is locked
SELECT username, account_status FROM dba_users WHERE username = 'YOUR_USER';

# Unlock if needed (as DBA)
ALTER USER your_user ACCOUNT UNLOCK;
```

### Issue: Connection works with sqlplus but not Python

**Solution:**

```bash
# Ensure same environment variables
env | grep ORACLE

# Run Python with same environment
source ~/.bashrc
source venv/bin/activate
python test_oracle_connection.py
```

### Issue: SELinux Blocking Connection

**Solution:**

```bash
# Check SELinux status
getenforce

# Temporarily disable (for testing)
sudo setenforce 0

# Check audit logs
sudo ausearch -m avc -ts recent

# Create SELinux policy (if needed)
sudo grep python /var/log/audit/audit.log | audit2allow -M mypython
sudo semodule -i mypython.pp

# Re-enable SELinux
sudo setenforce 1
```

---

## Production Deployment Checklist

### Security

- [ ] Store passwords in secrets manager (HashiCorp Vault, AWS Secrets Manager)
- [ ] Use `.env` file with restricted permissions (600)
- [ ] Never commit `.env` to version control
- [ ] Use service accounts with minimal privileges
- [ ] Enable Oracle Advanced Security (encryption, checksumming)

### Network

- [ ] Configure firewall rules for Oracle port (1521)
- [ ] Use VPN or private network for on-premise connections
- [ ] Set up connection pooling for better performance
- [ ] Configure TNS timeout settings

### Monitoring

- [ ] Set up connection health checks
- [ ] Monitor connection pool metrics
- [ ] Log database errors
- [ ] Set up alerts for connection failures

### Example systemd Service

```ini
[Unit]
Description=Valdo API
After=network.target oracle.service

[Service]
Type=simple
User=cm3app
Group=cm3app
WorkingDirectory=/opt/cm3-batch-automations
EnvironmentFile=/opt/cm3-batch-automations/.env
ExecStart=/opt/cm3-batch-automations/venv/bin/uvicorn src.api.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

## Quick Reference

### python-oracledb (Thin Mode)
```bash
pip install oracledb
# No Oracle Client needed
ORACLE_DSN=hostname:1521/service_name
```

### cx_Oracle (Thick Mode)
```bash
sudo yum install oracle-instantclient-basic-*.rpm
export ORACLE_HOME=/usr/lib/oracle/21/client64
export LD_LIBRARY_PATH=$ORACLE_HOME/lib:$LD_LIBRARY_PATH
pip install cx_Oracle
```

### Docker Connection
```bash
# Same server
ORACLE_DSN=localhost:1521/ORCLPDB1

# Remote server
ORACLE_DSN=docker-host:1521/ORCLPDB1
```

### On-Premise Connection
```bash
# Easy Connect
ORACLE_DSN=dbserver.company.com:1521/PROD

# TNS Names
export TNS_ADMIN=/path/to/tnsnames
ORACLE_DSN=PROD
```

---

## Additional Resources

- [python-oracledb Documentation](https://python-oracledb.readthedocs.io/)
- [Oracle Instant Client Downloads](https://www.oracle.com/database/technologies/instant-client/downloads.html)
- [Oracle Net Services Documentation](https://docs.oracle.com/en/database/oracle/oracle-database/21/netag/)
- [Project Usage Guide](./USAGE_GUIDE.md)
