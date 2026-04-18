"""
Skill runtime executor.
Downloads the skill payload, unpacks it, installs dependencies,
and executes the entry point — capturing outputs.

Two backends:
  subprocess  — runs skills directly in a child process (faster, less isolated)
  docker      — runs skills in an ephemeral container (safer, slower to start)

The subprocess backend is the default. Docker is recommended for production
when running untrusted agent-published skills.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.orm import Skill, SkillRun

_BACKEND = os.getenv("RUNTIME_BACKEND", "subprocess")
_TIMEOUT = int(os.getenv("SKILL_EXECUTION_TIMEOUT_SEC", "120"))
_SANDBOX = Path(os.getenv("SKILL_SANDBOX_DIR", "./sandbox"))
_SANDBOX.mkdir(parents=True, exist_ok=True)


# ── Blob download ──────────────────────────────────────────────────────────────

def _download_payload(key: str) -> bytes:
    import boto3
    from botocore.config import Config
    s3 = boto3.client(
        "s3",
        endpoint_url=os.getenv("STORAGE_ENDPOINT_URL"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "minioadmin"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "password123"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        config=Config(signature_version="s3v4"),
    )
    response = s3.get_object(Bucket=os.getenv("STORAGE_BUCKET", "nwo-skills"), Key=key)
    return response["Body"].read()


# ── Subprocess executor ────────────────────────────────────────────────────────

async def _run_subprocess(
    skill: Skill,
    inputs: dict,
    run_dir: Path,
    timeout: int,
) -> dict:
    """Execute a skill in a subprocess with input/output via stdin/stdout JSON."""
    entry = run_dir / skill.entry_point

    if not entry.exists():
        raise RuntimeError(f"Entry point '{skill.entry_point}' not found after extraction")

    if skill.runtime == "python":
        # Install requirements in the run dir's venv
        if skill.requirements:
            pip_cmd = [sys.executable, "-m", "pip", "install", "--quiet",
                       "--target", str(run_dir / "site-packages")] + skill.requirements
            pip_proc = await asyncio.create_subprocess_exec(
                *pip_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await asyncio.wait_for(pip_proc.communicate(), timeout=60.0)

        cmd = [sys.executable, str(entry)]
        env = {
            **os.environ,
            "PYTHONPATH": str(run_dir / "site-packages"),
            "NWO_SKILL_INPUTS": json.dumps(inputs),
            "NWO_SKILL_OUTPUT_FILE": str(run_dir / "outputs.json"),
        }

    elif skill.runtime == "javascript":
        cmd = ["node", str(entry)]
        env = {
            **os.environ,
            "NWO_SKILL_INPUTS": json.dumps(inputs),
            "NWO_SKILL_OUTPUT_FILE": str(run_dir / "outputs.json"),
        }

    elif skill.runtime == "shell":
        if os.getenv("ALLOW_SHELL_RUNTIME", "false").lower() != "true":
            raise RuntimeError("Shell runtime is disabled. Set ALLOW_SHELL_RUNTIME=true to enable.")
        cmd = ["bash", str(entry)]
        env = {**os.environ, "NWO_SKILL_INPUTS": json.dumps(inputs)}

    else:
        raise RuntimeError(f"Runtime '{skill.runtime}' not supported by subprocess backend")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(run_dir),
        env=env,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"Skill timed out after {timeout}s")

    if proc.returncode != 0:
        err_text = stderr.decode(errors="replace").strip()
        raise RuntimeError(f"Skill failed (exit {proc.returncode}):\n{err_text}")

    # Read outputs from file or parse stdout as JSON
    output_file = run_dir / "outputs.json"
    if output_file.exists():
        return json.loads(output_file.read_text())
    try:
        return json.loads(stdout.decode())
    except Exception:
        return {"stdout": stdout.decode(errors="replace")}


# ── Docker executor ────────────────────────────────────────────────────────────

async def _run_docker(skill: Skill, inputs: dict, run_dir: Path, timeout: int) -> dict:
    """Execute a skill in an ephemeral Docker container."""
    try:
        import docker as docker_sdk
    except ImportError:
        raise RuntimeError("docker package not installed. Run: pip install docker")

    runtime_images = {
        "python": "python:3.11-slim",
        "javascript": "node:20-slim",
        "shell": "ubuntu:24.04",
    }
    image = runtime_images.get(skill.runtime, "python:3.11-slim")

    inputs_json = json.dumps(inputs)
    outputs_path = run_dir / "outputs.json"

    # Build install + run command
    if skill.runtime == "python":
        req_str = " ".join(f'"{r}"' for r in (skill.requirements or []))
        install_cmd = f"pip install --quiet {req_str}" if req_str else "echo 'no reqs'"
        run_cmd = (
            f"{install_cmd} && "
            f"NWO_SKILL_INPUTS='{inputs_json}' "
            f"NWO_SKILL_OUTPUT_FILE=/outputs/outputs.json "
            f"python /skill/{skill.entry_point}"
        )
    elif skill.runtime == "javascript":
        run_cmd = (
            f"NWO_SKILL_INPUTS='{inputs_json}' "
            f"NWO_SKILL_OUTPUT_FILE=/outputs/outputs.json "
            f"node /skill/{skill.entry_point}"
        )
    else:
        run_cmd = f"bash /skill/{skill.entry_point}"

    client = docker_sdk.from_env()

    container = await asyncio.get_event_loop().run_in_executor(None, lambda: client.containers.run(
        image,
        command=["bash", "-c", run_cmd],
        volumes={
            str(run_dir): {"bind": "/skill", "mode": "ro"},
            str(run_dir): {"bind": "/outputs", "mode": "rw"},
        },
        network_mode=os.getenv("DOCKER_NETWORK", "none"),
        mem_limit=f"{os.getenv('SKILL_MAX_MEMORY_MB', '512')}m",
        remove=True,
        detach=False,
        stdout=True,
        stderr=True,
    ))

    if outputs_path.exists():
        return json.loads(outputs_path.read_text())
    try:
        return json.loads(container.decode() if isinstance(container, bytes) else container)
    except Exception:
        return {"stdout": str(container)}


# ── Main execution entry point ─────────────────────────────────────────────────

async def execute_skill(
    db: AsyncSession,
    skill: Skill,
    inputs: dict,
    caller_agent_id: str | None = None,
    timeout_sec: int | None = None,
) -> SkillRun:
    """
    Execute a skill and return the SkillRun record.

    Pipeline:
      1. Create SkillRun record (pending)
      2. Download + extract payload to sandbox
      3. Execute via configured backend
      4. Capture outputs / errors
      5. Update SkillRun + Skill stats
    """
    import uuid as _uuid

    run_id = str(_uuid.uuid4())
    timeout = timeout_sec or _TIMEOUT
    started = datetime.now(timezone.utc)

    # Create run record
    run = SkillRun(
        id=run_id,
        skill_id=skill.id,
        caller_agent_id=caller_agent_id,
        status="running",
        inputs=inputs,
        started_at=started,
    )
    db.add(run)
    await db.flush()

    run_dir = _SANDBOX / run_id
    run_dir.mkdir(parents=True)

    t0 = time.monotonic()
    outputs: dict = {}
    error: str | None = None
    status = "success"

    try:
        # Download + extract
        payload = _download_payload(skill.payload_key)
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
            tar.extractall(run_dir)

        # Execute
        if _BACKEND == "docker" and os.getenv("ALLOW_DOCKER_RUNTIME", "true").lower() == "true":
            outputs = await _run_docker(skill, inputs, run_dir, timeout)
        else:
            outputs = await _run_subprocess(skill, inputs, run_dir, timeout)

    except Exception as e:
        error = str(e)
        status = "failed" if "timed out" not in str(e).lower() else "timeout"

    finally:
        # Cleanup sandbox
        import shutil
        shutil.rmtree(run_dir, ignore_errors=True)

    duration_ms = int((time.monotonic() - t0) * 1000)
    finished = datetime.now(timezone.utc)

    # Update run record
    await db.execute(
        update(SkillRun).where(SkillRun.id == run_id).values(
            status=status,
            outputs=outputs,
            error=error,
            finished_at=finished,
            duration_ms=duration_ms,
        )
    )

    # Update skill stats
    success_inc = 1 if status == "success" else 0
    await db.execute(
        update(Skill).where(Skill.id == skill.id).values(
            run_count=Skill.run_count + 1,
            run_success_count=Skill.run_success_count + success_inc,
        )
    )

    run.status = status
    run.outputs = outputs
    run.error = error
    run.finished_at = finished
    run.duration_ms = duration_ms
    return run
