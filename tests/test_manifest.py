"""Tests for skill manifest validation."""

from __future__ import annotations

import pytest
from src.models.manifest import Runtime, SkillManifest, SkillType


def _valid_manifest(**overrides) -> dict:
    base = {
        "name": "Test Skill",
        "version": "1.0.0",
        "skill_type": "calibration",
        "runtime": "python",
        "entry_point": "main.py",
        "description": "A test skill",
        "tags": ["test"],
        "license": "MIT",
    }
    base.update(overrides)
    return base


def test_valid_manifest_parses():
    m = SkillManifest.model_validate(_valid_manifest())
    assert m.name == "Test Skill"
    assert m.version == "1.0.0"
    assert m.skill_type == SkillType.calibration
    assert m.runtime == Runtime.python


def test_invalid_semver_raises():
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="semver"):
        SkillManifest.model_validate(_valid_manifest(version="1.0"))


def test_urn_generation():
    m = SkillManifest.model_validate(_valid_manifest(name="Servo Calibration"))
    assert m.compute_urn() == "urn:nwo:skill:servo-calibration:1.0.0"


def test_slug_from_name():
    m = SkillManifest.model_validate(_valid_manifest(name="My Cool Skill 2.0"))
    assert m.compute_slug() == "my-cool-skill-2-0"


def test_tags_are_cleaned():
    m = SkillManifest.model_validate(_valid_manifest(tags=["SERVO", "My Tag!", "ok_tag"]))
    assert "servo" in m.tags
    assert "ok_tag" in m.tags
    # Uppercase and special chars are stripped
    assert all(t == t.lower() for t in m.tags)


def test_default_visibility_is_public():
    m = SkillManifest.model_validate(_valid_manifest())
    assert m.visibility.value == "public"


def test_input_ports_parse():
    manifest = _valid_manifest(inputs=[
        {"name": "servo_id", "type": "int", "required": True},
        {"name": "range_deg", "type": "float", "default": 180.0, "required": False},
    ])
    m = SkillManifest.model_validate(manifest)
    assert len(m.inputs) == 2
    assert m.inputs[0].name == "servo_id"
    assert m.inputs[1].default == 180.0


def test_to_jsonld_includes_context():
    m = SkillManifest.model_validate(_valid_manifest())
    ld = m.to_jsonld()
    assert "@context" in ld
    assert ld["@context"] == "https://nworobotics.cloud/skill/v1"


def test_hardware_requirements_default_empty():
    m = SkillManifest.model_validate(_valid_manifest())
    assert m.hardware.sensors == []
    assert m.hardware.actuators == []


def test_all_skill_types_valid():
    for st in SkillType:
        m = SkillManifest.model_validate(_valid_manifest(skill_type=st.value))
        assert m.skill_type == st


def test_all_runtimes_valid():
    for rt in Runtime:
        m = SkillManifest.model_validate(_valid_manifest(runtime=rt.value))
        assert m.runtime == rt
