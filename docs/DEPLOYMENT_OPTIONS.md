# Deployment Options for RHEL 8.9 (No Docker)

## Overview

This guide covers all deployment options for Valdo on RHEL 8.9 **without using Docker**. The system now includes a **REST API with Swagger UI** and **universal mapping structure**.

## Deployment Modes

### 1. CLI Mode (Traditional)
- Command-line batch processing
- File parsing and comparison
- Database operations

### 2. API Mode (New!)
- REST API with FastAPI
- Swagger UI at `/docs`
- Web-based file uploads
- Interactive documentation

## Deployment Options Comparison

| Option | Complexity | Isolation | Updates | Best For |
|--------|------------|-----------|---------|----------|
| **1. Traditional venv** | Low | None | Easy | Development, frequent updates |
| **2. PEX** | Low | Partial | Medium | Simple production, single-file |
| **3. RPM Package** | Medium | None | Easy | Enterprise, yum/dnf managed |
| **4. Systemd + venv** | Low | None | Easy | Production, service management |
| **5. Podman** | Medium | Full | Medium | Container-like without Docker |
| **6. API Server** | Low | None | Easy | Web access, REST API |

---

## Option 1: Traditional Virtual Environment (Recommended)

**Best for**: Most use cases, easy maintenance

### Advantages
- Simple setup and maintenance
- Easy debugging
- Standard Python workflow
- Quick updates with git pull

### Deployment Steps

See **docs/RHEL_DEPLOYMENT.md** for complete instructions.

**Quick Summary:**
```bash
# Install system dependencies
sudo yum install -y python39 python39-devel gcc make wget unzip libaio

# Install Oracle Instant Client
# (See RHEL_DEPLOYMENT.md for details)

# Create application user
sudo useradd -m -s /bin/bash cm3app
sudo mkdir -p /opt/cm3-batch-automations
sudo chown cm3app:cm3app /opt/cm3-batch-automations

# Deploy application
sudo su - cm3app
cd /opt/cm3-batch-automations
git clone <repo-url> .
python3.9 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
vim .env  # Set credentials

# Run as systemd service
sudo systemctl enable valdo.service
sudo systemctl start valdo.service
```

---

## Option 2: PEX (Python Executable)

**Best for**: Single-file deployment, multiple servers

### Advantages
- Single executable file
- No virtual environment needed
- Easy version management
- Fast startup

### Deployment Steps

See **docs/PEX_DEPLOYMENT.md** for complete instructions.

**Quick Summary:**
```bash
# Build PEX (on dev machine)
./build_pex.sh

# Deploy to server
scp dist/valdo.pex server:/opt/cm3-batch-automations/
scp -r config server:/opt/cm3-batch-automations/

# On server
chmod +x /opt/cm3-batch-automations/valdo.pex
./valdo.pex
```

---

## Option 3: RPM Package (Enterprise)

**Best for**: Enterprise environments, centralized management

### Advantages
- Native RHEL package management
- Automatic dependency resolution
- Easy updates via yum/dnf
- Rollback support
- Signed packages

### Build RPM

See **docs/RPM_DEPLOYMENT.md** for complete instructions.

**Quick Summary:**
```bash
# Build RPM
./build_rpm.sh

# Install on server
sudo yum install -y cm3-batch-automations-0.1.0-1.el8.noarch.rpm

# Configure
sudo vim /etc/valdo/config.json

# Start service
sudo systemctl enable valdo
sudo systemctl start valdo
```

---

## Option 4: Systemd Service + Virtual Environment

**Best for**: Production deployments, automatic restarts

### Advantages
- Automatic startup on boot
- Process monitoring and restart
- Logging via journald
- Resource limits
- Standard RHEL service management

### Setup

**1. Deploy application with venv (Option 1)**

**2. Create systemd service:**

`/etc/systemd/system/valdo.service`:
````ini
[Unit]
Description=Valdo API
After=network.target

[Service]
Type=simple
User=cm3app
Group=cm3app
WorkingDirectory=/opt/cm3-batch-automations
Environment="PATH=/opt/cm3-batch-automations/venv/bin:/opt/oracle/instantclient_19_23:/usr/local/bin:/usr/bin:/bin"
Environment="ORACLE_HOME=/opt/oracle/instantclient_19_23"
Environment="LD_LIBRARY_PATH=/opt/oracle/instantclient_19_23"
Environment="PYTHONPATH=/opt/cm3-batch-automations"
EnvironmentFile=/opt/cm3-batch-automations/.env

# For CLI mode (batch processing)
# ExecStart=/opt/cm3-batch-automations/venv/bin/python -m src.main

# For API mode (REST API server)
ExecStart=/opt/cm3-batch-automations/venv/bin/uvicorn src.api.main:app --host 0.0.0.0 --port 8000

Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/cm3-batch-automations/logs /opt/cm3-batch-automations/data /opt/cm3-batch-automations/uploads /opt/cm3-batch-automations/config/mappings

# Resource limits
LimitNOFILE=65536
MemoryLimit=2G
CPUQuota=200%

[Install]
WantedBy=multi-user.target
```

**3. Enable and start:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable valdo.service
sudo systemctl start valdo.service
sudo systemctl status valdo.service
```

---

## Option 5: Podman (Docker Alternative)

**Best for**: Container-like isolation without Docker daemon

### Advantages
- Rootless containers
- Docker-compatible
- No daemon required
- OCI compliant
- Systemd integration

### Why Podman?
- **Daemonless**: No background service
- **Rootless**: Run as regular user
- **Compatible**: Uses same Dockerfile
- **RHEL Native**: Officially supported by Red Hat

### Setup

**1. Install Podman:**
```bash
sudo yum install -y podman
```

**2. Build image:**
```bash
podman build -t valdo:latest .
```

**3. Run container:**
```bash
podman run -d \
  --name valdo \
  -v /opt/cm3-batch-automations/config:/app/config:ro \
  -v /opt/cm3-batch-automations/data:/app/data \
  -v /opt/cm3-batch-automations/logs:/app/logs \
  --env-file /opt/cm3-batch-automations/.env \
  valdo:latest
```

**4. Generate systemd service:**
```bash
podman generate systemd --new --name valdo > /etc/systemd/system/valdo-podman.service
sudo systemctl daemon-reload
sudo systemctl enable valdo-podman.service
sudo systemctl start valdo-podman.service
```

---

## Option 6: REST API Server (New!)

**Best for**: Web-based access, integration with other systems

### Advantages
- Interactive Swagger UI documentation
- Web-based file uploads
- Easy integration with frontends
- RESTful API for automation
- Real-time file processing

### Why REST API?
- **Modern Interface**: Web-based access from anywhere
- **Interactive Docs**: Swagger UI at `/docs`
- **Easy Integration**: Standard REST endpoints
- **File Uploads**: Web-based template and file uploads
- **Auto-Validation**: Pydantic models validate all requests

### Setup

**1. Install API dependencies:**
```bash
pip install -r requirements-api.txt
# Or manually:
pip install fastapi uvicorn[standard] python-multipart aiofiles pydantic pydantic-settings
```

**2. Start API server:**
```bash
# Development mode (auto-reload)
uvicorn src.api.main:app --reload --port 8000

# Production mode
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers 4
```

**3. Access Swagger UI:**
```bash
# Open in browser
http://localhost:8000/docs
```

**4. Create systemd service for API:**
```bash
sudo vim /etc/systemd/system/valdo-api.service
```

Add:
```ini
[Unit]
Description=Valdo REST API
After=network.target

[Service]
Type=simple
User=cm3app
Group=cm3app
WorkingDirectory=/opt/cm3-batch-automations
Environment="PATH=/opt/cm3-batch-automations/venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="ORACLE_HOME=/opt/oracle/instantclient_19_23"
Environment="LD_LIBRARY_PATH=/opt/oracle/instantclient_19_23"
EnvironmentFile=/opt/cm3-batch-automations/.env
ExecStart=/opt/cm3-batch-automations/venv/bin/uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers 4
Restart=on-failure
RestartSec=10

# Security
NoNewPrivileges=true
PrivateTmp=true
ReadWritePaths=/opt/cm3-batch-automations/logs /opt/cm3-batch-automations/uploads /opt/cm3-batch-automations/config/mappings

[Install]\nWantedBy=multi-user.target
```

**5. Enable and start:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable valdo-api.service
sudo systemctl start valdo-api.service
```

**6. Configure firewall:**
```bash
# Allow API port
sudo firewall-cmd --permanent --add-port=8000/tcp
sudo firewall-cmd --reload
```

### API Endpoints

- `GET /` - API information
- `GET /docs` - Swagger UI
- `GET /api/v1/system/health` - Health check
- `POST /api/v1/mappings/upload` - Upload template
- `GET /api/v1/mappings/` - List mappings
- `POST /api/v1/files/detect` - Detect file format
- `POST /api/v1/files/parse` - Parse file
- `POST /api/v1/files/compare` - Compare files

### Production Deployment

For production, use Gunicorn with Uvicorn workers:

```bash
pip install gunicorn

# Run with Gunicorn
gunicorn src.api.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

---

## Recommended Deployment Strategy

### For Your Environment (RHEL 8.9, No Docker)

**Primary Recommendation: Option 6 (REST API) + Option 4 (Systemd)**
- Deploy **REST API server** for modern web-based access
- Manage with **systemd service** (production-ready)
- Access via Swagger UI for testing and integration
- Best for teams and web-based workflows

**Alternative: Option 1 + Option 4 (Traditional CLI)**
- Deploy with **traditional venv** (easy maintenance)
- Manage with **systemd service** (production-ready)
- Best for batch processing and scheduled jobs

**Alternative: Option 3 (RPM)**
- If you have enterprise package management
- Best for multiple servers
- Centralized updates

**Alternative: Option 2 (PEX)**
- If you need single-file deployment
- Good for air-gapped environments
- Easy to version and rollback

**Alternative: Option 5 (Podman)**
- If you want container benefits without Docker
- Good isolation
- Rootless security

---

## Deployment Checklist

### Pre-Deployment
- [ ] RHEL 8.9 server provisioned
- [ ] Python 3.9+ installed
- [ ] Oracle Instant Client installed
- [ ] Application user created
- [ ] Firewall rules configured
- [ ] SELinux policies set

### Deployment
- [ ] Application code deployed
- [ ] Dependencies installed
- [ ] Configuration files in place
- [ ] Environment variables set
- [ ] Permissions configured
- [ ] Systemd service created

### Post-Deployment
- [ ] Service starts successfully
- [ ] Oracle connection works
- [ ] Logs are being written
- [ ] Monitoring configured
- [ ] Backup strategy in place
- [ ] Documentation updated

---

## Migration Between Options

### From venv to PEX
```bash
# Build PEX
./build_pex.sh

# Update systemd service
sudo vim /etc/systemd/system/valdo.service
# Change ExecStart to: /opt/cm3-batch-automations/valdo.pex

sudo systemctl daemon-reload
sudo systemctl restart valdo.service
```

### From venv to RPM
```bash
# Build RPM
./build_rpm.sh

# Stop current service
sudo systemctl stop valdo.service

# Install RPM
sudo yum install -y dist/cm3-batch-automations-*.rpm

# Migrate config
sudo cp /opt/cm3-batch-automations/.env /etc/valdo/

# Start new service
sudo systemctl start valdo
```

### From venv to Podman
```bash
# Build image
podman build -t valdo:latest .

# Stop current service
sudo systemctl stop valdo.service

# Run with Podman
podman run -d --name valdo \
  -v /opt/cm3-batch-automations/config:/app/config:ro \
  -v /opt/cm3-batch-automations/data:/app/data \
  -v /opt/cm3-batch-automations/logs:/app/logs \
  --env-file /opt/cm3-batch-automations/.env \
  valdo:latest

# Generate systemd service
podman generate systemd --new --name valdo > /etc/systemd/system/valdo-podman.service
sudo systemctl daemon-reload
sudo systemctl enable valdo-podman.service
```

---

## Troubleshooting

### Service Won't Start
```bash
# Check service status
sudo systemctl status valdo.service

# View logs
sudo journalctl -u valdo.service -xe

# Test manual start
sudo su - cm3app
cd /opt/cm3-batch-automations
source venv/bin/activate  # if using venv
python -m src.main
```

### Oracle Connection Issues
```bash
# Verify Oracle Instant Client
ls -la /opt/oracle/instantclient_19_23
ldconfig -p | grep oracle

# Test connection
python -c "import cx_Oracle; print(cx_Oracle.clientversion())"

# Check environment
echo $ORACLE_HOME
echo $LD_LIBRARY_PATH
```

### Permission Issues
```bash
# Fix ownership
sudo chown -R cm3app:cm3app /opt/cm3-batch-automations

# Fix permissions
sudo chmod -R 755 /opt/cm3-batch-automations
sudo chmod 600 /opt/cm3-batch-automations/.env

# Check SELinux
sudo ausearch -m avc -ts recent
```

---

## Security Best Practices

### 1. Dedicated User
```bash
sudo useradd -r -s /bin/false -d /opt/cm3-batch-automations cm3app
```

### 2. File Permissions
```bash
sudo chmod 600 /opt/cm3-batch-automations/.env
sudo chmod 755 /opt/cm3-batch-automations
sudo chmod -R 750 /opt/cm3-batch-automations/config
```

### 3. SELinux
```bash
sudo semanage fcontext -a -t bin_t "/opt/cm3-batch-automations/venv/bin(/.*)?"
sudo restorecon -Rv /opt/cm3-batch-automations
```

### 4. Firewall
```bash
# Only open required ports
sudo firewall-cmd --permanent --add-port=1521/tcp  # Oracle
sudo firewall-cmd --reload
```

### 5. Secrets Management
- Use HashiCorp Vault for production
- Or Red Hat Ansible Vault
- Never commit .env to git

---

## Monitoring and Maintenance

### Health Checks
```bash
# Service status
sudo systemctl status valdo.service

# Resource usage
top -u cm3app

# Disk usage
df -h /opt/cm3-batch-automations

# Log size
du -sh /opt/cm3-batch-automations/logs
```

### Log Rotation
```bash
sudo vim /etc/logrotate.d/valdo
```

```
/opt/cm3-batch-automations/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    notifempty
    create 0640 cm3app cm3app
    sharedscripts
    postrotate
        systemctl reload valdo.service > /dev/null 2>&1 || true
    endscript
}
```

### Backup
```bash
# Backup script
sudo tar -czf /backup/valdo-$(date +%Y%m%d).tar.gz \
    /opt/cm3-batch-automations/config \
    /opt/cm3-batch-automations/.env \
    /opt/cm3-batch-automations/data
```

---

## Summary

**Without Docker, your best options are:**

1. **Traditional venv + systemd** (Recommended)
   - Easiest to maintain
   - Standard Python workflow
   - Production-ready with systemd

2. **RPM Package**
   - Enterprise-grade
   - Centralized management
   - Best for multiple servers

3. **PEX**
   - Single-file deployment
   - Easy versioning
   - Good for air-gapped environments

4. **Podman** (if containers are acceptable)
   - Docker alternative
   - Rootless and daemonless
   - RHEL native

All options work perfectly on RHEL 8.9 without Docker!
