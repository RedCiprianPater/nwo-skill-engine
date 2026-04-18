"""Tests for the skill publish pipeline."""

from __future__ import annotations

import io
import json
import tarfile

import pytest

from src.models.manifest import SkillManifest
from src.publisher.publish import validate_manifest_json, validate_payload


def _make_tarball(files: dict[str, str]) -> bytes:
    """Create an in-memory .tar.gz with the given filename→content mapping."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf.read()


def _minimal_manifest(**overrides) -> dict:
    base = {
        "name": "Test Skill",
        "version": "1.0.0",
        "skill_type": "calibration",
        "runtime": "python",
        "entry_point": "main.py",
    }
    base.update(overrides)
    return base


# ── Manifest validation ────────────────────────────────────────────────────────

def test_validate_manifest_json_valid():
    m = validate_manifest_json(_minimal_manifest())
    assert isinstance(m, SkillManifest)
    assert m.name == "Test Skill"


def test_validate_manifest_json_missing_required_field():
    with pytest.raises(ValueError, match="Manifest validation failed"):
        validate_manifest_json({"name": "Incomplete"})


def test_validate_manifest_json_bad_semver():
    with pytest.raises(Exception):
        validate_manifest_json(_minimal_manifest(version="not.semver"))


# ── Payload validation ────────────────────────────────────────────────────────

def test_validate_payload_valid():
    manifest = validate_manifest_json(_minimal_manifest(entry_point="main.py"))
    payload = _make_tarball({"main.py": "print('hello')", "README.md": "# skill"})
    validate_payload(payload, manifest)  # Should not raise


def test_validate_payload_missing_entry_point():
    manifest = validate_manifest_json(_minimal_manifest(entry_point="main.py"))
    payload = _make_tarball({"other.py": "print('hi')"})
    with pytest.raises(ValueError, match="Entry point 'main.py' not found"):
        validate_payload(payload, manifest)


def test_validate_payload_not_a_tarball():
    manifest = validate_manifest_json(_minimal_manifest())
    with pytest.raises(ValueError, match="not a valid .tar.gz"):
        validate_payload(b"this is not a tarball", manifest)


def test_validate_payload_too_large(monkeypatch):
    import src.publisher.publish as pub
    monkeypatch.setattr(pub, "_MAX_MB", 0)  # Set limit to 0 MB
    manifest = validate_manifest_json(_minimal_manifest(entry_point="main.py"))
    payload = _make_tarball({"main.py": "x"})
    with pytest.raises(ValueError, match="too large"):
        validate_payload(payload, manifest)


def test_validate_payload_entry_point_in_subdir():
    """Entry point can be in a subdirectory — e.g. 'src/main.py'."""
    manifest = validate_manifest_json(_minimal_manifest(entry_point="src/main.py"))
    payload = _make_tarball({"src/main.py": "print('hi')", "manifest.json": "{}"})
    validate_payload(payload, manifest)  # Should not raise


# ── Builtin skills are valid ───────────────────────────────────────────────────

def test_servo_calibration_manifest_valid():
    from pathlib import Path
    p = Path("skills/builtins/servo_calibration/manifest.json")
    if p.exists():
        data = json.loads(p.read_text())
        m = validate_manifest_json(data)
        assert m.name == "Servo Calibration"


def test_object_detection_manifest_valid():
    from pathlib import Path
    p = Path("skills/builtins/object_detection/manifest.json")
    if p.exists():
        data = json.loads(p.read_text())
        m = validate_manifest_json(data)
        assert m.skill_type.value == "vision"


def test_assembly_sequence_manifest_valid():
    from pathlib import Path
    p = Path("skills/builtins/assembly_sequence/manifest.json")
    if p.exists():
        data = json.loads(p.read_text())
        m = validate_manifest_json(data)
        assert m.allow_network is True
