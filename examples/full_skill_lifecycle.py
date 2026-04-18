"""
Example: Full agent skill lifecycle.
  1. Register an agent identity
  2. Write a skill (Python, in memory)
  3. Pack it into a .tar.gz payload
  4. Publish to the skill engine
  5. Search for it
  6. Execute it with inputs
  7. Rate it
  8. Fork it under a new name

Run with both Layer 4 running:
  nwo-skill serve &
  python examples/full_skill_lifecycle.py
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import tarfile

import httpx

SKILL_URL = os.getenv("SKILL_URL", "http://localhost:8003")


def _make_payload(entry_point: str, code: str) -> bytes:
    """Pack a single-file skill into a .tar.gz payload."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        content = code.encode()
        info = tarfile.TarInfo(name=entry_point)
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


SKILL_CODE = '''
"""
NWO Skill: Vector Dot Product
Computes the dot product of two numeric vectors.
Demonstrates: reading NWO_SKILL_INPUTS, writing NWO_SKILL_OUTPUT_FILE.
"""
import json
import os

inputs = json.loads(os.environ.get("NWO_SKILL_INPUTS", "{}"))
vec_a = inputs.get("vector_a", [])
vec_b = inputs.get("vector_b", [])

if len(vec_a) != len(vec_b):
    raise ValueError(f"Vectors must be same length: {len(vec_a)} vs {len(vec_b)}")

dot = sum(a * b for a, b in zip(vec_a, vec_b))
magnitude_a = sum(x**2 for x in vec_a) ** 0.5
magnitude_b = sum(x**2 for x in vec_b) ** 0.5
cosine_sim = dot / (magnitude_a * magnitude_b) if magnitude_a and magnitude_b else 0.0

outputs = {
    "dot_product": dot,
    "cosine_similarity": round(cosine_sim, 6),
    "vector_length": len(vec_a),
}

out_path = os.environ.get("NWO_SKILL_OUTPUT_FILE", "outputs.json")
with open(out_path, "w") as f:
    json.dump(outputs, f)

print(f"Dot product: {dot}, cosine similarity: {cosine_sim:.4f}")
'''

MANIFEST = {
    "name": "Vector Dot Product",
    "version": "1.0.0",
    "skill_type": "other",
    "runtime": "python",
    "entry_point": "dot_product.py",
    "description": "Computes dot product and cosine similarity of two numeric vectors.",
    "tags": ["math", "vectors", "linear-algebra", "embedding"],
    "inputs": [
        {"name": "vector_a", "type": "list", "description": "First vector (list of floats)", "required": True},
        {"name": "vector_b", "type": "list", "description": "Second vector (list of floats)", "required": True},
    ],
    "outputs": [
        {"name": "dot_product", "type": "float", "description": "Dot product result"},
        {"name": "cosine_similarity", "type": "float", "description": "Cosine similarity [-1, 1]"},
        {"name": "vector_length", "type": "int", "description": "Number of dimensions"},
    ],
    "requirements": [],
    "license": "MIT",
    "visibility": "public",
    "timeout_sec": 30,
    "max_memory_mb": 64,
}


async def main():
    async with httpx.AsyncClient(timeout=120.0) as client:

        # ── 1. Register agent ──────────────────────────────────────────────
        print("1. Registering agent...")
        r = await client.post(f"{SKILL_URL}/agents/register", json={
            "name": "NWO Demo Agent",
            "public_key": "demo-ed25519-pub-key-replace-in-production",
        })
        r.raise_for_status()
        agent = r.json()
        agent_id = agent["id"]
        print(f"   Agent ID: {agent_id}")

        # ── 2. Pack payload ────────────────────────────────────────────────
        print("\n2. Packing skill payload...")
        payload = _make_payload("dot_product.py", SKILL_CODE)
        print(f"   Payload size: {len(payload) / 1024:.1f} KB")

        # ── 3. Publish ─────────────────────────────────────────────────────
        print("\n3. Publishing skill...")
        r = await client.post(
            f"{SKILL_URL}/skills/publish",
            headers={"X-Agent-ID": agent_id},
            files={"payload": ("skill.tar.gz", payload, "application/gzip")},
            data={"manifest": json.dumps(MANIFEST)},
        )
        r.raise_for_status()
        pub = r.json()
        skill_id = pub["skill_id"]
        print(f"   ✓ Published: {pub['name']} v{pub['version']}")
        print(f"   URN: {pub['urn']}")

        # ── 4. Search ──────────────────────────────────────────────────────
        print("\n4. Searching for 'vector dot product'...")
        r = await client.get(f"{SKILL_URL}/skills/search", params={
            "q": "vector dot product",
            "skill_type": "other",
            "limit": 5,
        })
        results = r.json()
        print(f"   Found {results['total']} skill(s)")
        for s in results["results"]:
            rating = f"★{s['avg_rating']:.1f}" if s.get("avg_rating") else "unrated"
            print(f"   - {s['name']} v{s['version']} [{s['runtime']}] {rating}")

        # ── 5. Execute ─────────────────────────────────────────────────────
        print("\n5. Executing skill...")
        r = await client.post(f"{SKILL_URL}/skills/{skill_id}/run", json={
            "inputs": {
                "vector_a": [1.0, 0.0, 0.0],
                "vector_b": [0.0, 1.0, 0.0],
            },
            "caller_agent_id": agent_id,
        })
        r.raise_for_status()
        run = r.json()
        print(f"   Status: {run['status']}  ({run.get('duration_ms', '?')}ms)")
        if run["status"] == "success":
            print(f"   Outputs: {json.dumps(run['outputs'], indent=4)}")
        else:
            print(f"   Error: {run.get('error')}")

        # ── 6. Execute again with parallel vectors ─────────────────────────
        print("\n6. Executing with parallel vectors (should have cosine ≈ 1.0)...")
        r = await client.post(f"{SKILL_URL}/skills/{skill_id}/run", json={
            "inputs": {
                "vector_a": [3.0, 4.0, 0.0],
                "vector_b": [6.0, 8.0, 0.0],
            },
        })
        run2 = r.json()
        print(f"   Cosine similarity: {run2['outputs'].get('cosine_similarity', '?')}")

        # ── 7. Rate ────────────────────────────────────────────────────────
        print("\n7. Rating skill (5 stars)...")
        r = await client.put(f"{SKILL_URL}/skills/{skill_id}/rate", json={
            "rating": 5,
            "comment": "Clean I/O contract, works reliably.",
            "rater_agent_id": agent_id,
        })
        rating_data = r.json()
        print(f"   Average rating: {rating_data['avg_rating']:.1f} ({rating_data['rating_count']} ratings)")

        # ── 8. Fork ────────────────────────────────────────────────────────
        print("\n8. Forking skill as 'Cosine Similarity'...")
        r = await client.post(f"{SKILL_URL}/skills/{skill_id}/fork", json={
            "new_name": "Cosine Similarity",
            "visibility": "public",
            "forker_agent_id": agent_id,
        })
        fork = r.json()
        print(f"   ✓ Fork published: {fork['name']} v{fork['version']}")
        print(f"   URN: {fork['urn']}")

        # ── Summary ────────────────────────────────────────────────────────
        print(f"\n{'─' * 50}")
        print(f"✓ Full lifecycle complete.")
        print(f"  Original : {SKILL_URL}/skills/{skill_id}")
        print(f"  Fork     : {SKILL_URL}/skills/{fork['skill_id']}")
        print(f"  Browse   : {SKILL_URL}/docs")


if __name__ == "__main__":
    asyncio.run(main())
