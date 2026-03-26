# PEX Deployment Guide

## Overview

This guide explains how to deploy Valdo as a PEX (Python EXecutable) file on RHEL 8.9.

## Prerequisites

- RHEL 8.9 with Python 3.9+
- Oracle Instant Client 19.23 installed on the system
- PEX tool installed

## What is PEX?

PEX (Python EXecutable) creates a single executable file containing your Python application and all its pure-Python dependencies. It's similar to a JAR file in Java.

## Limitations with This Project

**Cannot be bundled in PEX:**
- Oracle Instant Client (native C library)
- Configuration files (need to be external)
- Data directories
- Log files

**Can be bundled in PEX:**
- All Python source code (`src/`)
- Pure Python dependencies (pandas, Jinja2, click, etc.)

## Installation

### 1. Install PEX Tool

```bash
pip install pex
```

### 2. Install Oracle Instant Client (System-wide)

Follow the RHEL deployment guide to install Oracle Instant Client:

```bash
# Download and install Oracle Instant Client
cd /tmp
wget https://download.oracle.com/otn_software/linux/instantclient/1923000/instantclient-basic-linux.x64-19.23.0.0.0dbru.zip
sudo mkdir -p /opt/oracle
sudo unzip instantclient-basic-linux.x64-19.23.0.0.0dbru.zip -d /opt/oracle

# Configure library path
sudo sh -c "echo /opt/oracle/instantclient_19_23 > /etc/ld.so.conf.d/oracle-instantclient.conf"
sudo ldconfig

# Set environment variables
echo 'export ORACLE_HOME=/opt/oracle/instantclient_19_23' | sudo tee -a /etc/profile.d/oracle.sh
echo 'export LD_LIBRARY_PATH=$ORACLE_HOME:$LD_LIBRARY_PATH' | sudo tee -a /etc/profile.d/oracle.sh
sudo chmod +x /etc/profile.d/oracle.sh
source /etc/profile.d/oracle.sh
```

## Building the PEX File

### Build Script

Use the provided build script:

```bash
./build_pex.sh
```

Or manually:

```bash
pex . \
  --requirement requirements.txt \
  --entry-point src.main:main \
  --output-file dist/valdo.pex \
  --python-shebang="/usr/bin/env python3.9" \
  --inherit-path=prefer
```

### Build Options Explained

- `--requirement requirements.txt`: Include all dependencies
- `--entry-point src.main:main`: Entry point function
- `--output-file dist/valdo.pex`: Output PEX file
- `--python-shebang`: Python interpreter to use
- `--inherit-path=prefer`: Allow system packages (for cx_Oracle)

## Deployment Structure

```
/opt/cm3-batch-automations/
├── valdo.pex           # PEX executable
├── config/                 # Configuration files
│   ├── dev.json
│   ├── staging.json
│   └── mappings/
├── data/                   # Data directory
│   ├── samples/
│   └── mappings/
├── logs/                   # Log directory
└── .env                    # Environment variables
```

## Deployment Steps

### 1. Create Deployment Directory

```bash
sudo mkdir -p /opt/cm3-batch-automations/{config,data/samples,data/mappings,logs}
sudo useradd -m -s /bin/bash cm3app
sudo chown -R cm3app:cm3app /opt/cm3-batch-automations
```

### 2. Copy PEX File and Configuration

```bash
# Copy PEX file
sudo cp dist/valdo.pex /opt/cm3-batch-automations/
sudo chmod +x /opt/cm3-batch-automations/valdo.pex

# Copy configuration files
sudo cp -r config/* /opt/cm3-batch-automations/config/
sudo cp .env.example /opt/cm3-batch-automations/.env

# Set ownership
sudo chown -R cm3app:cm3app /opt/cm3-batch-automations
sudo chmod 600 /opt/cm3-batch-automations/.env
```

### 3. Configure Environment

```bash
sudo vim /opt/cm3-batch-automations/.env
```

Set:
```bash
ORACLE_USER=your_username
ORACLE_PASSWORD=your_password
ORACLE_DSN=hostname:port/service_name
ORACLE_HOME=/opt/oracle/instantclient_19_23
LD_LIBRARY_PATH=/opt/oracle/instantclient_19_23
```

## Running the PEX

### Direct Execution

```bash
cd /opt/cm3-batch-automations
./valdo.pex --help
```

### With Environment Variables

```bash
cd /opt/cm3-batch-automations
source .env
export LD_LIBRARY_PATH=$ORACLE_HOME:$LD_LIBRARY_PATH
./valdo.pex
```

## systemd Service Configuration

Create `/etc/systemd/system/valdo.service`:

```ini
[Unit]
Description=Valdo (PEX)
After=network.target

[Service]
Type=simple
User=cm3app
Group=cm3app
WorkingDirectory=/opt/cm3-batch-automations
EnvironmentFile=/opt/cm3-batch-automations/.env
Environment="PATH=/usr/local/bin:/usr/bin:/bin"
Environment="ORACLE_HOME=/opt/oracle/instantclient_19_23"
Environment="LD_LIBRARY_PATH=/opt/oracle/instantclient_19_23"
ExecStart=/opt/cm3-batch-automations/valdo.pex
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable valdo.service
sudo systemctl start valdo.service
sudo systemctl status valdo.service
```

## Advantages of PEX Deployment

1. **Single File Distribution**: Easy to copy and deploy
2. **Version Control**: One file per version
3. **No Virtual Environment**: PEX manages dependencies internally
4. **Fast Startup**: Pre-compiled Python bytecode
5. **Reproducible**: Same dependencies everywhere

## Disadvantages

1. **Native Dependencies**: Oracle Instant Client must be installed separately
2. **Configuration**: External config files still needed
3. **Updates**: Need to rebuild PEX for code changes
4. **Size**: PEX file can be large (includes all dependencies)

## Updating the Application

```bash
# Build new PEX
./build_pex.sh

# Stop service
sudo systemctl stop valdo.service

# Replace PEX file
sudo cp dist/valdo.pex /opt/cm3-batch-automations/
sudo chown cm3app:cm3app /opt/cm3-batch-automations/valdo.pex
sudo chmod +x /opt/cm3-batch-automations/valdo.pex

# Start service
sudo systemctl start valdo.service
```

## Troubleshooting

### PEX Won't Execute

```bash
# Check shebang
head -1 /opt/cm3-batch-automations/valdo.pex

# Verify Python version
python3.9 --version

# Check permissions
ls -la /opt/cm3-batch-automations/valdo.pex
```

### Oracle Client Not Found

```bash
# Verify Oracle Instant Client
ls -la /opt/oracle/instantclient_19_23

# Check library path
ldconfig -p | grep oracle

# Test cx_Oracle
python3.9 -c "import cx_Oracle; print(cx_Oracle.clientversion())"
```

### Import Errors

```bash
# Run with verbose output
PEX_VERBOSE=1 ./valdo.pex

# Check PEX contents
unzip -l valdo.pex | grep -i oracle
```

## Alternative: Shiv (PEX Alternative)

If you encounter issues with PEX, consider using Shiv:

```bash
pip install shiv

shiv -c valdo \
  -o dist/valdo.pyz \
  -p "/usr/bin/env python3.9" \
  --site-packages /opt/oracle/instantclient_19_23 \
  .
```

## Comparison: PEX vs Traditional Deployment

| Aspect | PEX | Traditional (venv) |
|--------|-----|--------------------|
| Distribution | Single file | Multiple files |
| Dependencies | Bundled | Separate install |
| Updates | Replace file | git pull + pip |
| Startup | Fast | Slower |
| Debugging | Harder | Easier |
| Size | Larger | Smaller |
| Oracle Client | External | External |

## Recommendation

**Use PEX if:**
- You need simple, single-file deployment
- You deploy to multiple servers
- You want version control per deployment
- You're comfortable with Oracle Instant Client system installation

**Use Traditional venv if:**
- You need frequent code updates
- You want easier debugging
- You prefer standard Python deployment
- You need more flexibility

## Security Considerations

1. **PEX File Permissions**: Set to 755 (executable)
2. **Configuration Files**: Set to 600 (owner read/write only)
3. **Environment File**: Never include in PEX, keep external
4. **Oracle Client**: Keep system-wide installation updated

## CI/CD Integration

Add to your GitLab CI pipeline:

```yaml
build-pex:
  stage: build
  script:
    - pip install pex
    - ./build_pex.sh
  artifacts:
    paths:
      - dist/valdo.pex
    expire_in: 30 days
```
