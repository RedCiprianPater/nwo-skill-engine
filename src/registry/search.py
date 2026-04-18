"""
Skill registry — search, discovery, and semantic similarity.
Mirrors the L2 search pattern but scoped to skills.
"""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import and_, desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.orm import Skill
from ..models.schemas import SkillSearchResponse, SkillSummary

_EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "openai")
_EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
_OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")


async def embed_skill_text(
    name: str,
    description: str | None,
    skill_type: str,
    tags: list[str],
    runtime: str,
) -> list[float]:
    """Generate an embedding vector for a skill (for semantic search)."""
    if _EMBEDDING_PROVIDER != "openai" or not _OPENAI_KEY:
        return []

    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=_OPENAI_KEY)

    parts = [f"Skill: {name}", f"Type: {skill_type}", f"Runtime: {runtime}"]
    if description:
        parts.append(f"Description: {description}")
    if tags:
        parts.append(f"Tags: {', '.join(tags)}")

    resp = await client.embeddings.create(
        input=". ".join(parts),
        model=_EMBEDDING_MODEL,
    )
    return resp.data[0].embedding


async def search_skills(
    db: AsyncSession,
    q: str | None = None,
    skill_type: str | None = None,
    runtime: str | None = None,
    license_: str | None = None,
    agent_id: str | None = None,
    tags: list[str] | None = None,
    latest_only: bool = True,
    visibility: str = "public",
    semantic: bool = True,
    sort_by: str = "created_at",
    limit: int = 20,
    offset: int = 0,
) -> SkillSearchResponse:
    """Search the skill registry."""

    filters = [Skill.is_deprecated == False]  # noqa: E712
    if latest_only:
        filters.append(Skill.is_latest == True)  # noqa: E712
    if visibility == "public":
        filters.append(Skill.visibility == "public")
    if skill_type:
        filters.append(Skill.skill_type == skill_type)
    if runtime:
        filters.append(Skill.runtime == runtime)
    if license_:
        filters.append(Skill.license == license_)
    if agent_id:
        filters.append(Skill.agent_id == agent_id)
    if tags:
        for tag in tags:
            filters.append(Skill.tags.any(tag))

    use_semantic = bool(q and semantic and _EMBEDDING_PROVIDER != "none")

    if use_semantic:
        results, total = await _semantic_search(db, q, filters, limit, offset)
    elif q:
        results, total = await _fulltext_search(db, q, filters, sort_by, limit, offset)
    else:
        results, total = await _filter_search(db, filters, sort_by, limit, offset)

    summaries = [_to_summary(s) for s in results]
    return SkillSearchResponse(total=total, limit=limit, offset=offset, query=q, results=summaries)


async def _semantic_search(db, q, filters, limit, offset):
    try:
        vec = await embed_skill_text(q, None, "", [], "")
        if not vec:
            return await _fulltext_search(db, q, filters, "created_at", limit, offset)
        vec_str = f"[{','.join(str(v) for v in vec)}]"
        distance_expr = text(f"embedding <=> '{vec_str}'::vector")
        stmt = (
            select(Skill)
            .where(and_(*filters), Skill.embedding.is_not(None))
            .order_by(distance_expr)
            .limit(limit).offset(offset)
        )
        count_stmt = select(func.count()).select_from(Skill).where(and_(*filters), Skill.embedding.is_not(None))
        results = (await db.execute(stmt)).scalars().all()
        total = (await db.execute(count_stmt)).scalar() or 0
        return list(results), int(total)
    except Exception:
        return await _fulltext_search(db, q, filters, "created_at", limit, offset)


async def _fulltext_search(db, q, filters, sort_by, limit, offset):
    ts_q = func.plainto_tsquery("english", q)
    ts_v = func.to_tsvector(
        "english",
        func.coalesce(Skill.name, "") + " " + func.coalesce(Skill.description, ""),
    )
    text_f = or_(ts_v.op("@@")(ts_q), Skill.name.ilike(f"%{q}%"), Skill.tags.any(q.lower()))
    stmt = (
        select(Skill).where(and_(*filters, text_f))
        .order_by(_sort(sort_by)).limit(limit).offset(offset)
    )
    count_stmt = select(func.count()).select_from(Skill).where(and_(*filters, text_f))
    results = (await db.execute(stmt)).scalars().all()
    total = (await db.execute(count_stmt)).scalar() or 0
    return list(results), int(total)


async def _filter_search(db, filters, sort_by, limit, offset):
    stmt = select(Skill).where(and_(*filters)).order_by(_sort(sort_by)).limit(limit).offset(offset)
    count_stmt = select(func.count()).select_from(Skill).where(and_(*filters))
    results = (await db.execute(stmt)).scalars().all()
    total = (await db.execute(count_stmt)).scalar() or 0
    return list(results), int(total)


def _sort(sort_by: str):
    if sort_by == "runs":
        return desc(Skill.run_count)
    if sort_by == "rating":
        return desc(Skill.avg_rating)
    if sort_by == "name":
        return Skill.name
    return desc(Skill.created_at)


def _to_summary(s: Skill) -> SkillSummary:
    return SkillSummary(
        id=s.id, urn=s.urn, name=s.name, slug=s.slug,
        version=s.version, skill_type=s.skill_type, runtime=s.runtime,
        description=s.description, tags=s.tags or [],
        license=s.license, visibility=s.visibility,
        run_count=s.run_count, avg_rating=s.avg_rating,
        rating_count=s.rating_count, fork_count=s.fork_count,
        agent_id=s.agent_id, is_latest=s.is_latest, created_at=s.created_at,
    )
