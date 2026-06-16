# RHEL 8.9 Deployment Guide

## Prerequisites

- RHEL 8.9 server with root or sudo access
- Python 3.11 (the canonical Valdo runtime — see
  docs/PRODUCTION_DEPLOYMENT.md, "Canonical production topology (S14-2)")
- Internet connectivity for downloading Oracle Instant Client

## Installation Steps

### 1. Install System Dependencies

```bash
# Update system
sudo yum update -y

# Install Python 3.11 and development tools (python3.11 ships in the
# RHEL 8/9 AppStream repository — the canonical Valdo runtime)
sudo yum install -y python3.11 python3.11-devel python3.11-pip

# Install required system libraries
sudo yum install -y gcc make wget unzip libaio
```

### 2. Install Oracle Instant Client

```bash
# Download Oracle Instant Client 19.23 for RHEL 8
cd /tmp
wget https://download.oracle.com/otn_software/linux/instantclient/1923000/instantclient-basic-linux.x64-19.23.0.0.0dbru.zip

# Extract to /opt/oracle
sudo mkdir -p /opt/oracle
sudo unzip instantclient-basic-linux.x64-19.23.0.0.0dbru.zip -d /opt/oracle

# Configure library path
sudo sh -c "echo /opt/oracle/instantclient_19_23 > /etc/ld.so.conf.d/oracle-instantclient.conf"
sudo ldconfig

# Set environment variables
echo 'export ORACLE_HOME=/opt/oracle/instantclient_19_23' | sudo tee -a /etc/profile.d/oracle.sh
echo 'export LD_LIBRARY_PATH=$ORACLE_HOME:$LD_LIBRARY_PATH' | sudo tee -a /etc/profile.d/oracle.sh
echo 'export PATH=$ORACLE_HOME:$PATH' | sudo tee -a /etc/profile.d/oracle.sh
sudo chmod +x /etc/profile.d/oracle.sh

# Load environment variables
source /etc/profile.d/oracle.sh
```

### 3. Create Application User

```bash
# Create dedicated user for the application
sudo useradd -m -s /bin/bash valdo

# Create application directory
sudo mkdir -p /opt/valdo
sudo chown valdo:valdo /opt/valdo
```

### 4. Deploy Application

```bash
# Switch to application user
sudo su - valdo

# Clone repository
cd /opt/valdo
git clone <repository-url> .

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt

# For API mode, also install API dependencies
pip install -r requirements-api.txt
```

### 5. Configure Application

```bash
# Copy environment template
cp .env.example .env

# Edit environment variables
vim .env
# Set:
# ORACLE_USER=your_username
# ORACLE_PASSWORD=your_password
# ORACLE_DSN=hostname:port/service_name

# Secure the .env file
chmod 600 .env

# Create necessary directories
mkdir -p logs data/samples data/mappings reports uploads config/mappings
```

### 6. Verify Installation

```bash
# Test Python imports
python -c "import cx_Oracle; print('cx_Oracle version:', cx_Oracle.version)"

# Run tests
pytest -v
```

## Running as a Service (systemd)

### Create systemd Service File

```bash
sudo vim /etc/systemd/system/valdo.service
```

Add the following content:

```ini
[Unit]
Description=Valdo Service
After=network.target

[Service]
Type=simple
User=valdo
Group=valdo
WorkingDirectory=/opt/valdo
Environment="PATH=/opt/valdo/venv/bin:/opt/oracle/instantclient_19_23:/usr/local/bin:/usr/bin:/bin"
Environment="ORACLE_HOME=/opt/oracle/instantclient_19_23"
Environment="LD_LIBRARY_PATH=/opt/oracle/instantclient_19_23"
Environment="PYTHONPATH=/opt/valdo"

# For CLI mode (batch processing)
# ExecStart=/opt/valdo/venv/bin/python -m src.main

# For API mode (REST API server) - Recommended!
ExecStart=/opt/valdo/venv/bin/uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers 4

Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Enable and Start Service

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable service to start on boot
sudo systemctl enable valdo.service

# Start service
sudo systemctl start valdo.service

# Check status
sudo systemctl status valdo.service

# View logs
sudo journalctl -u valdo.service -f
```

## Firewall Configuration

If you need to access the application remotely:

```bash
# Allow Oracle database port (example: 1521)
sudo firewall-cmd --permanent --add-port=1521/tcp

# Allow API port (8000) for REST API access
sudo firewall-cmd --permanent --add-port=8000/tcp

# Reload firewall
sudo firewall-cmd --reload
```

## Accessing the API

```bash
# Check API health
curl http://localhost:8000/api/v1/system/health

# Access Swagger UI
open http://localhost:8000/docs

# Or from remote machine
open http://server-ip:8000/docs
```

## SELinux Configuration

If SELinux is enforcing:

```bash
# Check SELinux status
getenforce

# If needed, allow Python to connect to network
sudo setsebool -P httpd_can_network_connect 1

# Allow Python to read/write to application directories
sudo semanage fcontext -a -t bin_t "/opt/valdo/venv/bin(/.*)?"
sudo restorecon -Rv /opt/valdo
```

## Log Rotation

Create log rotation configuration:

```bash
sudo vim /etc/logrotate.d/valdo
```

Add:

```
/opt/valdo/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    notifempty
    create 0640 valdo valdo
    sharedscripts
    postrotate
        systemctl reload valdo.service > /dev/null 2>&1 || true
    endscript
}
```

## Monitoring

### Check Application Health

```bash
# Check service status
sudo systemctl status valdo.service

# View recent logs
sudo journalctl -u valdo.service -n 100

# Check application logs
tail -f /opt/valdo/logs/*.log
```

### Resource Monitoring

```bash
# Monitor CPU and memory usage
top -u valdo

# Check disk usage
df -h /opt/valdo
```

## Backup and Maintenance

### Backup Configuration

```bash
# Backup configuration files
sudo tar -czf /backup/valdo-config-$(date +%Y%m%d).tar.gz \
    /opt/valdo/config \
    /opt/valdo/.env
```

### Update Application

```bash
# Switch to application user
sudo su - valdo
cd /opt/valdo

# Activate virtual environment
source venv/bin/activate

# Pull latest changes
git pull origin main

# Update dependencies
pip install -r requirements.txt --upgrade

# Restart service
sudo systemctl restart valdo.service
```

## Troubleshooting

### Oracle Client Issues

```bash
# Verify Oracle Instant Client installation
ls -la /opt/oracle/instantclient_19_23

# Check library path
ldconfig -p | grep oracle

# Test Oracle connection
python -c "import cx_Oracle; print(cx_Oracle.clientversion())"
```

### Permission Issues

```bash
# Fix ownership
sudo chown -R valdo:valdo /opt/valdo

# Fix permissions
sudo chmod -R 755 /opt/valdo
sudo chmod 600 /opt/valdo/.env
```

### Service Won't Start

```bash
# Check service logs
sudo journalctl -u valdo.service -xe

# Verify Python path
which python

# Test manual start
sudo su - valdo
cd /opt/valdo
source venv/bin/activate
python -m src.main
```

## Security Best Practices

1. **Keep system updated**:
   ```bash
   sudo yum update -y
   ```

2. **Secure credentials**:
   - Never commit `.env` file to version control
   - Use restrictive file permissions (600) for `.env`
   - Consider using HashiCorp Vault or similar for secrets management

3. **Regular backups**:
   - Schedule automated backups of configuration and data
   - Test restore procedures regularly

4. **Monitor logs**:
   - Set up log aggregation (e.g., ELK stack, Splunk)
   - Configure alerts for errors and anomalies

5. **Network security**:
   - Use firewall rules to restrict access
   - Enable SELinux in enforcing mode
   - Use VPN or SSH tunnels for remote access
