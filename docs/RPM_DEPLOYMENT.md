# RPM Package Deployment Guide

## Overview

This guide explains how to build and deploy Valdo as an RPM package for RHEL 8.9.

## Why RPM?

- **Native Package Management**: Use yum/dnf for installation
- **Dependency Management**: Automatic dependency resolution
- **Version Control**: Easy upgrades and rollbacks
- **Enterprise Standard**: Standard for RHEL environments
- **Signed Packages**: Security and authenticity
- **Centralized Distribution**: Use internal yum repositories

## Prerequisites

- RHEL 8.9 build server
- `rpm-build` package installed
- `rpmdevtools` package installed

## Setup Build Environment

```bash
# Install build tools
sudo yum install -y rpm-build rpmdevtools

# Create RPM build directory structure
rpmdev-setuptree

# This creates:
# ~/rpmbuild/
# ├── BUILD/
# ├── RPMS/
# ├── SOURCES/
# ├── SPECS/
# └── SRPMS/
```

## Build RPM Package

### Using Build Script

```bash
./build_rpm.sh
```

### Manual Build

```bash
# Create source tarball
tar -czf ~/rpmbuild/SOURCES/valdo-0.1.0.tar.gz \
    --transform 's,^,valdo-0.1.0/,' \
    --exclude='.git' \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    .

# Copy spec file
cp packaging/valdo.spec ~/rpmbuild/SPECS/

# Build RPM
rpmbuild -ba ~/rpmbuild/SPECS/valdo.spec

# RPM will be created at:
# ~/rpmbuild/RPMS/noarch/valdo-0.1.0-1.el8.noarch.rpm
```

## RPM Package Contents

```
/opt/valdo/
├── src/                    # Application code
├── requirements.txt
├── setup.py
└── README.md

/etc/valdo/
├── config.json             # Configuration
└── .env.example            # Environment template

/var/log/valdo/         # Log directory
/var/lib/valdo/         # Data directory

/usr/lib/systemd/system/
└── valdo.service       # Systemd service

/usr/bin/
└── valdo               # Command-line wrapper
```

## Installation

### Install from Local File

```bash
sudo yum install -y ~/rpmbuild/RPMS/noarch/valdo-0.1.0-1.el8.noarch.rpm
```

### Install from Repository

```bash
# Add repository (if using internal repo)
sudo yum-config-manager --add-repo http://your-repo/valdo.repo

# Install
sudo yum install -y valdo
```

## Post-Installation Configuration

### 1. Configure Environment

```bash
# Copy environment template
sudo cp /etc/valdo/.env.example /etc/valdo/.env

# Edit configuration
sudo vim /etc/valdo/.env
```

Set:
```bash
ORACLE_USER=your_username
ORACLE_PASSWORD=your_password
ORACLE_DSN=hostname:port/service_name
ORACLE_HOME=/opt/oracle/instantclient_19_23
LD_LIBRARY_PATH=/opt/oracle/instantclient_19_23
```

### 2. Secure Configuration

```bash
sudo chmod 600 /etc/valdo/.env
sudo chown valdo:valdo /etc/valdo/.env
```

### 3. Install Oracle Instant Client

```bash
# Download and install Oracle Instant Client
cd /tmp
wget https://download.oracle.com/otn_software/linux/instantclient/1923000/instantclient-basic-linux.x64-19.23.0.0.0dbru.zip
sudo mkdir -p /opt/oracle
sudo unzip instantclient-basic-linux.x64-19.23.0.0.0dbru.zip -d /opt/oracle

# Configure library path
sudo sh -c "echo /opt/oracle/instantclient_19_23 > /etc/ld.so.conf.d/oracle-instantclient.conf"
sudo ldconfig
```

### 4. Start Service

```bash
# Enable service
sudo systemctl enable valdo.service

# Start service
sudo systemctl start valdo.service

# Check status
sudo systemctl status valdo.service
```

## RPM Management

### Query Package Information

```bash
# List files in package
rpm -ql valdo

# Show package info
rpm -qi valdo

# Show package dependencies
rpm -qR valdo

# Verify package integrity
rpm -V valdo
```

### Upgrade Package

```bash
# Upgrade to new version
sudo yum upgrade valdo-0.2.0-1.el8.noarch.rpm

# Service will be restarted automatically
```

### Rollback

```bash
# Downgrade to previous version
sudo yum downgrade valdo-0.1.0-1.el8.noarch.rpm
```

### Uninstall

```bash
# Remove package
sudo yum remove valdo

# Configuration files in /etc/valdo/ are preserved
# To remove completely:
sudo rm -rf /etc/valdo
```

## Creating Internal Repository

### Setup Yum Repository

```bash
# On repository server
sudo yum install -y createrepo

# Create repository directory
sudo mkdir -p /var/www/html/repos/valdo/el8/x86_64

# Copy RPM files
sudo cp ~/rpmbuild/RPMS/noarch/*.rpm /var/www/html/repos/valdo/el8/x86_64/

# Create repository metadata
sudo createrepo /var/www/html/repos/valdo/el8/x86_64/

# Update metadata when adding new RPMs
sudo createrepo --update /var/www/html/repos/valdo/el8/x86_64/
```

### Configure Clients

On client servers, create `/etc/yum.repos.d/valdo.repo`:

```ini
[valdo]
name=Valdo Repository
baseurl=http://your-repo-server/repos/valdo/el8/x86_64/
enabled=1
gpgcheck=0
```

Then install:
```bash
sudo yum install -y valdo
```

## Signing RPM Packages

### Generate GPG Key

```bash
# Generate key
gpg --gen-key

# Export public key
gpg --export -a 'Your Name' > RPM-GPG-KEY-valdo

# Import to RPM
sudo rpm --import RPM-GPG-KEY-valdo
```

### Sign Package

```bash
# Add to ~/.rpmmacros
echo "%_signature gpg" >> ~/.rpmmacros
echo "%_gpg_name Your Name" >> ~/.rpmmacros

# Sign RPM
rpm --addsign ~/rpmbuild/RPMS/noarch/valdo-0.1.0-1.el8.noarch.rpm

# Verify signature
rpm --checksig ~/rpmbuild/RPMS/noarch/valdo-0.1.0-1.el8.noarch.rpm
```

## Automated Builds with CI/CD

### GitLab CI Example

```yaml
build-rpm:
  stage: build
  image: registry.access.redhat.com/ubi8/ubi:latest
  before_script:
    - yum install -y rpm-build rpmdevtools
    - rpmdev-setuptree
  script:
    - ./build_rpm.sh
  artifacts:
    paths:
      - rpmbuild/RPMS/noarch/*.rpm
    expire_in: 30 days

deploy-to-repo:
  stage: deploy
  script:
    - scp rpmbuild/RPMS/noarch/*.rpm repo-server:/var/www/html/repos/valdo/el8/x86_64/
    - ssh repo-server "createrepo --update /var/www/html/repos/valdo/el8/x86_64/"
  only:
    - main
```

## Troubleshooting

### Build Failures

```bash
# Check build log
cat ~/rpmbuild/BUILD/valdo-0.1.0/build.log

# Verify spec file
rpmlint ~/rpmbuild/SPECS/valdo.spec

# Check dependencies
rpm -qpR ~/rpmbuild/RPMS/noarch/valdo-0.1.0-1.el8.noarch.rpm
```

### Installation Issues

```bash
# Check dependencies
sudo yum deplist valdo

# Force reinstall
sudo yum reinstall valdo

# Check file conflicts
rpm -qp --conflicts ~/rpmbuild/RPMS/noarch/valdo-0.1.0-1.el8.noarch.rpm
```

### Service Issues

```bash
# Check service status
sudo systemctl status valdo.service

# View logs
sudo journalctl -u valdo.service -xe

# Verify installation
rpm -V valdo
```

## Best Practices

1. **Version Numbering**: Use semantic versioning (MAJOR.MINOR.PATCH)
2. **Changelog**: Maintain detailed changelog in spec file
3. **Dependencies**: Declare all dependencies in spec file
4. **Testing**: Test RPM on clean system before distribution
5. **Signing**: Always sign production RPMs
6. **Repository**: Use internal repository for distribution
7. **Backup**: Backup configuration before upgrades
8. **Documentation**: Include README in package

## Advantages of RPM Deployment

- ✅ Native RHEL package management
- ✅ Automatic dependency resolution
- ✅ Easy upgrades and rollbacks
- ✅ Centralized distribution
- ✅ Version control
- ✅ Signed packages for security
- ✅ Standard enterprise approach
- ✅ Integration with existing infrastructure

## Summary

RPM deployment is ideal for enterprise RHEL environments where:
- You manage multiple servers
- You need centralized package management
- You want easy upgrades and rollbacks
- You follow enterprise standards
- You have internal yum repositories

For single-server deployments or development, consider using traditional venv or PEX instead.
