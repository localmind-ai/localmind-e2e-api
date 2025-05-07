from __future__ import annotations

"""FastAPI admin API with **mutually exclusive** destructive operations.

* Only **one** of the following is allowed to run at any time:
  - background deployment
  - database reset

If another job is in progress the endpoint returns **HTTP 409**.
Swagger UI can poll `/deploy/{job_id}` for status updates.
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
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, status
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

ROOT = Path("/home/localmind")
REPO = ROOT / "lm-custom-build" / "localmind"

app = FastAPI(title="E2E API", version="1.2.0", docs_url="/docs")
security = HTTPBearer(auto_error=False)


def _auth(cred: HTTPAuthorizationCredentials | None = Depends(security)) -> None:
    if cred is None or cred.scheme.lower() != "bearer" or cred.credentials != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Bearer token",
        )


# -----------------------------------------------------------------------------
# utilities
# -----------------------------------------------------------------------------


def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> None:
    """Run *cmd* raising an exception on failure and capturing output."""
    try:
        subprocess.run(cmd, cwd=cwd, env=env, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Command {' '.join(exc.cmd)!r} failed (exit {exc.returncode})\n"
            f"stdout:\n{exc.stdout.decode()}\nstderr:\n{exc.stderr.decode()}"
        ) from exc


# -----------------------------------------------------------------------------
# global lock – ensures only **one** destructive operation at a time
# -----------------------------------------------------------------------------
_OPER_LOCK: Lock = Lock()

# -----------------------------------------------------------------------------
# deployment implementation
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


JOBS: dict[str, Job] = {}


def _deploy(job: Job) -> None:
    """Perform the actual deployment, updating *job* fields for status polling."""
    env = {
        **os.environ,
        "GIT_USERNAME": GIT_USERNAME,
        "GIT_PERSONAL_ACCESS_TOKEN": GIT_PAT,
    }

    try:
        job.state, job.step = State.running, "docker compose down"
        _run(["docker", "compose", "down"], cwd=ROOT, env=env)

        job.step = "git checkout main & pull"
        _run(["git", "switch", "main"], cwd=REPO, env=env)
        _run(["git", "pull"], cwd=REPO, env=env)

        job.step = f"git switch {job.branch}"
        _run(["git", "switch", job.branch], cwd=REPO, env=env)

        job.step = "clean image & builder cache"
        _run(["docker", "image", "rm", "-f", "localmind"], cwd=ROOT, env=env)
        _run(["docker", "builder", "prune", "-f"], cwd=ROOT, env=env)

        job.step = "docker build"
        _run(["docker", "build", "-t", "localmind", "."], cwd=REPO, env=env)

        job.step = "docker compose up -d"
        _run(["docker", "compose", "up", "-d"], cwd=ROOT, env=env)

        job.state, job.step = State.success, "done"
    except Exception as exc:
        job.state, job.error = State.error, str(exc)


# -----------------------------------------------------------------------------
# API – deployment
# -----------------------------------------------------------------------------


@app.post("/deploy", dependencies=[Depends(_auth)])
def deploy(branch: str, bg: BackgroundTasks):
    """Start a deployment in the background. Returns *job_id* or 409 if busy."""
    if not branch.strip():
        raise HTTPException(status_code=400, detail="branch must be non-empty")

    # enforce single‑flight across deploy & db‑reset
    if not _OPER_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Another operation is already in progress. Try again later.",
        )

    job = Job(id=str(uuid.uuid4()), branch=branch)
    JOBS[job.id] = job

    def _task(j: Job):
        try:
            _deploy(j)
        finally:
            _OPER_LOCK.release()

    bg.add_task(_task, job)
    return {"job_id": job.id}


@app.get("/deploy/{job_id}", dependencies=[Depends(_auth)])
def deploy_status(job_id: str):
    """Poll the status of a deployment job."""
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return {"state": job.state, "step": job.step, "error": job.error}


# -----------------------------------------------------------------------------
# helpers – database reset (shares the global lock)
# -----------------------------------------------------------------------------

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
    bash_cmd = (
        "command -v sqlite3 >/dev/null 2>&1 || ("
        "DEBIAN_FRONTEND=noninteractive apt-get update -y && "
        "DEBIAN_FRONTEND=noninteractive apt-get install -y sqlite3"
        ") && "
        f'[[ -f "{_DB_FILE}" ]] || (echo "Database file {_DB_FILE} not found" >&2; exit 1) && '
        f'sqlite3 "{_DB_FILE}" "{_SQL_CMDS}"'
    )
    _run(
        ["docker", "exec", _CONTAINER, "bash", "-c", bash_cmd], cwd=ROOT, env=os.environ
    )


@app.delete("/database", dependencies=[Depends(_auth)], include_in_schema=False)
async def delete_database():
    """Clear test‑suite tables. Returns 409 if a deploy or other reset is running."""
    if not _OPER_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Another operation is already in progress. Try again later.",
        )
    try:
        _reset_db_in_container()
        return {"message": "Database deletion finished."}
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Database deletion failed: {exc}"
        ) from exc
    finally:
        _OPER_LOCK.release()

