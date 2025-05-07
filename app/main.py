from __future__ import annotations

"""FastAPI admin API with background deploy jobs and status polling.
This file merges the original synchronous implementation with a small
in‑memory job queue so Swagger UI (/docs) can poll deployment progress.
"""

import os
import subprocess
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Final

from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
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

app = FastAPI(title="E2E API", version="1.1.0", docs_url="/docs")
security = HTTPBearer(auto_error=False)


def _auth(
    cred: HTTPAuthorizationCredentials | None = Depends(security),
) -> None:
    if cred is None or cred.scheme.lower() != "bearer" or cred.credentials != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Bearer token",
        )


# -----------------------------------------------------------------------------
# utils
# -----------------------------------------------------------------------------


def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> None:
    """Run *cmd* raising an exception on non‑zero exit; stdout/stderr are captured."""
    try:
        subprocess.run(cmd, cwd=cwd, env=env, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Command {' '.join(exc.cmd)!r} failed (exit {exc.returncode})\n"
            f"stdout:\n{exc.stdout.decode()}\nstderr:\n{exc.stderr.decode()}"
        ) from exc


# -----------------------------------------------------------------------------
# synchronous deployment helper (re‑uses original logic)
# -----------------------------------------------------------------------------


def _deploy_impl(branch: str) -> None:
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


# -----------------------------------------------------------------------------
# background job runner
# -----------------------------------------------------------------------------


class State(str, Enum):
    queued = "queued"
    running = "running"
    success = "success"
    error = "error"


@dataclass
class Job:
    id: str
    branch: str
    state: State = State.queued
    step: str = "waiting for slot"
    error: str | None = None
    logs: list[str] = field(default_factory=list)  # not exposed yet; future use


JOBS: dict[str, Job] = {}
_DEPLOY_LOCK: Lock = Lock()  # one deploy at a time – same semantics as before


def _deploy_with_status(job: Job) -> None:
    """Run the blocking deploy inside a background thread, updating *job*."""
    with _DEPLOY_LOCK:
        job.state, job.step = State.running, "docker compose down"
        try:
            # Each phase updates job.step before the command runs so clients see progress
            _run(["docker", "compose", "down"], cwd=ROOT, env=os.environ)

            job.step = "git checkout main & pull"
            _run(["git", "switch", "main"], cwd=REPO, env=os.environ)
            _run(["git", "pull"], cwd=REPO, env=os.environ)

            job.step = f"git switch {job.branch}"
            _run(["git", "switch", job.branch], cwd=REPO, env=os.environ)

            job.step = "clean image & builder cache"
            _run(["docker", "image", "rm", "-f", "localmind"], cwd=ROOT, env=os.environ)
            _run(["docker", "builder", "prune", "-f"], cwd=ROOT, env=os.environ)

            job.step = "docker build"
            _run(["docker", "build", "-t", "localmind", "."], cwd=REPO, env=os.environ)

            job.step = "docker compose up -d"
            _run(["docker", "compose", "up", "-d"], cwd=ROOT, env=os.environ)

            job.state, job.step = State.success, "done"
        except Exception as exc:
            job.state, job.error = State.error, str(exc)


# -----------------------------------------------------------------------------
# API – Deployment
# -----------------------------------------------------------------------------


@app.post("/deploy", dependencies=[Depends(_auth)])
def deploy(branch: str, bg: BackgroundTasks):
    """Kick off a deployment in the background and return a *job_id*."""
    if not branch.strip():
        raise HTTPException(status_code=400, detail="branch must be non‑empty")

    job_id = str(uuid.uuid4())
    job = Job(id=job_id, branch=branch)
    JOBS[job_id] = job

    bg.add_task(_deploy_with_status, job)

    return {"job_id": job_id}


@app.get("/deploy/{job_id}", dependencies=[Depends(_auth)])
def deploy_status(job_id: str):
    """Return the current state/step for the given *job_id*."""
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job")
    return {
        "state": job.state,
        "step": job.step,
        "error": job.error,
    }


# -----------------------------------------------------------------------------
# helpers – database reset (unchanged apart from import tweaks)
# -----------------------------------------------------------------------------

_DB_LOCK: Lock = Lock()
_CONTAINER: Final[str] = "localmind"
_DB_FILE: Final[str] = "data/webui.db"

_SQL_CMDS = (
    "DELETE FROM user          WHERE email != 'serviceaccount@localmind.ai';"
    "DELETE FROM user_group    WHERE name <> 'default';"
    "DELETE FROM model; "
    "DELETE FROM model_whitelist; "
    "DELETE FROM model_custom_variable;"
    "DELETE FROM tool; "
    "DELETE FROM tool_whitelist; "
    "DELETE FROM function; "
    "DELETE FROM function_whitelist;"
    "DELETE FROM folder;"
    "DELETE FROM folder_whitelist;"
    "DELETE FROM document;"
    "DELETE FROM organization   WHERE name <> 'default';"
    "DELETE FROM organization_custom_variable;"
    "DELETE FROM file;"
    "DELETE FROM group_membership;"
    "DELETE FROM project;"
    "DELETE FROM project_whitelist;"
    "DELETE FROM prompt;"
    "DELETE FROM prompt_whitelist;"
    "DELETE FROM uploaded_file;"
    "DELETE FROM webpages;"
)


def _reset_db_in_container() -> None:
    """Ensure sqlite3 exists in the container, verify the DB file, then wipe rows."""
    bash_cmd = (
        # 1) install sqlite3 if it's missing
        "command -v sqlite3 >/dev/null 2>&1 || ("
        "DEBIAN_FRONTEND=noninteractive apt-get update -y && "
        "DEBIAN_FRONTEND=noninteractive apt-get install -y sqlite3"
        ") && "
        # 2) verify the DB file exists
        f'[[ -f "{_DB_FILE}" ]] || (echo "Database file {_DB_FILE} not found" >&2; exit 1) && '
        # 3) run the deletes
        f'sqlite3 "{_DB_FILE}" "{_SQL_CMDS}"'
    )

    _run(
        ["docker", "exec", _CONTAINER, "bash", "-c", bash_cmd],
        cwd=ROOT,
        env=os.environ,
    )


@app.delete("/database", dependencies=[Depends(_auth)], include_in_schema=False)
async def delete_database():
    """Clear tables used by the E2E test‑suite."""
    if not _DB_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A database‑reset is already in progress. Try again later.",
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

