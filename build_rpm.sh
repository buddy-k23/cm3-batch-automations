#!/bin/bash

# Build script for creating RPM package
# Usage: ./build_rpm.sh

set -e

echo "Building Valdo RPM..."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if rpm-build is installed
if ! command -v rpmbuild &> /dev/null; then
    echo -e "${RED}Error: rpmbuild is not installed${NC}"
    echo "Install with: sudo yum install -y rpm-build rpmdevtools"
    exit 1
fi

# Get version from setup.py
VERSION=$(grep "version=" setup.py | cut -d'"' -f2)
echo -e "${GREEN}Version: ${VERSION}${NC}"

# Setup RPM build directory
if [ ! -d "$HOME/rpmbuild" ]; then
    echo -e "${YELLOW}Setting up RPM build directory...${NC}"
    rpmdev-setuptree
fi

# Create source tarball
echo -e "${GREEN}Creating source tarball...${NC}"
tar -czf "$HOME/rpmbuild/SOURCES/valdo-${VERSION}.tar.gz" \
    --transform "s,^,valdo-${VERSION}/," \
    --exclude='.git' \
    --exclude='venv' \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='.pytest_cache' \
    --exclude='htmlcov' \
    --exclude='dist' \
    --exclude='build' \
    --exclude='*.egg-info' \
    --exclude='rpmbuild' \
    .

# Copy spec file
echo -e "${GREEN}Copying spec file...${NC}"
mkdir -p packaging
cp packaging/valdo.spec "$HOME/rpmbuild/SPECS/" 2>/dev/null || {
    echo -e "${YELLOW}Spec file not found, creating default...${NC}"
    # Create default spec file if it doesn't exist
    cat > "$HOME/rpmbuild/SPECS/valdo.spec" << 'EOF'
Name:           valdo
Version:        0.1.0
Release:        1%{?dist}
Summary:        Valdo - File parsing and validation tool

License:        Proprietary
URL:            https://gitlab.com/your-org/valdo
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
Requires:       python39 >= 3.9.0
Requires:       python39-pip

%description
Automated file parsing, validation, and comparison tool for valdo batch
processing with Oracle database integration.

%prep
%setup -q

%build
# Nothing to build for pure Python

%install
rm -rf %{buildroot}

# Create directories
mkdir -p %{buildroot}/opt/valdo
mkdir -p %{buildroot}/etc/valdo
mkdir -p %{buildroot}/var/log/valdo
mkdir -p %{buildroot}/var/lib/valdo
mkdir -p %{buildroot}%{_unitdir}
mkdir -p %{buildroot}%{_bindir}

# Copy application files
cp -r src %{buildroot}/opt/valdo/
cp -r tests %{buildroot}/opt/valdo/
cp requirements.txt %{buildroot}/opt/valdo/
cp setup.py %{buildroot}/opt/valdo/
cp README.md %{buildroot}/opt/valdo/
cp pytest.ini %{buildroot}/opt/valdo/
cp .flake8 %{buildroot}/opt/valdo/

# Copy configuration
cp -r config/* %{buildroot}/etc/valdo/
cp .env.example %{buildroot}/etc/valdo/

# Create systemd service file
cat > %{buildroot}%{_unitdir}/valdo.service << 'SERVICEEOF'
[Unit]
Description=Valdo
After=network.target

[Service]
Type=simple
User=valdo
Group=valdo
WorkingDirectory=/opt/valdo
EnvironmentFile=/etc/valdo/.env
ExecStart=/usr/bin/python3.9 -m src.main
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICEEOF

# Create command-line wrapper
cat > %{buildroot}%{_bindir}/valdo << 'WRAPPEREOF'
#!/bin/bash
cd /opt/valdo
source /etc/valdo/.env 2>/dev/null || true
exec /usr/bin/python3.9 -m src.main "$@"
WRAPPEREOF
chmod +x %{buildroot}%{_bindir}/valdo

%pre
# Create user if it doesn't exist
getent group valdo >/dev/null || groupadd -r valdo
getent passwd valdo >/dev/null || \
    useradd -r -g valdo -d /opt/valdo -s /sbin/nologin \
    -c "Valdo" valdo
exit 0

%post
# Install Python dependencies
cd /opt/valdo
/usr/bin/python3.9 -m pip install --user -r requirements.txt

# Set permissions
chown -R valdo:valdo /opt/valdo
chown -R valdo:valdo /var/log/valdo
chown -R valdo:valdo /var/lib/valdo
chmod 750 /etc/valdo
chmod 600 /etc/valdo/.env.example

# Reload systemd
systemctl daemon-reload

echo "Valdo installed successfully!"
echo "Next steps:"
echo "  1. Install Oracle Instant Client"
echo "  2. Configure /etc/valdo/.env"
echo "  3. Start service: systemctl start valdo"

%preun
if [ $1 -eq 0 ]; then
    # Uninstall
    systemctl stop valdo.service 2>/dev/null || true
    systemctl disable valdo.service 2>/dev/null || true
fi

%postun
if [ $1 -eq 0 ]; then
    # Uninstall
    systemctl daemon-reload
fi

%files
%defattr(-,root,root,-)
/opt/valdo/
%config(noreplace) /etc/valdo/
%attr(0755,valdo,valdo) /var/log/valdo
%attr(0755,valdo,valdo) /var/lib/valdo
%{_unitdir}/valdo.service
%attr(0755,root,root) %{_bindir}/valdo

%changelog
* Thu Feb 06 2026 Development Team <dev@example.com> - 0.1.0-1
- Initial RPM release
- Core modules: parsers, database, validators, comparators
- Configuration management
- HTML reporting
- Systemd service integration
EOF
}

# Build RPM
echo -e "${GREEN}Building RPM package...${NC}"
rpmbuild -ba "$HOME/rpmbuild/SPECS/valdo.spec"

# Find the built RPM
RPM_FILE=$(find "$HOME/rpmbuild/RPMS" -name "valdo-*.rpm" | head -1)

if [ -n "$RPM_FILE" ]; then
    FILE_SIZE=$(du -h "$RPM_FILE" | cut -f1)
    echo -e "${GREEN}✓ RPM build complete!${NC}"
    echo -e "Output: ${RPM_FILE} (${FILE_SIZE})"
    echo ""
    echo "To install:"
    echo "  sudo yum install -y ${RPM_FILE}"
    echo ""
    echo "See docs/RPM_DEPLOYMENT.md for detailed instructions"
else
    echo -e "${RED}Error: RPM file not found${NC}"
    exit 1
fi
