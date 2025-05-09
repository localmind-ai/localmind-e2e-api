# E2E API

> **Internal‑use only – runs ONLY on our _Beta_ instance due to security reasons!**

This repository hosts a FastAPI service that exposes a few endpoints used for e2e testing:

| Method | Path        | Description                                                                                          |
| ------ | ----------- | ---------------------------------------------------------------------------------------------------- |
| POST   | `/deploy`   | Trigger deployment of a specific branch (used by our nightly test run)                               |
| DELETE | `/database` | Wipes certain database tables (used by e2e test suite when testing manually on the BETA environment) |

All endpoints are secured by a bearer token that must be supplied in the `Authorization` header. Configure the token via `.env`.

## Running locally

### 1. Prepare environment

```bash
cp .env.example .env             # then edit API_KEY
```

### 2. Development

```bash
# one-time setup (installs deps into Poetry's virtualenv)
poetry install

# start FastAPI with live-reload
poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 3. Production deployment

Traffic that reaches `https://beta-e2e.localmind.io/` is forwarded by our global **Nginx** to port **8000** on the beta server, where the FastAPI app listens.

---

#### 1 · Install runtime dependencies

```bash
sudo -u localmind -i
poetry install --no-dev     # from the project root as localmind user, not root user!
```

---

#### 2 · Create a start-up script (wrapper)

Because `poetry` often lives in _per-user_ paths, a tiny wrapper script keeps the
systemd unit clean and avoids hard-coding paths inside the service file. This script
already exists in this repo, but just for reference, here it is:

```bash
#!/usr/bin/env bash
set -e
cd /home/localmind-e2e-api

# use Poetry to launch Gunicorn/Uvicorn
exec /home/localmind/.local/bin/poetry run gunicorn app.main:app \
     -k uvicorn.workers.UvicornWorker \
     --workers 4 \
     --bind 0.0.0.0:8000
```

```bash
chmod +x /home/localmind-e2e-api/start.sh
```

_(Replace `/usr/local/bin/poetry` if `which poetry` prints something else.)_

---

#### 3 · Create the **systemd** unit

Create the following system service. Note that this runs the FastAPI as the localmind user, not root. So you need to make sure that you followed step 1 correctly or the service will fail to start.

```ini
# /etc/systemd/system/e2e-api.service
[Unit]
Description=E2E FastAPI service (Gunicorn/Uvicorn)
After=network.target

[Service]
User=localmind
Group=localmind
ExecStart=/home/localmind-e2e-api/start.sh
WorkingDirectory=/home/localmind-e2e-api
EnvironmentFile=/home/localmind-e2e-api/.env

# Restart policy
Restart=on-failure
KillSignal=SIGINT
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

> **Ensure** your `.env` contains `API_KEY`, `GIT_USERNAME`, and
> `GIT_PERSONAL_ACCESS_TOKEN` – the `/deploy` endpoint needs them.

---

#### 4 · Enable and start the service

```bash
sudo systemctl daemon-reload       # pick up the new unit
sudo systemctl enable  e2e-api     # start on boot
sudo systemctl start   e2e-api
```

---

### Deploying updates

```bash
sudo -u localmind -i
git pull
poetry install --no-dev
sudo systemctl restart e2e-api
```

---

### Managing the service

```bash
# reload unit if you edit it
sudo systemctl daemon-reload

# start / stop / restart
sudo systemctl start   e2e-api
sudo systemctl stop    e2e-api
sudo systemctl restart e2e-api

# enable on boot
sudo systemctl enable  e2e-api

# current status
sudo systemctl status  e2e-api

# live logs (Ctrl-C to exit)
sudo journalctl -u e2e-api -f
```

> **Troubleshooting tip:** if the service fails to start, run  
> `sudo journalctl -xeu e2e-api.service` to see exact error messages.
