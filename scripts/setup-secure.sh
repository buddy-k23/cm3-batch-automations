#!/bin/bash
# =============================================================================
# Valdo — Secure Server Setup Script
# =============================================================================
# Sets up Nginx as a reverse proxy with:
#   - Internal corporate SSL certificate (no browser warning)
#   - Custom hostname via /etc/hosts
#   - IP allowlist (corporate network only)
#   - API key security
#   - Valdo running as a systemd service
#
# Usage:
#   sudo bash scripts/setup-secure.sh
#
# Prerequisites:
#   - Run from the valdo project root directory
#   - Must be run as root or with sudo
#   - Nginx must be installable (dnf/yum available)
#   - Valdo already deployed and venv created (run setup-linux.sh first)
#
# Configuration — edit these variables before running:
# =============================================================================

VALDO_HOME="/app/software/APPS/valdo"
VALDO_PORT="8000"                              # internal uvicorn port (not exposed)
NGINX_PORT="443"                               # HTTPS port for browser access
APP_NAME="valdo"                               # custom hostname (access via https://valdo)
APP_USER=$(stat -c '%U' "$VALDO_HOME")         # owner of the valdo directory
ALLOWED_NETWORK="10.0.0.0/8"                  # Full corporate network (covers all 10.x.x.x subnets)
CERT_DIR="/etc/ssl/valdo"
CERT_DAYS=3650                                 # 10 years

# Corporate SSL — set these if your org has an internal CA
# Leave blank to use a self-signed cert (still avoids "Not Secure" if you
# import the cert into your browser/corporate trust store)
CORP_CERT=""   # e.g. /path/to/corp-signed.crt
CORP_KEY=""    # e.g. /path/to/corp-signed.key

# =============================================================================

set -e
trap 'echo ""; echo "ERROR: Setup failed at line $LINENO." >&2' ERR

# --- Must run as root ---
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Please run with sudo: sudo bash scripts/setup-secure.sh"
    exit 1
fi

# --- Must run from project root ---
if [ ! -f "$VALDO_HOME/setup.py" ] && [ ! -f "$VALDO_HOME/pyproject.toml" ]; then
    echo "ERROR: VALDO_HOME ($VALDO_HOME) does not look like the valdo project root."
    exit 1
fi

echo ""
echo "============================================="
echo " Valdo — Secure Server Setup"
echo "============================================="
echo " App home:        $VALDO_HOME"
echo " App user:        $APP_USER"
echo " Custom hostname: $APP_NAME"
echo " Allowed network: $ALLOWED_NETWORK"
echo "============================================="
echo ""

# =============================================================================
# STEP 1 — Install Nginx
# =============================================================================
echo "[1/7] Installing Nginx ..."
if ! command -v nginx &>/dev/null; then
    dnf install -y nginx || yum install -y nginx
    echo "Nginx installed."
else
    echo "Nginx already installed, skipping."
fi

# =============================================================================
# STEP 2 — Generate or copy SSL certificate
# =============================================================================
echo ""
echo "[2/7] Configuring SSL certificate ..."
mkdir -p "$CERT_DIR"

if [ -n "$CORP_CERT" ] && [ -f "$CORP_CERT" ] && [ -n "$CORP_KEY" ] && [ -f "$CORP_KEY" ]; then
    echo "Using corporate-signed certificate from $CORP_CERT"
    cp "$CORP_CERT" "$CERT_DIR/valdo.crt"
    cp "$CORP_KEY"  "$CERT_DIR/valdo.key"
elif [ -f "$CERT_DIR/valdo.crt" ]; then
    echo "SSL certificate already exists — skipping generation."
else
    echo "Generating self-signed certificate (valid $CERT_DAYS days) ..."
    echo ""
    echo "  NOTE: To avoid the browser 'Not Secure' warning:"
    echo "  1. Copy $CERT_DIR/valdo.crt to your Windows machine"
    echo "  2. Double-click it → Install Certificate → Local Machine"
    echo "     → Place in 'Trusted Root Certification Authorities'"
    echo "  OR ask your sysadmin to sign it with the corporate CA."
    echo ""
    openssl req -x509 -nodes \
        -days "$CERT_DAYS" \
        -newkey rsa:2048 \
        -keyout "$CERT_DIR/valdo.key" \
        -out    "$CERT_DIR/valdo.crt" \
        -subj   "/CN=$APP_NAME/O=Enterprise/OU=Collections360/C=US" \
        -addext "subjectAltName=DNS:$APP_NAME,DNS:$(hostname),IP:$(hostname -I | awk '{print $1}')"
fi

chmod 600 "$CERT_DIR/valdo.key"
chmod 644 "$CERT_DIR/valdo.crt"
echo "Certificate ready at $CERT_DIR/"

# =============================================================================
# STEP 3 — Configure .env (idempotent — preserves existing API key on redeploy)
# =============================================================================
echo ""
echo "[3/7] Configuring .env ..."

ENV_FILE="$VALDO_HOME/.env"

if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$VALDO_HOME/.env.example" ]; then
        cp "$VALDO_HOME/.env.example" "$ENV_FILE"
        echo "Created .env from .env.example"
    else
        touch "$ENV_FILE"
        echo "Created empty .env"
    fi
fi

# Update or add a setting — preserves existing value if key already set
update_env() {
    local key="$1"
    local value="$2"
    if grep -q "^${key}=" "$ENV_FILE"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
        echo "${key}=${value}" >> "$ENV_FILE"
    fi
}

# Preserve existing API key on redeploy — only generate on first run
if grep -q "^API_KEYS=" "$ENV_FILE" && ! grep -q "^API_KEYS=key-dev-abc123$" "$ENV_FILE"; then
    echo "API key already set in .env — preserving existing key."
    API_KEY=$(grep "^API_KEYS=" "$ENV_FILE" | cut -d= -f2)
else
    API_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    update_env "API_KEYS" "$API_KEY"
    echo ""
    echo "  *** NEW API KEY GENERATED — save this ***"
    echo "  API_KEY: $API_KEY"
    echo ""
fi

update_env "ENABLE_FILE_DOWNLOADER" "true"
update_env "ALLOWED_ORIGINS"        "https://$APP_NAME,https://$(hostname)"
update_env "ENVIRONMENT"            "sit"
update_env "LOG_LEVEL"              "INFO"

echo ".env configured."

# =============================================================================
# STEP 4 — Configure Nginx
# =============================================================================
echo "[4/7] Configuring Nginx ..."

cat > /etc/nginx/conf.d/valdo.conf << EOF
# Valdo — Nginx reverse proxy configuration
# Generated by setup-secure.sh on $(date)

# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name $APP_NAME $(hostname);
    return 301 https://\$host\$request_uri;
}

# HTTPS server
server {
    listen $NGINX_PORT ssl;
    server_name $APP_NAME $(hostname);

    ssl_certificate     $CERT_DIR/valdo.crt;
    ssl_certificate_key $CERT_DIR/valdo.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # IP allowlist — only corporate network
    allow $ALLOWED_NETWORK;
    allow 127.0.0.1;
    deny all;

    # Security headers
    add_header X-Frame-Options        "SAMEORIGIN"   always;
    add_header X-Content-Type-Options "nosniff"      always;
    add_header X-XSS-Protection       "1; mode=block" always;

    # Proxy to uvicorn
    location / {
        proxy_pass         http://127.0.0.1:$VALDO_PORT;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }

    # WebSocket support (for live reload / watch features)
    location /ws {
        proxy_pass         http://127.0.0.1:$VALDO_PORT;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade    \$http_upgrade;
        proxy_set_header   Connection "upgrade";
    }
}
EOF

# Test Nginx config
nginx -t
echo "Nginx configured."

# =============================================================================
# STEP 5 — Add custom hostname to /etc/hosts
# =============================================================================
echo ""
echo "[5/7] Adding custom hostname '$APP_NAME' to /etc/hosts ..."

SERVER_IP=$(hostname -I | awk '{print $1}')

if grep -q "$APP_NAME" /etc/hosts; then
    echo "Hostname '$APP_NAME' already in /etc/hosts, skipping."
else
    echo "$SERVER_IP  $APP_NAME" >> /etc/hosts
    echo "Added: $SERVER_IP  $APP_NAME"
fi

echo ""
echo "  To access from your Windows machine, add this line to"
echo "  C:\\Windows\\System32\\drivers\\etc\\hosts (run Notepad as Administrator):"
echo ""
echo "  $SERVER_IP  $APP_NAME"
echo ""

# =============================================================================
# STEP 6 — Create systemd service for Valdo
# =============================================================================
echo "[6/7] Creating systemd service ..."

cat > /etc/systemd/system/valdo.service << EOF
[Unit]
Description=Valdo API Server
After=network.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$VALDO_HOME
EnvironmentFile=$VALDO_HOME/.env
ExecStart=$VALDO_HOME/venv/bin/uvicorn src.api.main:app --host 127.0.0.1 --port $VALDO_PORT
Restart=always
RestartSec=5
StandardOutput=append:$VALDO_HOME/logs/uvicorn.log
StandardError=append:$VALDO_HOME/logs/uvicorn.log

[Install]
WantedBy=multi-user.target
EOF

# Stop any manually started uvicorn
pkill -f "uvicorn src.api.main:app" 2>/dev/null || true
sleep 1

# Enable and start services
systemctl daemon-reload
systemctl enable valdo
systemctl start valdo
systemctl enable nginx
systemctl restart nginx

echo "Systemd service 'valdo' created and started."

# =============================================================================
# STEP 7 — Configure firewall
# =============================================================================
echo ""
echo "[7/7] Configuring firewall ..."

if command -v firewall-cmd &>/dev/null; then
    # Allow HTTPS (443) and HTTP (80 — redirects to HTTPS)
    firewall-cmd --permanent --add-service=https
    firewall-cmd --permanent --add-service=http
    # Block direct access to uvicorn port
    firewall-cmd --permanent --remove-port="${VALDO_PORT}/tcp" 2>/dev/null || true
    firewall-cmd --reload
    echo "Firewall updated — ports 80 and 443 open, port $VALDO_PORT blocked externally."
else
    echo "WARNING: firewall-cmd not found. Skipping firewall configuration."
    echo "Manually ensure port $VALDO_PORT is not exposed externally."
fi

# =============================================================================
# Summary
# =============================================================================
echo ""
echo "============================================="
echo " Setup complete!"
echo "============================================="
echo ""
echo " Service status:"
systemctl is-active valdo   && echo "  valdo:  running" || echo "  valdo:  STOPPED"
systemctl is-active nginx   && echo "  nginx:  running" || echo "  nginx:  STOPPED"
echo ""
echo " Access the app at:  https://$APP_NAME"
echo " Or via IP:          https://$SERVER_IP"
echo ""
echo " To avoid the browser 'Not Secure' warning:"
echo "   Option A (recommended): Ask your sysadmin to sign the cert with"
echo "            the corporate CA and re-run with CORP_CERT/CORP_KEY set."
echo "   Option B: Import $CERT_DIR/valdo.crt into your browser/OS"
echo "            trust store on each client machine."
echo "            Copy the cert:"
echo "            scp $APP_USER@$SERVER_IP:$CERT_DIR/valdo.crt ."
echo "            Then install it in Windows:"
echo "            certmgr.msc → Trusted Root Certification Authorities → Import"
echo ""
echo " Useful commands:"
echo "   sudo systemctl status valdo       # check service status"
echo "   sudo systemctl restart valdo      # restart after .env changes"
echo "   tail -f $VALDO_HOME/logs/uvicorn.log  # view logs"
echo ""
