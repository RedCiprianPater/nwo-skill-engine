"""FastAPI routes for the NWO Skill Engine."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.database import get_session
from ..models.orm import Agent, Skill, SkillRating, SkillRun
from ..models.schemas import (
    AgentRegisterRequest, AgentResponse, ForkRequest,
    PublishResponse, RateRequest, RunRecord, RunRequest, RunResponse,
    SkillDetail, SkillSearchResponse, SkillSummary,
)
from ..publisher.publish import publish_skill
from ..registry.search import _to_summary, search_skills
from ..runtime.executor import execute_skill

router = APIRouter()
DB = Annotated[AsyncSession, Depends(get_session)]


# ── /agents ────────────────────────────────────────────────────────────────────

@router.post("/agents/register", response_model=AgentResponse, tags=["Agents"])
async def register_agent(req: AgentRegisterRequest, db: DB):
    existing = (await db.execute(
        select(Agent).where(Agent.public_key == req.public_key)
    )).scalar_one_or_none()
    if existing:
        return AgentResponse(id=existing.id, name=existing.name, is_active=existing.is_active, created_at=existing.created_at)

    agent = Agent(name=req.name, public_key=req.public_key, metadata_=req.metadata)
    db.add(agent)
    await db.flush()
    return AgentResponse(id=agent.id, name=agent.name, is_active=agent.is_active, created_at=agent.created_at)


@router.get("/agents/{agent_id}", response_model=AgentResponse, tags=["Agents"])
async def get_agent(agent_id: str, db: DB):
    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    count = (await db.execute(
        select(func.count()).select_from(Skill).where(Skill.agent_id == agent_id)
    )).scalar() or 0
    return AgentResponse(id=agent.id, name=agent.name, is_active=agent.is_active,
                         created_at=agent.created_at, skill_count=int(count))


@router.get("/agents/{agent_id}/skills", response_model=SkillSearchResponse, tags=["Agents"])
async def agent_skills(agent_id: str, db: DB, limit: int = 20, offset: int = 0):
    return await search_skills(db, agent_id=agent_id, limit=limit, offset=offset)


# ── /skills/publish ────────────────────────────────────────────────────────────

@router.post("/skills/publish", response_model=PublishResponse, tags=["Skills"])
async def publish(
    db: DB,
    manifest: str = Form(..., description="JSON-encoded SkillManifest"),
    payload: UploadFile = File(..., description=".tar.gz archive containing skill code"),
    x_agent_id: str | None = Header(default=None, alias="X-Agent-ID"),
):
    """Publish a new skill. Requires X-Agent-ID header."""
    if not x_agent_id:
        raise HTTPException(status_code=401, detail="X-Agent-ID header required")

    agent = (await db.execute(select(Agent).where(Agent.id == x_agent_id))).scalar_one_or_none()
    if not agent or not agent.is_active:
        raise HTTPException(status_code=403, detail="Unknown or inactive agent")

    try:
        manifest_dict = json.loads(manifest)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid manifest JSON: {e}")

    payload_bytes = await payload.read()
    if not payload_bytes:
        raise HTTPException(status_code=422, detail="Empty payload")

    try:
        return await publish_skill(db, x_agent_id, manifest_dict, payload_bytes)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


# ── /skills/search ────────────────────────────────────────────────────────────

@router.get("/skills/search", response_model=SkillSearchResponse, tags=["Skills"])
async def search(
    db: DB,
    q: str | None = Query(default=None),
    skill_type: str | None = Query(default=None),
    runtime: str | None = Query(default=None),
    license: str | None = Query(default=None),
    agent_id: str | None = Query(default=None),
    tags: list[str] = Query(default=[]),
    latest_only: bool = Query(default=True),
    semantic: bool = Query(default=True),
    sort_by: str = Query(default="created_at"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return await search_skills(
        db, q=q, skill_type=skill_type, runtime=runtime,
        license_=license, agent_id=agent_id, tags=tags,
        latest_only=latest_only, semantic=semantic,
        sort_by=sort_by, limit=limit, offset=offset,
    )


# ── /skills/{id} ──────────────────────────────────────────────────────────────

@router.get("/skills/types", tags=["Skills"])
async def skill_types():
    """List all skill type categories."""
    from ..models.manifest import SkillType, Runtime
    return {
        "skill_types": [t.value for t in SkillType],
        "runtimes": [r.value for r in Runtime],
    }


@router.get("/skills/{skill_id}", response_model=SkillDetail, tags=["Skills"])
async def get_skill(skill_id: str, db: DB):
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        # Also try by URN
        skill = (await db.execute(select(Skill).where(Skill.urn == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    agent = (await db.execute(select(Agent).where(Agent.id == skill.agent_id))).scalar_one_or_none()
    agent_resp = AgentResponse(id=agent.id, name=agent.name, is_active=agent.is_active,
                               created_at=agent.created_at) if agent else None

    import os
    public_url = os.getenv("STORAGE_PUBLIC_URL", "http://localhost:9000/nwo-skills")
    payload_url = f"{public_url.rstrip('/')}/{skill.payload_key}"

    summary = _to_summary(skill)
    return SkillDetail(
        **summary.model_dump(),
        manifest=skill.manifest,
        inputs_schema=skill.inputs_schema or [],
        outputs_schema=skill.outputs_schema or [],
        requirements=skill.requirements or [],
        system_deps=skill.system_deps or [],
        ros2_package=skill.ros2_package,
        hardware_requirements=skill.hardware_requirements or {},
        forked_from_urn=skill.forked_from_urn,
        generator=skill.generator,
        llm_provider=skill.llm_provider,
        llm_model=skill.llm_model,
        source_prompt=skill.source_prompt,
        payload_url=payload_url,
        payload_size_bytes=skill.payload_size_bytes,
        run_success_count=skill.run_success_count,
        agent=agent_resp,
    )


@router.get("/skills/{skill_id}/download", tags=["Skills"])
async def download_skill(skill_id: str, db: DB):
    """Download the skill payload (.tar.gz)."""
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    import os
    public_url = os.getenv("STORAGE_PUBLIC_URL", "http://localhost:9000/nwo-skills")
    return RedirectResponse(url=f"{public_url.rstrip('/')}/{skill.payload_key}")


@router.get("/skills/{skill_id}/versions", tags=["Skills"])
async def skill_versions(skill_id: str, db: DB):
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    versions = (await db.execute(
        select(Skill)
        .where(Skill.agent_id == skill.agent_id, Skill.slug == skill.slug)
        .order_by(Skill.version_major.desc(), Skill.version_minor.desc(), Skill.version_patch.desc())
    )).scalars().all()
    return [_to_summary(v) for v in versions]


# ── /skills/{id}/run ──────────────────────────────────────────────────────────

@router.post("/skills/{skill_id}/run", response_model=RunResponse, tags=["Execution"])
async def run_skill(skill_id: str, req: RunRequest, db: DB):
    """Execute a skill with the given inputs."""
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    if skill.is_deprecated:
        raise HTTPException(status_code=410, detail="Skill is deprecated")

    run = await execute_skill(
        db=db,
        skill=skill,
        inputs=req.inputs,
        caller_agent_id=req.caller_agent_id,
        timeout_sec=req.timeout_sec,
    )

    return RunResponse(
        run_id=run.id, skill_id=skill.id, urn=skill.urn,
        status=run.status, outputs=run.outputs or {},
        error=run.error, duration_ms=run.duration_ms,
        peak_memory_mb=run.peak_memory_mb,
    )


@router.get("/skills/{skill_id}/runs", tags=["Execution"])
async def skill_runs(skill_id: str, db: DB, limit: int = 20):
    runs = (await db.execute(
        select(SkillRun)
        .where(SkillRun.skill_id == skill_id)
        .order_by(SkillRun.created_at.desc())
        .limit(limit)
    )).scalars().all()
    return [RunRecord.model_validate(r) for r in runs]


# ── /skills/{id}/rate ─────────────────────────────────────────────────────────

@router.put("/skills/{skill_id}/rate", tags=["Marketplace"])
async def rate_skill(skill_id: str, req: RateRequest, db: DB):
    """Rate a skill (1-5 stars). One rating per agent per skill."""
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    existing = (await db.execute(
        select(SkillRating).where(
            SkillRating.skill_id == skill_id,
            SkillRating.rater_agent_id == req.rater_agent_id,
        )
    )).scalar_one_or_none()

    if existing:
        await db.execute(
            update(SkillRating).where(SkillRating.id == existing.id)
            .values(rating=req.rating, comment=req.comment)
        )
    else:
        db.add(SkillRating(
            skill_id=skill_id, rater_agent_id=req.rater_agent_id,
            rating=req.rating, comment=req.comment,
        ))

    # Recompute avg
    avg = (await db.execute(
        select(func.avg(SkillRating.rating)).where(SkillRating.skill_id == skill_id)
    )).scalar()
    count = (await db.execute(
        select(func.count()).select_from(SkillRating).where(SkillRating.skill_id == skill_id)
    )).scalar()
    await db.execute(
        update(Skill).where(Skill.id == skill_id).values(avg_rating=avg, rating_count=count)
    )
    return {"skill_id": skill_id, "avg_rating": float(avg or 0), "rating_count": int(count or 0)}


# ── /skills/{id}/fork ─────────────────────────────────────────────────────────

@router.post("/skills/{skill_id}/fork", response_model=PublishResponse, tags=["Marketplace"])
async def fork_skill(skill_id: str, req: ForkRequest, db: DB):
    """
    Fork a skill — copies the manifest and payload under a new agent/name,
    preserving provenance via forked_from_urn.
    """
    original = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not original:
        raise HTTPException(status_code=404, detail="Skill not found")

    # Download original payload
    try:
        import boto3
        from botocore.config import Config
        import os as _os
        s3 = boto3.client(
            "s3",
            endpoint_url=_os.getenv("STORAGE_ENDPOINT_URL"),
            aws_access_key_id=_os.getenv("AWS_ACCESS_KEY_ID", "minioadmin"),
            aws_secret_access_key=_os.getenv("AWS_SECRET_ACCESS_KEY", "password123"),
            region_name=_os.getenv("AWS_REGION", "us-east-1"),
            config=Config(signature_version="s3v4"),
        )
        bucket = _os.getenv("STORAGE_BUCKET", "nwo-skills")
        payload_bytes = s3.get_object(Bucket=bucket, Key=original.payload_key)["Body"].read()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Could not fetch original payload: {e}")

    # Build forked manifest
    forked_manifest = dict(original.manifest)
    forked_manifest["name"] = req.new_name or f"{original.name} (fork)"
    forked_manifest["version"] = "1.0.0"
    forked_manifest["forked_from"] = original.urn
    forked_manifest["agent_id"] = req.forker_agent_id
    forked_manifest["signature"] = None
    forked_manifest["visibility"] = req.visibility.value
    forked_manifest.pop("@id", None)

    try:
        result = await publish_skill(db, req.forker_agent_id, forked_manifest, payload_bytes)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Increment fork count on original
    await db.execute(
        update(Skill).where(Skill.id == skill_id).values(fork_count=Skill.fork_count + 1)
    )
    return result


# ── /skills/{id}/deprecate ────────────────────────────────────────────────────

@router.delete("/skills/{skill_id}", tags=["Skills"])
async def deprecate_skill(
    skill_id: str, db: DB,
    x_agent_id: str | None = Header(default=None, alias="X-Agent-ID"),
):
    if not x_agent_id:
        raise HTTPException(status_code=401, detail="X-Agent-ID required")
    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    if skill.agent_id != x_agent_id:
        raise HTTPException(status_code=403, detail="Can only deprecate your own skills")
    await db.execute(update(Skill).where(Skill.id == skill_id).values(is_deprecated=True))
    return {"message": f"Skill {skill_id} deprecated"}
