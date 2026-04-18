"""NWO Skill Engine CLI."""
from __future__ import annotations
import asyncio, json, os, tarfile, tempfile
from pathlib import Path
import click
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

console = Console()


@click.group()
def cli():
    """NWO Robotics Skill Engine — Layer 4."""


@cli.command()
@click.option("--host", default=None)
@click.option("--port", default=None, type=int)
@click.option("--reload", is_flag=True)
def serve(host, port, reload):
    """Start the skill engine API."""
    import uvicorn
    _host = host or os.getenv("API_HOST", "0.0.0.0")
    _port = port or int(os.getenv("API_PORT", "8003"))
    console.print(f"\n[bold]NWO Skill Engine[/bold] → http://{_host}:{_port}")
    console.print(f"  Docs: http://{_host}:{_port}/docs\n")
    uvicorn.run("src.api.main:app", host=_host, port=_port, reload=reload)


@cli.command()
@click.argument("skill_dir")
@click.option("--output", "-o", default=None, help="Output .tar.gz path")
def pack(skill_dir, output):
    """Pack a skill directory into a .tar.gz payload archive."""
    d = Path(skill_dir)
    if not d.exists():
        console.print(f"[red]Directory not found: {skill_dir}[/red]"); return

    manifest_path = d / "manifest.json"
    if not manifest_path.exists():
        console.print("[red]manifest.json not found in skill directory[/red]"); return

    out = Path(output) if output else d.parent / f"{d.name}.tar.gz"
    with tarfile.open(out, "w:gz") as tar:
        for f in d.rglob("*"):
            if f.is_file() and "__pycache__" not in str(f) and not f.suffix == ".pyc":
                tar.add(f, arcname=f.relative_to(d))

    size_kb = out.stat().st_size / 1024
    console.print(f"[green]✓[/green] Packed → {out} ({size_kb:.1f} KB)")


@cli.command()
@click.argument("manifest_file")
def validate(manifest_file):
    """Validate a skill manifest.json file."""
    from src.publisher.publish import validate_manifest_json
    try:
        data = json.loads(Path(manifest_file).read_text())
        manifest = validate_manifest_json(data)
        console.print(f"[green]✓[/green] Manifest valid")
        console.print(f"  Name    : {manifest.name}")
        console.print(f"  Version : {manifest.version}")
        console.print(f"  Type    : {manifest.skill_type.value}")
        console.print(f"  Runtime : {manifest.runtime.value}")
        console.print(f"  URN     : {manifest.compute_urn()}")
    except Exception as e:
        console.print(f"[red]✗ Invalid:[/red] {e}")


@cli.command()
@click.argument("skill_dir")
@click.option("--api", default="http://localhost:8003", help="Skill engine API URL")
@click.option("--agent-id", required=True, help="Your registered agent ID")
def publish(skill_dir, api, agent_id):
    """Pack and publish a skill directory to the skill engine."""
    asyncio.run(_publish(skill_dir, api, agent_id))


async def _publish(skill_dir, api_url, agent_id):
    import httpx
    d = Path(skill_dir)
    manifest_path = d / "manifest.json"
    if not manifest_path.exists():
        console.print("[red]manifest.json not found[/red]"); return

    # Pack
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    with tarfile.open(tmp_path, "w:gz") as tar:
        for f in d.rglob("*"):
            if f.is_file() and "__pycache__" not in str(f) and not f.suffix == ".pyc":
                tar.add(f, arcname=f.relative_to(d))

    console.print(f"Packed {tmp_path.stat().st_size / 1024:.1f} KB payload")

    async with httpx.AsyncClient(timeout=60.0) as client:
        with open(tmp_path, "rb") as pf:
            r = await client.post(
                f"{api_url}/skills/publish",
                headers={"X-Agent-ID": agent_id},
                files={"payload": ("skill.tar.gz", pf, "application/gzip")},
                data={"manifest": manifest_path.read_text()},
            )
        tmp_path.unlink(missing_ok=True)

        if r.status_code != 200:
            console.print(f"[red]✗ Publish failed ({r.status_code}):[/red] {r.text}"); return

        data = r.json()
        console.print(f"[green]✓[/green] Published!")
        console.print(f"  Name    : {data['name']}")
        console.print(f"  Version : {data['version']}")
        console.print(f"  URN     : {data['urn']}")
        console.print(f"  URL     : {data['payload_url']}")


@cli.command()
@click.option("--q", default=None, help="Search query")
@click.option("--type", "skill_type", default=None)
@click.option("--runtime", default=None)
@click.option("--api", default="http://localhost:8003")
def search(q, skill_type, runtime, api):
    """Search published skills."""
    asyncio.run(_search(q, skill_type, runtime, api))


async def _search(q, skill_type, runtime, api_url):
    import httpx
    params = {}
    if q: params["q"] = q
    if skill_type: params["skill_type"] = skill_type
    if runtime: params["runtime"] = runtime

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(f"{api_url}/skills/search", params=params)
        data = r.json()

    t = Table(title=f"Skills ({data['total']} total)")
    t.add_column("Name"); t.add_column("Version"); t.add_column("Type")
    t.add_column("Runtime"); t.add_column("Runs"); t.add_column("★")

    for s in data["results"]:
        rating = f"{s['avg_rating']:.1f}" if s.get("avg_rating") else "—"
        t.add_row(s["name"], s["version"], s["skill_type"],
                  s["runtime"], str(s["run_count"]), rating)
    console.print(t)


@cli.command()
@click.argument("skill_id")
@click.argument("inputs_json")
@click.option("--api", default="http://localhost:8003")
def run(skill_id, inputs_json, api):
    """Execute a skill with JSON inputs."""
    asyncio.run(_run(skill_id, inputs_json, api))


async def _run(skill_id, inputs_json, api_url):
    import httpx
    try:
        inputs = json.loads(inputs_json)
    except Exception:
        console.print("[red]inputs_json must be valid JSON[/red]"); return

    async with httpx.AsyncClient(timeout=180.0) as client:
        with console.status("Executing skill..."):
            r = await client.post(f"{api_url}/skills/{skill_id}/run", json={"inputs": inputs})
        data = r.json()

    if data.get("status") == "success":
        console.print(f"[green]✓[/green] Success in {data.get('duration_ms', '?')}ms")
        console.print(Syntax(json.dumps(data["outputs"], indent=2), "json", theme="monokai"))
    else:
        console.print(f"[red]✗ {data.get('status')}:[/red] {data.get('error')}")


if __name__ == "__main__":
    cli()
