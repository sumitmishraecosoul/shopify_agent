#!/usr/bin/env bash
set -euo pipefail

# VPS-safe deployment script for the Shopify Assistant (FastAPI + Uvicorn + PM2 + Nginx + Certbot).
# Designed for multi-project VPS environments:
# - Does NOT run system upgrades or install global dependencies
# - Only touches the PM2 app named "shopify-agent" and a dedicated Nginx vhost
# - Runs Uvicorn as a PACKAGE module: shopify_assistant.main:app

# --- CONFIGURATION ---
SUBDOMAIN="shopify.thrivebrands.in"
PROJECT_ROOT="/home/clp/htdocs/shopify_agent"
APP_PKG_DIR="${PROJECT_ROOT}/shopify_assistant"
PORT="8010"

# Venv is inside the package directory in this project layout
VENV_DIR="${APP_PKG_DIR}/venv"
PYTHON_BIN="${VENV_DIR}/bin/python3"

NGINX_SITE_NAME="shopify_assistant"
NGINX_AVAILABLE="/etc/nginx/sites-available/${NGINX_SITE_NAME}"
# Many multi-site servers include only: /etc/nginx/sites-enabled/*.conf
NGINX_ENABLED="/etc/nginx/sites-enabled/${NGINX_SITE_NAME}.conf"

CERT_NAME="${SUBDOMAIN}"
CERT_DIR="/etc/letsencrypt/live/${CERT_NAME}"

echo "Starting deployment for ${SUBDOMAIN} on port ${PORT}"

echo "Preparing Python virtual environment..."
cd "${APP_PKG_DIR}"
if [ ! -d "${VENV_DIR}" ]; then
  python3 -m venv "${VENV_DIR}"
fi

"${PYTHON_BIN}" -m pip install --upgrade pip
if [ -f "requirements.txt" ]; then
  "${PYTHON_BIN}" -m pip install -r requirements.txt
fi

echo "Starting (or restarting) app with PM2..."
pm2 delete shopify-agent 2>/dev/null || true

cd "${PROJECT_ROOT}"
pm2 start "${PYTHON_BIN}" --name "shopify-agent" -- \
  -m uvicorn shopify_assistant.main:app \
  --host 127.0.0.1 \
  --port "${PORT}"

pm2 save
pm2 startup | bash || true

echo "Configuring Nginx reverse proxy..."
cat > "${NGINX_AVAILABLE}" <<EOF
server {
    listen 80;
    server_name ${SUBDOMAIN};

    location / {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF

ln -sf "${NGINX_AVAILABLE}" "${NGINX_ENABLED}"
nginx -t
systemctl reload nginx

echo "Ensuring a certificate exists for ${SUBDOMAIN}..."
if [ ! -d "${CERT_DIR}" ]; then
  echo "No cert found at ${CERT_DIR}. Requesting a new certificate..."
  certbot certonly --nginx -d "${SUBDOMAIN}"
fi

echo "Enabling HTTPS for ${SUBDOMAIN} (manual vhost, avoids certbot vhost matching issues)..."
cat >> "${NGINX_AVAILABLE}" <<EOF

server {
    listen 443 ssl;
    http2 on;
    server_name ${SUBDOMAIN};

    ssl_certificate ${CERT_DIR}/fullchain.pem;
    ssl_certificate_key ${CERT_DIR}/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
EOF

nginx -t
systemctl reload nginx

echo "Deployment complete."
pm2 status
