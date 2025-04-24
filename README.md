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

### 2. Development – hot‑reload inside Docker

```bash
docker compose -f docker-compose-dev.yml up --build -d
docker compose -f docker-compose-dev.yml down
```

Changes to `app/` are picked up automatically via `uvicorn --reload`.

### 3. Production

Our Nginx server has been configured to route traffic hitting `beta.localmind.io/e2e-api` to port 9000 of the beta server. So not only should you not deploy this code to any other server, but you also couldn't without changing the Nginx configuration.

```bash
docker compose -f docker-compose-prod.yml up --build -d
docker compose -f docker-compose-prod.yml down
```
