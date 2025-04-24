"""FastAPI service skeleton for internal deployment utilities."""

from __future__ import annotations

import os

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv

# Load environment variables from local .env file, if present
load_dotenv()

API_KEY = os.getenv("API_KEY")
if not API_KEY:
    raise RuntimeError("API_KEY must be set in .env)")

app = FastAPI(title="E2E API", version="1.0.0", docs_url="/docs")

security = HTTPBearer(auto_error=False)


def _auth(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> None:
    """Reusable dependency that enforces a static Bearer token."""
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or credentials.credentials != API_KEY
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Bearer token",
        )


@app.post("/deploy", dependencies=[Depends(_auth)])
async def deploy(branch: str):
    """
    Deploy the specified Localmind Git branch on the Beta instance.
    Returns 200 OK if the deployment finished successfully.
    Returns 500 Internal Server Error if the deployment failed.
    """
    # TODO: insert real deployment logic
    return {"message": f"Deployment of branch '{branch}' scheduled."}


@app.delete("/database", dependencies=[Depends(_auth)])
async def delete_database():
    """
    Clear certain database tables that need to be reset when running our e2e test suite on the Beta environment.
    Returns 200 OK if the database deletion finished successfully.
    Returns 500 Internal Server Error if the database deletion failed.
    """
    # TODO: insert real database‑deletion logic
    return {"message": "Database deletion scheduled."}
