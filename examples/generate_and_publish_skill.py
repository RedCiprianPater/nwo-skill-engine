"""
Example: Autonomous skill generation agent.
Uses the Claude API to write a skill from a natural language description,
then publishes it to the skill engine without human intervention.

This is the Layer 4 equivalent of the Layer 1 design engine:
  Natural language → parametric script (L1)  →  STL file
  Natural language → Python skill code  (L4)  →  published skill

Run:
  ANTHROPIC_API_KEY=... python examples/generate_and_publish_skill.py
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import tarfile

import httpx

SKILL_URL = os.getenv("SKILL_URL", "http://localhost:8003")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

SYSTEM_PROMPT = """You are an expert robot skill engineer writing skills for the NWO Robotics platform.

A skill is a Python script that:
1. Reads inputs from the environment variable NWO_SKILL_INPUTS (JSON string)
2. Performs its computation or hardware action
3. Writes outputs to NWO_SKILL_OUTPUT_FILE (path from environment variable), as a JSON dict

Rules:
- Output ONLY the Python code. No markdown fences, no explanation.
- Always import json and os at the top.
- Always read: inputs = json.loads(os.environ.get("NWO_SKILL_INPUTS", "{}"))
- Always write outputs to: open(os.environ.get("NWO_SKILL_OUTPUT_FILE", "outputs.json"), "w")
- Handle missing inputs gracefully with .get() and defaults.
- Keep it focused, clean, and well-commented.
- Use only stdlib unless hardware-specific packages are truly required.
"""

MANIFEST_PROMPT = """Given this skill description, produce a JSON manifest object.
Return ONLY valid JSON, no markdown fences.
Required fields: name, version (use "1.0.0"), skill_type, runtime (use "python"),
entry_point (use "skill.py"), description, tags (array), inputs (array of {name, type, description, required}),
outputs (array of {name, type, description}), requirements (array, stdlib only = []),
license ("MIT"), visibility ("public"), timeout_sec (int), max_memory_mb (int).

skill_type must be one of: motion_primitive, vision, calibration, assembly,
sensor_fusion, navigation, communication, tool_use, meta, other.

Skill description: """


async def generate_skill(description: str) -> tuple[str, dict]:
    """Use Claude to generate Python skill code + manifest for a given description."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Generate code
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-opus-4-5",
                "max_tokens": 2048,
                "messages": [
                    {"role": "user", "content": f"Write this skill:\n{description}"}
                ],
                "system": SYSTEM_PROMPT,
            },
        )
        r.raise_for_status()
        code = r.json()["content"][0]["text"].strip()
        # Strip accidental markdown fences
        code = re.sub(r"```python\s*|```\s*", "", code).strip()

        # Generate manifest
        r2 = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-opus-4-5",
                "max_tokens": 1024,
                "messages": [
                    {"role": "user", "content": MANIFEST_PROMPT + description}
                ],
            },
        )
        r2.raise_for_status()
        manifest_text = r2.json()["content"][0]["text"].strip()
        manifest_text = re.sub(r"```json\s*|```\s*", "", manifest_text).strip()
        manifest = json.loads(manifest_text)

    return code, manifest


def _pack(code: str, entry_point: str = "skill.py") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        content = code.encode()
        info = tarfile.TarInfo(name=entry_point)
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


async def main():
    if not ANTHROPIC_API_KEY:
        print("Set ANTHROPIC_API_KEY to run this example.")
        return

    async with httpx.AsyncClient(timeout=120.0) as client:

        # Register agent
        r = await client.post(f"{SKILL_URL}/agents/register", json={
            "name": "Autonomous Skill Generator",
            "public_key": "autogen-agent-demo-key",
        })
        agent_id = r.json()["id"]
        print(f"Agent: {agent_id}\n")

        descriptions = [
            "A skill that takes a list of joint angles in degrees and converts them to radians. Inputs: angles (list of float). Outputs: radians (list of float), max_angle_deg (float).",
            "A skill that takes a target_position dict with x,y,z keys and a current_position dict with the same keys, and computes the Euclidean distance between them. Output: distance_mm (float), direction_vector (list of 3 floats).",
        ]

        for description in descriptions:
            print(f"Generating skill: {description[:60]}...")

            try:
                code, manifest = await generate_skill(description)
                print(f"  Code: {len(code)} chars | Manifest: {manifest.get('name')}")

                payload = _pack(code, manifest.get("entry_point", "skill.py"))

                r = await client.post(
                    f"{SKILL_URL}/skills/publish",
                    headers={"X-Agent-ID": agent_id},
                    files={"payload": ("skill.tar.gz", payload, "application/gzip")},
                    data={"manifest": json.dumps(manifest)},
                )

                if r.status_code == 200:
                    pub = r.json()
                    print(f"  ✓ Published: {pub['name']} v{pub['version']}")
                    print(f"    URN: {pub['urn']}\n")
                else:
                    print(f"  ✗ Publish failed: {r.status_code} — {r.text}\n")

            except Exception as e:
                print(f"  ✗ Error: {e}\n")
                continue

    print("Done. Browse skills at: " + SKILL_URL + "/docs")


if __name__ == "__main__":
    asyncio.run(main())
