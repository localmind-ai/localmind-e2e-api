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

Changes to `app/` are picked up automatically via `uvicorn --reload`.

### 3. Production

Our Nginx server has been configured to route traffic hitting `https://beta-e2e.localmind.io/` to port 8000 of the beta server.

First, make sure production dependencies are installed:

```bash
poetry install --no-dev
```

Then, create a service unit so the API starts automatically on boot and is supervised by the OS.

```bash
# /etc/systemd/system/e2e-api.service
[Unit]
Description=E2E FastAPI service (Gunicorn/Uvicorn)
After=network.target

[Service]
# Path to the repo root
WorkingDirectory=/opt/e2e-api
# Absolute path to Poetry (adjust if different on your box)
ExecStart=/usr/bin/poetry run gunicorn app.main:app \
          -k uvicorn.workers.UvicornWorker \
          --workers 4 \
          --bind 0.0.0.0:8000
EnvironmentFile=/opt/e2e-api/.env
User=www-data
Group=www-data
Restart=on-failure
# Give Gunicorn time to gracefully stop workers
KillSignal=SIGINT
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

Whenever you deploy updates:

```bash
git pull
poetry install --no-dev
sudo systemctl restart e2e-api
```

That's it! Here are some general commands to help you manage the system service:

```bash
# register the new unit file
sudo systemctl daemon-reload

# start / stop / restart the service
sudo systemctl start   e2e-api
sudo systemctl stop    e2e-api
sudo systemctl restart e2e-api

# enable on boot
sudo systemctl enable  e2e-api

# check current status
sudo systemctl status  e2e-api

# tail logs (Press Ctrl-C to exit)
sudo journalctl -u e2e-api -f
```
