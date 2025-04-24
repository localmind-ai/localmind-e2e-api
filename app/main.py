# app.py
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from threading import Lock
from typing import Final

from dotenv import load_dotenv
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    status,
)
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# -----------------------------------------------------------------------------
# config & auth
# -----------------------------------------------------------------------------
load_dotenv()

API_KEY: Final[str | None] = os.getenv("API_KEY")
GIT_USERNAME: Final[str | None] = os.getenv("GIT_USERNAME")
GIT_PAT: Final[str | None] = os.getenv("GIT_PERSONAL_ACCESS_TOKEN")

if not API_KEY:
    raise RuntimeError("API_KEY must be set in .env")

if not GIT_USERNAME or not GIT_PAT:
    raise RuntimeError("GIT_USERNAME and GIT_PERSONAL_ACCESS_TOKEN must be set in .env")

# Beta paths
ROOT = Path("/home/localmind")
REPO = ROOT / "lm-custom-build" / "localmind"

app = FastAPI(title="E2E API", version="1.0.0", docs_url="/docs")
security = HTTPBearer(auto_error=False)


def _auth(
    cred: HTTPAuthorizationCredentials | None = Depends(security),
) -> None:
    if cred is None or cred.scheme.lower() != "bearer" or cred.credentials != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Bearer token",
        )


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────
def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> None:
    try:
        subprocess.run(cmd, cwd=cwd, env=env, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Command {' '.join(exc.cmd)!r} failed "
            f"(exit {exc.returncode})\nstdout:\n{exc.stdout.decode()}\nstderr:\n{exc.stderr.decode()}"
        ) from exc


def _deploy(branch: str) -> None:
    env = {
        **os.environ,
        "GIT_USERNAME": GIT_USERNAME,
        "GIT_PERSONAL_ACCESS_TOKEN": GIT_PAT,
    }

    _run(["docker", "compose", "down"], cwd=ROOT, env=env)

    _run(["git", "switch", "main"], cwd=REPO, env=env)
    _run(["git", "pull"], cwd=REPO, env=env)
    _run(["git", "switch", branch], cwd=REPO, env=env)

    _run(["docker", "image", "rm", "-f", "localmind"], cwd=ROOT, env=env)
    _run(["docker", "builder", "prune", "-f"], cwd=ROOT, env=env)
    _run(["docker", "build", "-t", "localmind", "."], cwd=REPO, env=env)

    _run(["docker", "compose", "up", "-d"], cwd=ROOT, env=env)


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────
_DEPLOY_LOCK: Lock = (
    Lock()
)  # only one job at a time is allowed to avoid messing sth up on the beta server


@app.post("/deploy", dependencies=[Depends(_auth)])
def deploy(branch: str):
    """
    Run a deployment synchronously.
      • 409 if another deployment is already running
      • 500 on any failure (with reason)
      • 200 on success
    """
    # enforce single-flight
    if not _DEPLOY_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A deployment is already in progress. Try again later.",
        )

    try:
        if not branch.strip():
            raise HTTPException(
                status_code=400, detail="branch must be a non-empty string"
            )

        _deploy(branch)
        return {"message": f"Deployment of branch '{branch}' completed successfully."}

    except HTTPException:
        # propagate explicit FastAPI errors as-is
        raise
    except Exception as exc:
        # wrap any other error into a clean 500
        raise HTTPException(
            status_code=500,
            detail=f"Deployment failed: {exc}",
        ) from exc
    finally:
        _DEPLOY_LOCK.release()


app.delete("/database", dependencies=[Depends(_auth)])


async def delete_database():
    """
    Clear certain database tables that need to be reset when running our e2e test suite on the Beta environment.
      • 200 on sucess
      • 500 on any failure (with reason)
    """
    # TODO: insert real logic
    return {"message": "Database deletion finished."}
