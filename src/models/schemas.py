"""Pydantic schemas for the Skill Engine API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .manifest import (
    HardwareRequirements,
    InputPort,
    OutputPort,
    Runtime,
    SkillLicense,
    SkillManifest,
    SkillType,
    SkillVisibility,
)


# ── Agent ──────────────────────────────────────────────────────────────────────

class AgentRegisterRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=128)
    public_key: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentResponse(BaseModel):
    id: str
    name: str
    is_active: bool
    created_at: datetime
    skill_count: int = 0
    model_config = {"from_attributes": True}


# ── Skill publish ──────────────────────────────────────────────────────────────

class PublishResponse(BaseModel):
    skill_id: str
    urn: str
    name: str
    version: str
    payload_url: str
    message: str


# ── Skill responses ────────────────────────────────────────────────────────────

class SkillSummary(BaseModel):
    id: str
    urn: str
    name: str
    slug: str
    version: str
    skill_type: str
    runtime: str
    description: str | None
    tags: list[str]
    license: str
    visibility: str
    run_count: int
    avg_rating: float | None
    rating_count: int
    fork_count: int
    agent_id: str
    is_latest: bool
    created_at: datetime
    model_config = {"from_attributes": True}


class SkillDetail(SkillSummary):
    manifest: dict[str, Any]
    inputs_schema: list[dict]
    outputs_schema: list[dict]
    requirements: list[str]
    system_deps: list[str]
    ros2_package: str | None
    hardware_requirements: dict
    forked_from_urn: str | None
    generator: str | None
    llm_provider: str | None
    llm_model: str | None
    source_prompt: str | None
    payload_url: str
    payload_size_bytes: int
    run_success_count: int
    agent: AgentResponse


# ── Search ─────────────────────────────────────────────────────────────────────

class SkillSearchResponse(BaseModel):
    total: int
    limit: int
    offset: int
    query: str | None
    results: list[SkillSummary]


# ── Execution ─────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    caller_agent_id: str | None = None
    timeout_sec: int | None = None


class RunResponse(BaseModel):
    run_id: str
    skill_id: str
    urn: str
    status: str
    outputs: dict[str, Any]
    error: str | None
    duration_ms: int | None
    peak_memory_mb: float | None


class RunRecord(BaseModel):
    id: str
    skill_id: str
    caller_agent_id: str | None
    status: str
    inputs: dict
    outputs: dict
    error: str | None
    duration_ms: int | None
    created_at: datetime
    model_config = {"from_attributes": True}


# ── Rating ────────────────────────────────────────────────────────────────────

class RateRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: str | None = None
    rater_agent_id: str


# ── Fork ─────────────────────────────────────────────────────────────────────

class ForkRequest(BaseModel):
    new_name: str | None = None
    visibility: SkillVisibility = SkillVisibility.public
    forker_agent_id: str
