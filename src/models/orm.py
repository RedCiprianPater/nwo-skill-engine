"""
SQLAlchemy ORM models for the NWO Skill Engine.

Tables:
  skills          — published skill records (one row per version)
  skill_runs      — execution log
  skill_ratings   — agent ratings of skills
  agents          — registered agent identities (mirrors L2 pattern)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey,
    Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Agent(Base):
    __tablename__ = "skill_agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)

    skills: Mapped[list["Skill"]] = relationship("Skill", back_populates="agent")


class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("agent_id", "slug", "version", name="uq_skill_agent_slug_version"),
        Index("ix_skills_skill_type", "skill_type"),
        Index("ix_skills_runtime", "runtime"),
        Index("ix_skills_created_at", "created_at"),
        Index("ix_skills_agent_id", "agent_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("skill_agents.id"), nullable=False)

    # Identity
    urn: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    version_major: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version_patch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True)
    is_deprecated: Mapped[bool] = mapped_column(Boolean, default=False)

    # Classification
    skill_type: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)

    # Manifest (full JSON-LD)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Payload (blob store)
    payload_key: Mapped[str] = mapped_column(String(512), nullable=False)
    payload_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_hash_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_point: Mapped[str] = mapped_column(String(256), nullable=False)

    # Dependencies
    requirements: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    system_deps: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    ros2_package: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Hardware
    hardware_requirements: Mapped[dict] = mapped_column(JSONB, default=dict)

    # I/O contract (denormalised for quick API responses)
    inputs_schema: Mapped[list] = mapped_column(JSONB, default=list)
    outputs_schema: Mapped[list] = mapped_column(JSONB, default=list)

    # Licensing + visibility
    license: Mapped[str] = mapped_column(String(32), default="MIT")
    visibility: Mapped[str] = mapped_column(String(16), default="public")

    # Forking
    forked_from_urn: Mapped[str | None] = mapped_column(String(512), nullable=True)
    fork_count: Mapped[int] = mapped_column(Integer, default=0)

    # Provenance
    generator: Mapped[str | None] = mapped_column(String(64), nullable=True)
    llm_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_signature: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Stats
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    run_success_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    rating_count: Mapped[int] = mapped_column(Integer, default=0)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    # Vector embedding for semantic search
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)

    agent: Mapped["Agent"] = relationship("Agent", back_populates="skills")
    runs: Mapped[list["SkillRun"]] = relationship("SkillRun", back_populates="skill")
    ratings: Mapped[list["SkillRating"]] = relationship("SkillRating", back_populates="skill")


class SkillRun(Base):
    """One row per skill execution."""
    __tablename__ = "skill_runs"
    __table_args__ = (
        Index("ix_runs_skill_id", "skill_id"),
        Index("ix_runs_created_at", "created_at"),
        Index("ix_runs_caller_agent_id", "caller_agent_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    skill_id: Mapped[str] = mapped_column(String(36), ForeignKey("skills.id"), nullable=False)
    caller_agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    status: Mapped[str] = mapped_column(String(16), default="pending")
    # pending | running | success | failed | timeout | cancelled

    inputs: Mapped[dict] = mapped_column(JSONB, default=dict)
    outputs: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    peak_memory_mb: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    skill: Mapped["Skill"] = relationship("Skill", back_populates="runs")


class SkillRating(Base):
    """Agent ratings for published skills."""
    __tablename__ = "skill_ratings"
    __table_args__ = (
        UniqueConstraint("skill_id", "rater_agent_id", name="uq_rating_skill_agent"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    skill_id: Mapped[str] = mapped_column(String(36), ForeignKey("skills.id"), nullable=False)
    rater_agent_id: Mapped[str] = mapped_column(String(36), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)   # 1-5
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    skill: Mapped["Skill"] = relationship("Skill", back_populates="ratings")
