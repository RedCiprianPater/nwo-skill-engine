"""
Tests for skill execution and search service.
"""

from __future__ import annotations

import io
import json
import os
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("STORAGE_BUCKET", "test-bucket")
os.environ.setdefault("EMBEDDING_PROVIDER", "none")
os.environ.setdefault("RUNTIME_BACKEND", "subprocess")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_payload(entry_point: str, code: str) -> bytes:
    """Create a minimal .tar.gz payload with the given entry point and code."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        content = code.encode()
        info = tarfile.TarInfo(name=entry_point)
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _make_skill_record(skill_id: str, entry_point: str = "main.py", runtime: str = "python"):
    """Create a minimal ORM-like Skill object for testing."""
    skill = MagicMock()
    skill.id = skill_id
    skill.urn = f"urn:nwo:skill:test:{skill_id}"
    skill.name = "Test Skill"
    skill.runtime = runtime
    skill.entry_point = entry_point
    skill.requirements = []
    skill.payload_key = f"skills/agent-test/{skill_id}.tar.gz"
    skill.is_deprecated = False
    return skill


# ── Executor tests ─────────────────────────────────────────────────────────────

class TestSubprocessExecutor:

    def test_simple_python_skill_succeeds(self, tmp_path):
        """A skill that writes JSON to NWO_SKILL_OUTPUT_FILE should succeed."""
        code = """
import json, os
outputs = {"result": 42, "message": "hello from skill"}
out_path = os.environ.get("NWO_SKILL_OUTPUT_FILE", "outputs.json")
with open(out_path, "w") as f:
    json.dump(outputs, f)
"""
        import asyncio
        from src.runtime.executor import _run_subprocess

        skill = _make_skill_record("test-simple")
        skill.requirements = []

        payload = _make_payload("main.py", code)
        import io as _io, tarfile as _tf
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        with _tf.open(fileobj=_io.BytesIO(payload), mode="r:gz") as tar:
            tar.extractall(run_dir)

        result = asyncio.run(_run_subprocess(skill, {}, run_dir, timeout=30))
        assert result["result"] == 42
        assert result["message"] == "hello from skill"

    def test_skill_receives_inputs(self, tmp_path):
        """Skill should read NWO_SKILL_INPUTS from environment."""
        code = """
import json, os
inputs = json.loads(os.environ.get("NWO_SKILL_INPUTS", "{}"))
out_path = os.environ.get("NWO_SKILL_OUTPUT_FILE", "outputs.json")
with open(out_path, "w") as f:
    json.dump({"echo": inputs.get("value", None)}, f)
"""
        import asyncio
        from src.runtime.executor import _run_subprocess
        import io as _io, tarfile as _tf

        skill = _make_skill_record("test-inputs")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        payload = _make_payload("main.py", code)
        with _tf.open(fileobj=_io.BytesIO(payload), mode="r:gz") as tar:
            tar.extractall(run_dir)

        result = asyncio.run(_run_subprocess(skill, {"value": "nwo-test"}, run_dir, timeout=30))
        assert result["echo"] == "nwo-test"

    def test_failing_skill_raises(self, tmp_path):
        """A skill that exits non-zero should raise RuntimeError."""
        code = "import sys; sys.exit(1)"
        import asyncio
        from src.runtime.executor import _run_subprocess
        import io as _io, tarfile as _tf

        skill = _make_skill_record("test-fail")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        payload = _make_payload("main.py", code)
        with _tf.open(fileobj=_io.BytesIO(payload), mode="r:gz") as tar:
            tar.extractall(run_dir)

        with pytest.raises(RuntimeError, match="failed"):
            asyncio.run(_run_subprocess(skill, {}, run_dir, timeout=30))

    def test_timeout_raises(self, tmp_path):
        """A skill that runs too long should raise a timeout error."""
        code = "import time; time.sleep(60)"
        import asyncio
        from src.runtime.executor import _run_subprocess
        import io as _io, tarfile as _tf

        skill = _make_skill_record("test-timeout")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        payload = _make_payload("main.py", code)
        with _tf.open(fileobj=_io.BytesIO(payload), mode="r:gz") as tar:
            tar.extractall(run_dir)

        with pytest.raises(RuntimeError, match="timed out"):
            asyncio.run(_run_subprocess(skill, {}, run_dir, timeout=1))

    def test_shell_runtime_blocked_by_default(self, tmp_path):
        """Shell runtime should be blocked unless explicitly enabled."""
        import asyncio
        from src.runtime.executor import _run_subprocess

        skill = _make_skill_record("test-shell", entry_point="run.sh", runtime="shell")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "run.sh").write_text("#!/bin/bash\necho '{}'")

        with pytest.raises(RuntimeError, match="Shell runtime is disabled"):
            asyncio.run(_run_subprocess(skill, {}, run_dir, timeout=10))


# ── Search tests ──────────────────────────────────────────────────────────────

class TestSearchService:

    @pytest.mark.asyncio
    async def test_embed_skill_text_returns_empty_without_key(self):
        """embed_skill_text should return [] when no API key is configured."""
        os.environ["EMBEDDING_PROVIDER"] = "openai"
        os.environ["OPENAI_API_KEY"] = ""

        from src.registry.search import embed_skill_text
        result = await embed_skill_text("test", None, "calibration", [], "python")
        assert result == []

    @pytest.mark.asyncio
    async def test_embed_skill_text_none_provider(self):
        """embed_skill_text returns [] immediately when provider is 'none'."""
        os.environ["EMBEDDING_PROVIDER"] = "none"
        from src.registry.search import embed_skill_text
        result = await embed_skill_text("test", "desc", "motion_primitive", ["tag"], "python")
        assert result == []

    def test_sort_expression_created_at(self):
        """_sort returns descending created_at for default sort."""
        from src.registry.search import _sort
        from src.models.orm import Skill
        expr = _sort("created_at")
        assert expr is not None

    def test_sort_expression_runs(self):
        """_sort returns descending run_count for 'runs'."""
        from src.registry.search import _sort
        expr = _sort("runs")
        assert expr is not None

    def test_to_summary_maps_fields(self):
        """_to_summary maps all ORM fields to SkillSummary correctly."""
        from datetime import datetime, timezone
        from src.registry.search import _to_summary

        skill = MagicMock()
        skill.id = "abc"
        skill.urn = "urn:nwo:skill:test:1.0.0"
        skill.name = "My Skill"
        skill.slug = "my-skill"
        skill.version = "1.0.0"
        skill.skill_type = "calibration"
        skill.runtime = "python"
        skill.description = "A test"
        skill.tags = ["test"]
        skill.license = "MIT"
        skill.visibility = "public"
        skill.run_count = 10
        skill.avg_rating = 4.5
        skill.rating_count = 3
        skill.fork_count = 1
        skill.agent_id = "agent-xyz"
        skill.is_latest = True
        skill.created_at = datetime.now(timezone.utc)

        summary = _to_summary(skill)
        assert summary.id == "abc"
        assert summary.name == "My Skill"
        assert summary.run_count == 10
        assert summary.avg_rating == 4.5
