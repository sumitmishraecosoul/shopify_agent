# Deploy EcoSoul Backend on Hostinger VPS (FastAPI + Uvicorn)

This guide deploys the backend API on a Hostinger VPS (Ubuntu-style Linux) and runs it on **port 8010**.

## 0) Assumptions

- VPS OS: Ubuntu 22.04/24.04 (or similar)
- You have SSH access (`root` or a sudo user)
- You want the API available publicly (optionally behind Nginx + SSL)
- Backend port: **8010**

---

## 1) Server preparation (one-time)

SSH into VPS:

```bash
ssh root@<VPS_IP>
```

Update packages:

```bash
sudo apt update && sudo apt upgrade -y
```

Install required system packages:

```bash
sudo apt install -y python3 python3-venv python3-pip git curl
```

Optional (recommended) if you’ll use Nginx:

```bash
sudo apt install -y nginx
```

---

## 2) Copy the project to VPS

### Option A: Git clone (recommended)

```bash
cd /opt
sudo git clone <YOUR_REPO_URL> ecosoul_chatbot
sudo chown -R $USER:$USER /opt/ecosoul_chatbot
cd /opt/ecosoul_chatbot
```

### Option B: Upload files (SCP)

From your local machine:

```bash
scp -r "C:\Users\Sumit Mishra\Documents\ecosoul_chatbot" root@<VPS_IP>:/opt/ecosoul_chatbot
```

---

## 3) Create venv + install dependencies

```bash
cd /opt/ecosoul_chatbot
python3 -m venv shopify_assistant/venv
source shopify_assistant/venv/bin/activate
pip install --upgrade pip
pip install -r shopify_assistant/requirements.txt
```

---

## 4) Configure environment variables (`shopify_assistant/.env`)

Create/update:

```bash
nano /opt/ecosoul_chatbot/shopify_assistant/.env
```

Minimum required fields for “live” setup:

```env
PORT=8010

# LLM
LLM_BASE_URL=http://<YOUR_LLM_HOST>:11435
LLM_MODEL_NAME=qwen2.5:32b-instruct

# Shopify domain (used for cart permalink)
SHOPIFY_STORE_DOMAIN=https://<your-store-domain>

# Azure inventory pull (connection-string method)
AZURE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net
AZURE_CONTAINER_NAME=thrive-client-ecosoulhome
AZURE_BLOB_PATH_TEMPLATE=data_dump/inventory/shopify/{YYYY}/{MM}/{DD}/us_shopify_inventory.json

# Auth for /api/v1/*
API_AUTH_USERNAME=shopify_client_app
API_AUTH_PASSWORD=<set-strong-password>

# Service token for pipeline refresh trigger (set a long random token)
INVENTORY_REFRESH_SERVICE_TOKEN=<set-long-random-token>
```

Important:
- Do **not** commit `.env` to git.
- Use strong secrets for `API_AUTH_PASSWORD` and `INVENTORY_REFRESH_SERVICE_TOKEN`.

---

## 5) Run backend manually (quick test)

```bash
cd /opt/ecosoul_chatbot
source shopify_assistant/venv/bin/activate
python -m uvicorn shopify_assistant.main:app --host 0.0.0.0 --port 8010
```

Test from VPS:

```bash
curl -i http://127.0.0.1:8010/health
```

From your local machine:

- Swagger: `http://<VPS_IP>:8010/docs`
- Health: `http://<VPS_IP>:8010/health`

Stop server with `CTRL+C` and proceed to systemd service.

---

## 6) Run backend as a systemd service (recommended)

Create service file:

```bash
sudo nano /etc/systemd/system/ecosoul-backend.service
```

Paste:

```ini
[Unit]
Description=EcoSoul Backend (FastAPI)
After=network.target

[Service]
WorkingDirectory=/opt/ecosoul_chatbot
EnvironmentFile=/opt/ecosoul_chatbot/shopify_assistant/.env
ExecStart=/opt/ecosoul_chatbot/shopify_assistant/venv/bin/python -m uvicorn shopify_assistant.main:app --host 0.0.0.0 --port 8010
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Enable + start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable ecosoul-backend
sudo systemctl start ecosoul-backend
sudo systemctl status ecosoul-backend --no-pager
```

Logs:

```bash
sudo journalctl -u ecosoul-backend -f
```

---

## 7) Open firewall (if needed)

If UFW is enabled:

```bash
sudo ufw allow 8010/tcp
sudo ufw reload
sudo ufw status
```

---

## 8) (Optional) Nginx reverse proxy + SSL

### A) Reverse proxy to 8010

Create site config:

```bash
sudo nano /etc/nginx/sites-available/ecosoul-backend
```

Example:

```nginx
server {
  listen 80;
  server_name <YOUR_DOMAIN>;

  location / {
    proxy_pass http://127.0.0.1:8010;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
```

Enable config:

```bash
sudo ln -s /etc/nginx/sites-available/ecosoul-backend /etc/nginx/sites-enabled/ecosoul-backend
sudo nginx -t
sudo systemctl reload nginx
```

### B) SSL with Let’s Encrypt (Certbot)

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d <YOUR_DOMAIN>
```

Then you will have:
- Swagger: `https://<YOUR_DOMAIN>/docs`

---

## 9) Verify inventory refresh trigger (pipeline integration)

### Option A: Service token (recommended for scripts)

```bash
curl -s -X POST "http://127.0.0.1:8010/api/v1/inventory/refresh" \
  -H "Authorization: Bearer <INVENTORY_REFRESH_SERVICE_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"date":"2026-04-30","force":false}'
```

### Option B: Login token

1) Login:

```bash
curl -s -X POST "http://127.0.0.1:8010/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"shopify_client_app","password":"<API_AUTH_PASSWORD>"}'
```

2) Call refresh with returned `access_token`.

---

## 10) Update deployment

If using git:

```bash
cd /opt/ecosoul_chatbot
git pull
source shopify_assistant/venv/bin/activate
pip install -r shopify_assistant/requirements.txt
sudo systemctl restart ecosoul-backend
```

---

## Notes / common issues

- If Azure download fails, backend falls back to existing local inventory file.
- If `/docs` loads but `/api/v1/*` calls return `401`, you’re missing `Authorization: Bearer ...`.
- Keep `.env` secrets private; rotate if leaked.

