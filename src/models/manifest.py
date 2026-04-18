"""
Skill manifest — the JSON-LD descriptor that accompanies every skill payload.

A manifest is the contract between a skill publisher and a skill consumer.
It describes what the skill does, what it needs, and how to run it.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ── Enums ──────────────────────────────────────────────────────────────────────

class SkillType(str, Enum):
    motion_primitive = "motion_primitive"
    vision = "vision"
    calibration = "calibration"
    assembly = "assembly"
    sensor_fusion = "sensor_fusion"
    navigation = "navigation"
    communication = "communication"
    tool_use = "tool_use"
    meta = "meta"
    other = "other"


class Runtime(str, Enum):
    python = "python"
    javascript = "javascript"
    ros2 = "ros2"
    shell = "shell"
    wasm = "wasm"


class SkillLicense(str, Enum):
    mit = "MIT"
    apache2 = "Apache-2.0"
    gpl3 = "GPL-3.0"
    cc0 = "CC0"
    cc_by = "CC-BY"
    proprietary = "proprietary"


class SkillVisibility(str, Enum):
    public = "public"
    private = "private"
    org = "org"


# ── I/O port definitions ──────────────────────────────────────────────────────

class PortType(str, Enum):
    int = "int"
    float = "float"
    str = "str"
    bool = "bool"
    dict = "dict"
    list = "list"
    bytes = "bytes"
    any = "any"


class InputPort(BaseModel):
    name: str
    type: PortType = PortType.any
    description: str = ""
    required: bool = True
    default: Any = None


class OutputPort(BaseModel):
    name: str
    type: PortType = PortType.any
    description: str = ""


# ── Hardware requirements ─────────────────────────────────────────────────────

class HardwareRequirements(BaseModel):
    """What physical hardware this skill requires."""
    robot_types: list[str] = Field(default_factory=list)
    # e.g. ["unitree_g1", "generic_6dof_arm"]
    sensors: list[str] = Field(default_factory=list)
    # e.g. ["rgb_camera", "imu", "force_torque"]
    actuators: list[str] = Field(default_factory=list)
    # e.g. ["servo", "stepper", "pneumatic_gripper"]
    communication: list[str] = Field(default_factory=list)
    # e.g. ["serial_usb", "can_bus", "ethernet"]
    min_compute: str | None = None
    # e.g. "raspberry_pi_4", "jetson_nano", "x86_64"


# ── Skill manifest ────────────────────────────────────────────────────────────

class SkillManifest(BaseModel):
    """
    JSON-LD manifest for a published skill.
    This is the authoritative descriptor — stored as JSON alongside the payload.
    """

    # JSON-LD identity
    context: str = Field(
        default="https://nworobotics.cloud/skill/v1",
        alias="@context",
    )
    id: str | None = Field(
        default=None,
        alias="@id",
        description="URN assigned on publish: urn:nwo:skill:{slug}:{version}",
    )

    # Core identity
    name: str = Field(..., min_length=3, max_length=128)
    version: str = Field(..., description="Semantic version string, e.g. '1.2.0'")
    slug: str | None = None   # Computed from name on publish
    skill_type: SkillType
    runtime: Runtime
    description: str | None = Field(default=None, max_length=4096)
    tags: list[str] = Field(default_factory=list, max_length=20)

    # Entry point within the payload archive
    entry_point: str = Field(
        ...,
        description="Relative path to the main script/module within the payload .tar.gz",
        example="main.py",
    )

    # I/O contract
    inputs: list[InputPort] = Field(default_factory=list)
    outputs: list[OutputPort] = Field(default_factory=list)

    # Dependencies
    requirements: list[str] = Field(
        default_factory=list,
        description="Python pip requirements (for python runtime), npm packages for JS, etc.",
    )
    ros2_package: str | None = Field(
        default=None,
        description="ROS2 package name (for ros2 runtime)",
    )
    system_deps: list[str] = Field(
        default_factory=list,
        description="apt/brew packages needed in the execution environment",
    )

    # Hardware
    hardware: HardwareRequirements = Field(default_factory=HardwareRequirements)

    # Publishing metadata
    agent_id: str | None = None
    signature: str | None = None       # hex ed25519 signature
    license: SkillLicense = SkillLicense.mit
    visibility: SkillVisibility = SkillVisibility.public
    forked_from: str | None = None     # URN of parent skill if forked

    # Provenance
    generator: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    source_prompt: str | None = None

    # Runtime config
    timeout_sec: int = Field(default=120, ge=1, le=3600)
    max_memory_mb: int = Field(default=512, ge=64, le=8192)
    allow_network: bool = False
    allow_filesystem: bool = False

    model_config = {"populate_by_name": True}

    @field_validator("version")
    @classmethod
    def validate_semver(cls, v: str) -> str:
        if not re.match(r"^\d+\.\d+\.\d+", v):
            raise ValueError(f"Version must be a semver string (e.g. '1.0.0'), got '{v}'")
        return v

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, v: list[str]) -> list[str]:
        return [re.sub(r"[^a-z0-9\-_]", "", t.lower().strip())[:32] for t in v if t.strip()]

    def compute_slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")[:128]

    def compute_urn(self) -> str:
        slug = self.slug or self.compute_slug()
        return f"urn:nwo:skill:{slug}:{self.version}"

    def to_jsonld(self) -> dict:
        d = self.model_dump(by_alias=True, exclude_none=True)
        d["@context"] = self.context
        if self.id:
            d["@id"] = self.id
        return d


# ── JSON Schema for manifest validation ───────────────────────────────────────

MANIFEST_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "NWO Skill Manifest",
    "type": "object",
    "required": ["name", "version", "skill_type", "runtime", "entry_point"],
    "properties": {
        "name": {"type": "string", "minLength": 3, "maxLength": 128},
        "version": {"type": "string", "pattern": r"^\d+\.\d+\.\d+"},
        "skill_type": {"type": "string", "enum": [t.value for t in SkillType]},
        "runtime": {"type": "string", "enum": [r.value for r in Runtime]},
        "entry_point": {"type": "string"},
        "description": {"type": "string", "maxLength": 4096},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        "inputs": {"type": "array"},
        "outputs": {"type": "array"},
        "requirements": {"type": "array", "items": {"type": "string"}},
        "license": {"type": "string", "enum": [l.value for l in SkillLicense]},
    },
}
