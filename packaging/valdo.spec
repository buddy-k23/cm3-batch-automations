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

Features:
- File parsing (pipe-delimited, fixed-width)
- Oracle database connectivity
- Data validation and comparison
- HTML report generation
- Configurable for multiple environments

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
mkdir -p %{buildroot}/var/lib/valdo/data
mkdir -p %{buildroot}/var/lib/valdo/reports
mkdir -p %{buildroot}%{_unitdir}
mkdir -p %{buildroot}%{_bindir}

# Copy application files
cp -r src %{buildroot}/opt/valdo/
cp -r tests %{buildroot}/opt/valdo/
cp -r docs %{buildroot}/opt/valdo/
cp requirements.txt %{buildroot}/opt/valdo/
cp setup.py %{buildroot}/opt/valdo/
cp README.md %{buildroot}/opt/valdo/
cp pytest.ini %{buildroot}/opt/valdo/
cp .flake8 %{buildroot}/opt/valdo/

# Copy configuration
cp -r config/* %{buildroot}/etc/valdo/
cp .env.example %{buildroot}/etc/valdo/

# Create systemd service file
cat > %{buildroot}%{_unitdir}/valdo.service << 'EOF'
[Unit]
Description=Valdo
After=network.target

[Service]
Type=simple
User=valdo
Group=valdo
WorkingDirectory=/opt/valdo
Environment="PATH=/usr/local/bin:/usr/bin:/bin"
Environment="ORACLE_HOME=/opt/oracle/instantclient_19_23"
Environment="LD_LIBRARY_PATH=/opt/oracle/instantclient_19_23"
EnvironmentFile=-/etc/valdo/.env
ExecStart=/usr/bin/python3.9 -m src.main
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

# Security
NoNewPrivileges=true
PrivateTmp=true

# Resource limits
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

# Create command-line wrapper
cat > %{buildroot}%{_bindir}/valdo << 'EOF'
#!/bin/bash
cd /opt/valdo
[ -f /etc/valdo/.env ] && source /etc/valdo/.env
export ORACLE_HOME=${ORACLE_HOME:-/opt/oracle/instantclient_19_23}
export LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-$ORACLE_HOME}
exec /usr/bin/python3.9 -m src.main "$@"
EOF
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
/usr/bin/python3.9 -m pip install --quiet --user -r requirements.txt 2>/dev/null || true

# Set permissions
chown -R valdo:valdo /opt/valdo
chown -R valdo:valdo /var/log/valdo
chown -R valdo:valdo /var/lib/valdo
chmod 750 /etc/valdo
if [ -f /etc/valdo/.env ]; then
    chmod 600 /etc/valdo/.env
fi
chmod 600 /etc/valdo/.env.example

# Reload systemd
systemctl daemon-reload >/dev/null 2>&1 || true

cat << 'POSTEOF'

========================================
Valdo installed!
========================================

Next steps:

1. Install Oracle Instant Client:
   See /opt/valdo/docs/RHEL_DEPLOYMENT.md

2. Configure application:
   sudo cp /etc/valdo/.env.example /etc/valdo/.env
   sudo vim /etc/valdo/.env
   sudo chmod 600 /etc/valdo/.env

3. Start service:
   sudo systemctl enable valdo
   sudo systemctl start valdo

4. Check status:
   sudo systemctl status valdo

Documentation: /opt/valdo/docs/
========================================

POSTEOF

%preun
if [ $1 -eq 0 ]; then
    # Uninstall
    systemctl stop valdo.service 2>/dev/null || true
    systemctl disable valdo.service 2>/dev/null || true
fi

%postun
if [ $1 -eq 0 ]; then
    # Uninstall - cleanup
    systemctl daemon-reload >/dev/null 2>&1 || true
    echo "Valdo removed."
    echo "Configuration preserved in /etc/valdo/"
    echo "To remove completely: sudo rm -rf /etc/valdo"
fi

%files
%defattr(-,root,root,-)
%doc README.md
%doc docs/
/opt/valdo/
%dir %attr(0750,root,valdo) /etc/valdo
%config(noreplace) %attr(0640,root,valdo) /etc/valdo/*.json
%config(noreplace) %attr(0600,root,valdo) /etc/valdo/.env.example
%dir %attr(0755,valdo,valdo) /var/log/valdo
%dir %attr(0755,valdo,valdo) /var/lib/valdo
%dir %attr(0755,valdo,valdo) /var/lib/valdo/data
%dir %attr(0755,valdo,valdo) /var/lib/valdo/reports
%{_unitdir}/valdo.service
%attr(0755,root,root) %{_bindir}/valdo

%changelog
* Thu Feb 06 2026 Development Team <dev@example.com> - 0.1.0-1
- Initial RPM release
- Core modules implemented:
  * File parsers (pipe-delimited, fixed-width)
  * Oracle database connectivity
  * Data validators and comparators
  * HTML report generation
  * Configuration management
- Systemd service integration
- RHEL 8.9 compatible
- Comprehensive documentation included
