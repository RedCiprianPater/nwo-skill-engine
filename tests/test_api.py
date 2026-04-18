"""
FastAPI integration tests for the skill engine.
Uses in-memory SQLite and mocked blob storage.
"""

from __future__ import annotations

import io
import json
import tarfile
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.api.main import app
from src.models.database import get_session
from src.models.orm import Base

# ── In-memory test database ───────────────────────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DB_URL, echo=False)
TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_session():
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
def client():
    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _register_agent(client) -> str:
    r = client.post("/agents/register", json={
        "name": "Test Bot",
        "public_key": "pk-" + uuid.uuid4().hex[:12],
    })
    assert r.status_code == 200
    return r.json()["id"]


def _make_tarball(entry_point: str = "main.py") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        code = b"import json, os\nprint(json.dumps({'result': 'ok'}))"
        info = tarfile.TarInfo(name=entry_point)
        info.size = len(code)
        tar.addfile(info, io.BytesIO(code))
    buf.seek(0)
    return buf.read()


def _minimal_manifest(name: str = "Test Skill", entry_point: str = "main.py") -> str:
    return json.dumps({
        "name": name,
        "version": "1.0.0",
        "skill_type": "calibration",
        "runtime": "python",
        "entry_point": entry_point,
        "description": "Integration test skill",
        "tags": ["test"],
        "license": "MIT",
    })


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_skill_types_endpoint(client):
    r = client.get("/skills/types")
    assert r.status_code == 200
    data = r.json()
    assert "skill_types" in data
    assert "calibration" in data["skill_types"]
    assert "runtimes" in data
    assert "python" in data["runtimes"]


def test_register_agent(client):
    r = client.post("/agents/register", json={
        "name": "My Robot",
        "public_key": "pk-" + uuid.uuid4().hex,
    })
    assert r.status_code == 200
    assert "id" in r.json()


def test_register_agent_idempotent(client):
    pk = "pk-" + uuid.uuid4().hex
    r1 = client.post("/agents/register", json={"name": "Bot A", "public_key": pk})
    r2 = client.post("/agents/register", json={"name": "Bot B", "public_key": pk})
    assert r1.json()["id"] == r2.json()["id"]


def test_search_empty_returns_results(client):
    r = client.get("/skills/search")
    assert r.status_code == 200
    data = r.json()
    assert "results" in data
    assert "total" in data
    assert data["total"] == 0


def test_publish_requires_agent_header(client):
    r = client.post(
        "/skills/publish",
        files={"payload": ("skill.tar.gz", _make_tarball(), "application/gzip")},
        data={"manifest": _minimal_manifest()},
    )
    assert r.status_code == 401


@patch("src.publisher.publish._upload_bytes", return_value="http://localhost/skill.tar.gz")
@patch("src.registry.search.embed_skill_text", new_callable=AsyncMock, return_value=[])
def test_publish_skill_success(mock_embed, mock_upload, client):
    agent_id = _register_agent(client)
    r = client.post(
        "/skills/publish",
        headers={"X-Agent-ID": agent_id},
        files={"payload": ("skill.tar.gz", _make_tarball(), "application/gzip")},
        data={"manifest": _minimal_manifest()},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Test Skill"
    assert data["version"] == "1.0.0"
    assert data["urn"].startswith("urn:nwo:skill:")


@patch("src.publisher.publish._upload_bytes", return_value="http://localhost/skill.tar.gz")
@patch("src.registry.search.embed_skill_text", new_callable=AsyncMock, return_value=[])
def test_publish_creates_new_version(mock_embed, mock_upload, client):
    agent_id = _register_agent(client)

    def publish():
        return client.post(
            "/skills/publish",
            headers={"X-Agent-ID": agent_id},
            files={"payload": ("skill.tar.gz", _make_tarball(), "application/gzip")},
            data={"manifest": _minimal_manifest()},
        )

    r1 = publish()
    r2 = publish()
    assert r1.status_code == 200
    assert r2.status_code == 200
    # Both succeed — second is a new version
    assert r1.json()["urn"] != r2.json()["urn"]


def test_publish_invalid_manifest(client):
    agent_id = _register_agent(client)
    r = client.post(
        "/skills/publish",
        headers={"X-Agent-ID": agent_id},
        files={"payload": ("skill.tar.gz", _make_tarball(), "application/gzip")},
        data={"manifest": json.dumps({"name": "incomplete"})},  # Missing required fields
    )
    assert r.status_code == 422


def test_get_skill_not_found(client):
    r = client.get("/skills/does-not-exist")
    assert r.status_code == 404


def test_search_by_skill_type(client):
    r = client.get("/skills/search", params={"skill_type": "calibration"})
    assert r.status_code == 200


def test_rate_skill_not_found(client):
    r = client.put("/skills/no-such-skill/rate", json={
        "rating": 5, "rater_agent_id": "agent-x"
    })
    assert r.status_code == 404


def test_deprecate_requires_auth(client):
    r = client.delete("/skills/some-skill-id")
    assert r.status_code == 401


def test_agent_skills_empty(client):
    agent_id = _register_agent(client)
    r = client.get(f"/agents/{agent_id}/skills")
    assert r.status_code == 200
    assert r.json()["total"] == 0
