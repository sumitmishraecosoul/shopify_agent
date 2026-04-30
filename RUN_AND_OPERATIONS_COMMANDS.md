# EcoSoul Chatbot - Run & Operations Commands

This file contains the default commands to run backend/frontend and validate API availability.

## 1) Activate Python environment

```powershell
cd "C:\Users\Sumit Mishra\Documents\ecosoul_chatbot"
.\shopify_assistant\venv\Scripts\Activate.ps1
```

## 2) Run backend (FastAPI)

### Local only (same machine)

```powershell
.\shopify_assistant\venv\Scripts\python.exe -m uvicorn shopify_assistant.main:app --host 127.0.0.1 --port 8000
```

### Office/LAN access (other systems on same network)

```powershell
.\shopify_assistant\venv\Scripts\python.exe -m uvicorn shopify_assistant.main:app --host 0.0.0.0 --port 8000
```

### Alternative backend run (uses `PORT` from `.env`, default is 8010 in config)

```powershell
.\shopify_assistant\venv\Scripts\python.exe -m shopify_assistant.main
```

## 3) Run frontend (Streamlit)

```powershell
streamlit run shopify_assistant/streamlit_app.py
```

## 4) Swagger / API docs links

When backend runs on port `8000`:

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`

When backend runs on LAN host `192.168.50.56`:

- Swagger UI: `http://192.168.50.56:8000/docs`
- ReDoc: `http://192.168.50.56:8000/redoc`
- OpenAPI JSON: `http://192.168.50.56:8000/openapi.json`

## 5) Quick health checks

```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing
Invoke-WebRequest -Uri "http://192.168.50.56:8000/health" -UseBasicParsing
```

## 5.1) Trigger inventory refresh (after your pipeline uploads to Azure)

### Option A (recommended): Service token (no login needed)

Set on backend (env var): `INVENTORY_REFRESH_SERVICE_TOKEN=<some-long-random-token>`

Then call:

```powershell
$token = "<INVENTORY_REFRESH_SERVICE_TOKEN>"
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8010/api/v1/inventory/refresh" `
  -Headers @{ Authorization = "Bearer $token" } `
  -ContentType "application/json" `
  -Body (@{ date = "2026-04-30"; force = $false } | ConvertTo-Json)
```

### Retry/backoff example (PowerShell)

```powershell
$token = "<INVENTORY_REFRESH_SERVICE_TOKEN>"
$uri = "http://127.0.0.1:8010/api/v1/inventory/refresh"
$body = (@{ date = "2026-04-30"; force = $false } | ConvertTo-Json)

for ($i=0; $i -lt 3; $i++) {
  try {
    $resp = Invoke-RestMethod -Method Post -Uri $uri -Headers @{ Authorization = "Bearer $token" } -ContentType "application/json" -Body $body
    $resp
    break
  } catch {
    Start-Sleep -Seconds (2 * ($i + 1))
    if ($i -eq 2) { throw }
  }
}
```

### Option B: Login token

1) Login to get token:

```powershell
$login = @{ username = "shopify_client_app"; password = "change-me" } | ConvertTo-Json
$token = (Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8010/api/v1/auth/login" -ContentType "application/json" -Body $login).access_token
```

2) Call refresh trigger:

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8010/api/v1/inventory/refresh" `
  -Headers @{ Authorization = "Bearer $token" } `
  -ContentType "application/json" `
  -Body (@{ date = "2026-04-30"; force = $false } | ConvertTo-Json)
```

### Retry/backoff example (Python)

```python
import time
import requests

BASE = "http://<YOUR_VPS_IP>:8010"
SERVICE_TOKEN = "<INVENTORY_REFRESH_SERVICE_TOKEN>"

payload = {"date": "2026-04-30", "force": False}
headers = {"Authorization": f"Bearer {SERVICE_TOKEN}"}

for attempt in range(1, 4):
    try:
        r = requests.post(f"{BASE}/api/v1/inventory/refresh", json=payload, headers=headers, timeout=60)
        r.raise_for_status()
        print(r.json())
        break
    except Exception as e:
        if attempt == 3:
            raise
        time.sleep(2 * attempt)
```

## 6) Port/process checks (Windows)

Check what is running on API port:

```powershell
netstat -ano | Select-String ":8000"
```

Kill process (replace PID):

```powershell
taskkill /PID <PID> /F
```

## 7) Network access checklist (if LAN URL does not open)

1. Start backend with `--host 0.0.0.0`.
2. Confirm listener is `0.0.0.0:8000` using `netstat`.
3. Ensure Windows Firewall allows inbound TCP `8000` on Private network.
4. Ensure all users are on same office LAN.
5. Re-check URL: `http://192.168.50.56:8000/docs`.
