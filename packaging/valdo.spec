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
# S9-1 (#387): nginx reverse-proxy sample drop-in directory.
mkdir -p %{buildroot}/etc/nginx/conf.d

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

# S9-1 (#387): ship the nginx reverse-proxy config as a *.sample so it never
# overwrites a hand-tuned production conf. SRE activates it per
# docs/PRODUCTION_DEPLOYMENT.md ("## TLS + nginx (S9-1)").
cp packaging/nginx/valdo.conf %{buildroot}/etc/nginx/conf.d/valdo.conf.sample

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

# S10-2 (#397): background validation worker systemd unit. Separate from
# valdo.service (the gunicorn MCP server) — this process drains the
# APP_MCP_RUN_REGISTRY queue out of band (ADR 0021). It registers a liveness
# marker in MCP_WORKERS on start + each poll, which validate_file consults
# before taking the async enqueue path (else it falls back to a synchronous
# inline run). TimeoutStopSec is generous so a graceful SIGTERM lets the
# in-flight validation finish rather than being SIGKILL'd mid-run.
cat > %{buildroot}%{_unitdir}/valdo-run-job-worker.service << 'EOF'
[Unit]
Description=Valdo MCP background validation worker
After=network.target valdo.service

[Service]
Type=simple
User=valdo
Group=valdo
WorkingDirectory=/opt/valdo
Environment="PATH=/usr/local/bin:/usr/bin:/bin"
Environment="ORACLE_HOME=/opt/oracle/instantclient_19_23"
Environment="LD_LIBRARY_PATH=/opt/oracle/instantclient_19_23"
EnvironmentFile=-/etc/valdo/.env
ExecStart=/usr/bin/python3.9 -m src.main run-job-worker --poll-interval 2 --reap-multiple 10
Restart=on-failure
RestartSec=10
# systemd sends SIGTERM on stop; the worker finishes its in-flight job and
# exits 0. TimeoutStopSec MUST exceed the worst-case single-file validation
# time so a graceful stop is never SIGKILL'd mid-run (which would strand a row
# in 'running' until the reaper reclaims it).
TimeoutStopSec=120
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

# S10-2 (#397): enable the background worker so it starts on boot. It is only
# *started* once /etc/valdo/.env is configured (see next-steps below). The
# worker is harmless without async enabled — it simply finds an empty queue.
systemctl enable valdo-run-job-worker.service >/dev/null 2>&1 || true

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

5. (Async validation) Start the background worker:
   sudo systemctl start valdo-run-job-worker
   sudo systemctl status valdo-run-job-worker
   # validate_file is async ON by default; with the worker draining the
   # queue it enqueues and returns fast. If the worker is stopped, validate_file
   # falls back to a synchronous inline run, so nothing is ever stranded.

Documentation: /opt/valdo/docs/
========================================

POSTEOF

%preun
if [ $1 -eq 0 ]; then
    # Uninstall
    systemctl stop valdo.service 2>/dev/null || true
    systemctl disable valdo.service 2>/dev/null || true
    # S10-2 (#397): stop + disable the background worker too.
    systemctl stop valdo-run-job-worker.service 2>/dev/null || true
    systemctl disable valdo-run-job-worker.service 2>/dev/null || true
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
# S9-1 (#387): nginx reverse-proxy sample. *.sample (not the live conf) so
# rpm never disrupts a running nginx; noreplace preserves operator edits.
%config(noreplace) %attr(0644,root,root) /etc/nginx/conf.d/valdo.conf.sample
%dir %attr(0755,valdo,valdo) /var/log/valdo
%dir %attr(0755,valdo,valdo) /var/lib/valdo
%dir %attr(0755,valdo,valdo) /var/lib/valdo/data
%dir %attr(0755,valdo,valdo) /var/lib/valdo/reports
%{_unitdir}/valdo.service
# S10-2 (#397): background validation worker unit.
%{_unitdir}/valdo-run-job-worker.service
%attr(0755,root,root) %{_bindir}/valdo

%changelog
* Tue Jun 16 2026 Development Team <dev@example.com> - 0.1.0-3
- S10-2 (#397): ship the background validation worker as its own systemd unit
  (valdo-run-job-worker.service) — continuous poll/claim/run/reap with
  graceful SIGTERM (TimeoutStopSec=120). Enabled in %post, disabled in %preun.
  Pairs with async validate_file (ON by default) + the MCP_WORKERS liveness
  marker (Alembic 0007); validate_file falls back to a synchronous inline run
  when no worker is live. See docs/PRODUCTION_DEPLOYMENT.md.

* Mon Jun 16 2026 Development Team <dev@example.com> - 0.1.0-2
- S9-1 (#387): ship nginx reverse-proxy config to
  /etc/nginx/conf.d/valdo.conf.sample (TLS termination + X-Forwarded-*).
  See docs/PRODUCTION_DEPLOYMENT.md for activation.

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
