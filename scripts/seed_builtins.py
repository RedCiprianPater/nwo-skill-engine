"""
Seed script — packs all built-in skills and publishes them to the skill engine.

Usage:
    python scripts/seed_builtins.py --api http://localhost:8003 --agent-id <agent-id>

Creates a "NWO System" agent if none is provided, then publishes all
skills in skills/builtins/ to the registry.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import tarfile
from pathlib import Path

import httpx

BUILTINS_DIR = Path(__file__).parent.parent / "skills" / "builtins"


async def register_system_agent(api_url: str) -> str:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(f"{api_url}/agents/register", json={
            "name": "NWO System",
            "public_key": "nwo-system-builtin-agent-public-key-v1",
            "metadata": {"role": "system", "description": "Built-in skill publisher"},
        })
        r.raise_for_status()
        return r.json()["id"]


def pack_skill(skill_dir: Path) -> bytes:
    """Pack a skill directory into a .tar.gz bytes object."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f in skill_dir.rglob("*"):
            if f.is_file() and "__pycache__" not in str(f) and not f.suffix == ".pyc":
                tar.add(f, arcname=f.relative_to(skill_dir))
    buf.seek(0)
    return buf.read()


async def publish_skill(api_url: str, agent_id: str, skill_dir: Path) -> dict | None:
    manifest_path = skill_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"  [skip] No manifest.json in {skill_dir.name}")
        return None

    manifest_text = manifest_path.read_text()
    payload_bytes = pack_skill(skill_dir)

    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f"{api_url}/skills/publish",
            headers={"X-Agent-ID": agent_id},
            files={"payload": ("skill.tar.gz", payload_bytes, "application/gzip")},
            data={"manifest": manifest_text},
        )
        if r.status_code == 200:
            data = r.json()
            print(f"  [ok] {data['name']} v{data['version']} — {data['urn']}")
            return data
        else:
            print(f"  [fail] {skill_dir.name}: {r.status_code} {r.text[:120]}")
            return None


async def main(api_url: str, agent_id: str | None):
    print(f"Seeding built-in skills to {api_url}")

    if not agent_id:
        print("Registering NWO System agent...")
        agent_id = await register_system_agent(api_url)
        print(f"  Agent ID: {agent_id}")

    skill_dirs = [d for d in BUILTINS_DIR.iterdir() if d.is_dir() and (d / "manifest.json").exists()]
    print(f"\nPublishing {len(skill_dirs)} built-in skill(s)...")

    results = []
    for skill_dir in sorted(skill_dirs):
        result = await publish_skill(api_url, agent_id, skill_dir)
        if result:
            results.append(result)

    print(f"\nDone. Published {len(results)}/{len(skill_dirs)} skills.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8003")
    parser.add_argument("--agent-id", default=None)
    args = parser.parse_args()
    asyncio.run(main(args.api, args.agent_id))
