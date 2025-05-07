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


# -----------------------------------------------------------------------------
# helpers – database reset
# -----------------------------------------------------------------------------
_DB_LOCK: Lock = Lock()  # separate from the deploy lock
_CONTAINER: Final[str] = "localmind"
_DB_FILE: Final[str] = "data/webu.db"

_SQL_CMDS: Final[str] = (
    "DELETE FROM user          WHERE name = 'Test Suite User'; "
    "DELETE FROM user_group    WHERE name != 'default'; "
    "DELETE FROM model; "
    "DELETE FROM model_whitelist; "
    "DELETE FROM tool; "
    "DELETE FROM tool_whitelist; "
    "DELETE FROM function; "
    "DELETE FROM function_whitelist;"
)


def _reset_db_in_container() -> None:
    """
    Make sure sqlite3 exists in the container and execute the wipe.
    Assumes the container runs as root (or user with sudo-less package rights).
    """
    bash_cmd = (
        # 1) install sqlite3 if missing
        "command -v sqlite3 >/dev/null 2>&1 || "
        "(DEBIAN_FRONTEND=noninteractive apt-get update && "
        " DEBIAN_FRONTEND=noninteractive apt-get install -y sqlite3) && "
        # 2) run the deletes
        f'sqlite3 {_DB_FILE} "{_SQL_CMDS}"'
    )

    _run(
        ["docker", "exec", _CONTAINER, "bash", "-c", bash_cmd],
        cwd=ROOT,
        env=os.environ,
    )


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------
@app.delete("/database", dependencies=[Depends(_auth)])
async def delete_database():
    """
    Clear the tables needed by the E2E test-suite.

      • 200 – success
      • 409 – another reset already running
      • 500 – any failure
    """
    if not _DB_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A database-reset is already in progress. Try again later.",
        )

    try:
        _reset_db_in_container()
        return {"message": "Database deletion finished."}

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Database deletion failed: {exc}",
        ) from exc

    finally:
        _DB_LOCK.release()
