"""
Skill publisher.
Validates the manifest, verifies the payload archive, stores to blob,
embeds for semantic search, and persists the skill record.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import tempfile
import uuid
from pathlib import Path

import jsonschema
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.manifest import MANIFEST_SCHEMA, SkillManifest
from ..models.orm import Agent, Skill
from ..models.schemas import PublishResponse

_MAX_MB = int(os.getenv("MAX_PAYLOAD_SIZE_MB", "50"))
_BUCKET = os.getenv("STORAGE_BUCKET", "nwo-skills")
_PUBLIC_URL = os.getenv("STORAGE_PUBLIC_URL", "http://localhost:9000/nwo-skills")


# ── Blob helpers ───────────────────────────────────────────────────────────────

def _get_s3():
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("STORAGE_ENDPOINT_URL"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "minioadmin"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "password123"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        config=Config(signature_version="s3v4"),
    )


def _upload_bytes(data: bytes, key: str, content_type: str = "application/octet-stream") -> str:
    _get_s3().put_object(Bucket=_BUCKET, Key=key, Body=data, ContentType=content_type)
    return f"{_PUBLIC_URL.rstrip('/')}/{key}"


def _skill_key(agent_id: str, skill_id: str) -> str:
    return f"skills/{agent_id}/{skill_id}.tar.gz"


def _manifest_key(agent_id: str, skill_id: str) -> str:
    return f"manifests/{agent_id}/{skill_id}.json"


# ── Validation ────────────────────────────────────────────────────────────────

def validate_manifest_json(manifest_dict: dict) -> SkillManifest:
    """
    Validate a manifest dict against the JSON schema, then parse into SkillManifest.
    Raises ValueError with a human-readable message on failure.
    """
    try:
        jsonschema.validate(manifest_dict, MANIFEST_SCHEMA)
    except jsonschema.ValidationError as e:
        raise ValueError(f"Manifest validation failed: {e.message}")
    return SkillManifest.model_validate(manifest_dict)


def validate_payload(payload_bytes: bytes, manifest: SkillManifest) -> None:
    """
    Validate that the payload archive:
    1. Is a valid .tar.gz
    2. Contains the entry point declared in the manifest
    3. Does not exceed size limits
    """
    if len(payload_bytes) > _MAX_MB * 1024 * 1024:
        raise ValueError(f"Payload too large: {len(payload_bytes) / 1e6:.1f} MB > {_MAX_MB} MB")

    try:
        with tarfile.open(fileobj=io.BytesIO(payload_bytes), mode="r:gz") as tar:
            names = tar.getnames()
    except tarfile.TarError as e:
        raise ValueError(f"Payload is not a valid .tar.gz archive: {e}")

    # Check entry point exists in archive
    entry = manifest.entry_point
    if not any(n == entry or n.endswith("/" + entry) for n in names):
        raise ValueError(
            f"Entry point '{entry}' not found in payload archive. "
            f"Archive contains: {', '.join(names[:10])}"
        )


# ── Main publish function ──────────────────────────────────────────────────────

async def publish_skill(
    db: AsyncSession,
    agent_id: str,
    manifest_dict: dict,
    payload_bytes: bytes,
) -> PublishResponse:
    """
    Full publish pipeline:
    1. Validate manifest + payload
    2. Compute version parts and slug
    3. Upload payload + manifest to blob store
    4. Generate embedding
    5. Persist Skill record
    6. Mark previous latest version as non-latest

    Returns PublishResponse.
    """
    # 1. Validate
    manifest = validate_manifest_json(manifest_dict)
    validate_payload(payload_bytes, manifest)

    # 2. Prepare identity
    skill_id = str(uuid.uuid4())
    slug = manifest.compute_slug()
    urn = manifest.compute_urn()
    manifest.slug = slug
    manifest.id = urn
    manifest.agent_id = agent_id

    version_parts = [int(x) for x in manifest.version.split(".")[:3]]
    vmaj, vmin, vpatch = version_parts[0], version_parts[1], version_parts[2]

    payload_hash = hashlib.sha256(payload_bytes).hexdigest()

    # 3. Upload to blob
    pkey = _skill_key(agent_id, skill_id)
    mkey = _manifest_key(agent_id, skill_id)
    payload_url = _upload_bytes(payload_bytes, pkey, "application/gzip")
    _upload_bytes(
        json.dumps(manifest.to_jsonld(), indent=2).encode(),
        mkey,
        "application/ld+json",
    )

    # 4. Embedding
    embedding: list[float] | None = None
    try:
        from ..registry.search import embed_skill_text
        embedding = await embed_skill_text(
            name=manifest.name,
            description=manifest.description,
            skill_type=manifest.skill_type.value,
            tags=manifest.tags,
            runtime=manifest.runtime.value,
        )
    except Exception:
        pass

    # 5. Deactivate previous latest
    existing_latest = (
        await db.execute(
            select(Skill).where(
                Skill.agent_id == agent_id,
                Skill.slug == slug,
                Skill.is_latest == True,   # noqa: E712
            )
        )
    ).scalar_one_or_none()

    if existing_latest:
        await db.execute(
            update(Skill).where(Skill.id == existing_latest.id).values(is_latest=False)
        )

    # 6. Persist
    skill = Skill(
        id=skill_id,
        agent_id=agent_id,
        urn=urn,
        name=manifest.name,
        slug=slug,
        version=manifest.version,
        version_major=vmaj,
        version_minor=vmin,
        version_patch=vpatch,
        is_latest=True,
        skill_type=manifest.skill_type.value,
        runtime=manifest.runtime.value,
        description=manifest.description,
        tags=manifest.tags,
        manifest=manifest.to_jsonld(),
        payload_key=pkey,
        payload_size_bytes=len(payload_bytes),
        payload_hash_sha256=payload_hash,
        entry_point=manifest.entry_point,
        requirements=manifest.requirements,
        system_deps=manifest.system_deps,
        ros2_package=manifest.ros2_package,
        hardware_requirements=manifest.hardware.model_dump(),
        inputs_schema=[i.model_dump() for i in manifest.inputs],
        outputs_schema=[o.model_dump() for o in manifest.outputs],
        license=manifest.license.value,
        visibility=manifest.visibility.value,
        forked_from_urn=manifest.forked_from,
        generator=manifest.generator,
        llm_provider=manifest.llm_provider,
        llm_model=manifest.llm_model,
        source_prompt=manifest.source_prompt,
        agent_signature=manifest.signature,
        embedding=embedding,
    )
    db.add(skill)
    await db.flush()

    return PublishResponse(
        skill_id=skill_id,
        urn=urn,
        name=manifest.name,
        version=manifest.version,
        payload_url=payload_url,
        message=f"Published '{manifest.name}' v{manifest.version} successfully.",
    )
