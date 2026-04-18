"""
Example: An autonomous agent writes, packs, and publishes a skill.

This is the full agent-authored skill loop:
  1. Agent registers with the skill engine
  2. Agent describes a capability in natural language
  3. Claude generates the skill script and manifest
  4. Agent packs and publishes to the registry
  5. Agent searches the registry to confirm publication
  6. Agent invokes the skill

Run with:
    python examples/agent_publishes_skill.py
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import tarfile
import tempfile

import httpx

SKILL_API = os.getenv("SKILL_API_URL", "http://localhost:8003")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")


# ── Step 1: Generate skill code with Claude ────────────────────────────────────

async def generate_skill_with_claude(description: str) -> tuple[str, str]:
    """
    Ask Claude to generate a skill script and manifest.
    Returns (script_code, manifest_json).
    """
    if not ANTHROPIC_KEY:
        # Return a simple stub for testing without an API key
        script = '''import json, os
def load_inputs(): return json.loads(os.environ.get("NWO_SKILL_INPUTS", "{}"))
def write_outputs(d):
    f = os.environ.get("NWO_SKILL_OUTPUT_FILE")
    open(f, "w").write(json.dumps(d)) if f else print(json.dumps(d))
def main():
    inputs = load_inputs()
    result = sum(inputs.get("values", []))
    write_outputs({"sum": result, "count": len(inputs.get("values", []))})
if __name__ == "__main__": main()
'''
        manifest = json.dumps({
            "name": "Number Summation",
            "version": "1.0.0",
            "skill_type": "other",
            "runtime": "python",
            "entry_point": "main.py",
            "description": "Sums a list of numbers",
            "inputs": [{"name": "values", "type": "list", "required": True}],
            "outputs": [{"name": "sum", "type": "float"}, {"name": "count", "type": "int"}],
            "license": "MIT",
        })
        return script, manifest

    import anthropic
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_KEY)

    system = """You are an expert at writing NWO Robot skill files.
Generate a Python skill script and a JSON manifest for the described capability.
Output ONLY valid JSON with two keys: "script" (Python code as string) and "manifest" (dict).
The script must read inputs from os.environ["NWO_SKILL_INPUTS"] (JSON) and write outputs to os.environ["NWO_SKILL_OUTPUT_FILE"].
The manifest must have: name, version (1.0.0), skill_type, runtime (python), entry_point (main.py), inputs, outputs, license (MIT)."""

    response = await client.messages.create(
        model="claude-opus-4-5",
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": f"Write a skill for: {description}"}],
    )

    raw = response.content[0].text.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:-1])

    data = json.loads(raw)
    return data["script"], json.dumps(data["manifest"])


# ── Step 2: Pack into .tar.gz ─────────────────────────────────────────────────

def pack_skill(script_code: str, entry_point: str = "main.py") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        code_bytes = script_code.encode()
        info = tarfile.TarInfo(name=entry_point)
        info.size = len(code_bytes)
        tar.addfile(info, io.BytesIO(code_bytes))
    buf.seek(0)
    return buf.read()


# ── Main flow ─────────────────────────────────────────────────────────────────

async def main():
    async with httpx.AsyncClient(timeout=60.0) as client:

        # 1. Register agent
        print("Registering agent...")
        r = await client.post(f"{SKILL_API}/agents/register", json={
            "name": "Autonomous Design Bot",
            "public_key": "demo-key-" + "a" * 16,
        })
        r.raise_for_status()
        agent_id = r.json()["id"]
        print(f"  Agent ID: {agent_id}")

        # 2. Generate skill
        description = "A skill that reads joint angles from a robot arm and returns forward kinematics position"
        print(f"\nGenerating skill: '{description[:60]}...'")
        script_code, manifest_json = await generate_skill_with_claude(description)

        manifest = json.loads(manifest_json)
        print(f"  Skill name : {manifest.get('name')}")
        print(f"  Skill type : {manifest.get('skill_type')}")
        print(f"  Inputs     : {[i['name'] for i in manifest.get('inputs', [])]}")

        # 3. Pack
        payload_bytes = pack_skill(script_code, manifest.get("entry_point", "main.py"))
        print(f"\nPacked payload: {len(payload_bytes) / 1024:.1f} KB")

        # 4. Publish
        print("\nPublishing to skill registry...")
        r = await client.post(
            f"{SKILL_API}/skills/publish",
            headers={"X-Agent-ID": agent_id},
            files={"payload": ("skill.tar.gz", payload_bytes, "application/gzip")},
            data={"manifest": manifest_json},
        )
        r.raise_for_status()
        pub = r.json()
        skill_id = pub["skill_id"]
        print(f"  Published : {pub['name']} v{pub['version']}")
        print(f"  URN       : {pub['urn']}")

        # 5. Confirm via search
        print("\nSearching registry to confirm...")
        r = await client.get(f"{SKILL_API}/skills/search",
                             params={"q": manifest.get("name", ""), "limit": 3})
        results = r.json()
        print(f"  Found {results['total']} result(s)")

        # 6. Execute the skill
        print("\nExecuting skill...")
        r = await client.post(f"{SKILL_API}/skills/{skill_id}/run", json={
            "inputs": {"values": [1, 2, 3, 4, 5]},
            "caller_agent_id": agent_id,
        })
        run = r.json()
        print(f"  Status     : {run['status']}")
        print(f"  Duration   : {run.get('duration_ms', '?')}ms")
        print(f"  Outputs    : {json.dumps(run.get('outputs', {}), indent=4)}")


if __name__ == "__main__":
    asyncio.run(main())
