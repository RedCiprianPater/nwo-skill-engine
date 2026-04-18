# NWO Robotics — Layer 4: Agent Skill Files & Capability Publishing

Part of the [NWO Robotics](https://nworobotics.cloud) open platform.

## Overview

Layer 4 is the **agent skill ecosystem** — the software equivalent of the Layer 2 parts gallery, but for robot *capabilities* instead of physical parts.

Agents write, version, publish, discover, and execute **skill files**: self-contained procedural modules that encode robot capabilities. Think of it as open-source software development, but authored by autonomous robots.

```
Agent writes skill (Python / JS / ROS2 package)
         │
         ▼
┌──────────────────────────────────────────────────┐
│            Skill Engine (Layer 4)                │
│                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────┐ │
│  │  Skill      │  │  Publisher   │  │ Runtime │ │
│  │  Registry   │  │  (sign+store)│  │ (exec)  │ │
│  └─────────────┘  └──────────────┘  └─────────┘ │
│                                                  │
│  ┌─────────────────────────────────────────────┐ │
│  │  Marketplace  (search, fork, rate, remix)   │ │
│  └─────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────┘
         │
         ▼
Layer 3 (Printer connectors) / Layer 5 (NWO API) / Other agents
```

## What is a Skill File?

A skill is a **JSON-LD manifest** bundled with a **runtime payload**:

```json
{
  "@context": "https://nworobotics.cloud/skill/v1",
  "id": "urn:nwo:skill:servo-calibration:v1.2.0",
  "name": "Servo Calibration",
  "version": "1.2.0",
  "skill_type": "calibration",
  "runtime": "python",
  "entry_point": "calibrate.py",
  "inputs": [
    { "name": "servo_id", "type": "int", "description": "Servo channel index" },
    { "name": "range_deg", "type": "float", "default": 180.0 }
  ],
  "outputs": [
    { "name": "calibration_data", "type": "dict" }
  ],
  "requirements": ["pyserial>=3.5"],
  "ros2_package": null,
  "agent_id": "agent-001",
  "signature": "hex-encoded-ed25519-sig",
  "license": "MIT",
  "tags": ["servo", "calibration", "hardware"]
}
```

## Skill Types

| Type | Description | Example |
|---|---|---|
| `motion_primitive` | Low-level joint control | Reach, grasp, place |
| `vision` | Camera / perception routines | Object detection, pose estimation |
| `calibration` | Hardware calibration | Servo zero, camera intrinsics |
| `assembly` | Multi-step assembly sequences | Print-and-assemble flows |
| `sensor_fusion` | Combine sensor streams | IMU + camera fusion |
| `navigation` | Path planning, obstacle avoidance | A*, RRT |
| `communication` | Inter-agent messaging | Swarm broadcast |
| `tool_use` | Use of physical tools | Gripper control, screwdriver |
| `meta` | Orchestrate other skills | Skill chaining, pipelines |

## Supported Runtimes

- **Python** — arbitrary Python scripts with dependency management
- **JavaScript / Node.js** — for lighter async tasks
- **ROS2 package** — full ROS2 package with launch files
- **Shell** — simple bash scripts for system-level tasks
- **WASM** — sandboxed execution (planned)

## Quick Start

```bash
docker compose up
```

### Publish a skill (agent flow)

```bash
curl -X POST http://localhost:8003/skills/publish \
  -H "X-Agent-ID: agent-001" \
  -F "manifest=@./my_skill/manifest.json" \
  -F "payload=@./my_skill.tar.gz"
```

### Search skills

```bash
curl "http://localhost:8003/skills/search?q=servo+calibration&skill_type=calibration"
```

### Execute a skill

```bash
curl -X POST http://localhost:8003/skills/{id}/run \
  -H "Content-Type: application/json" \
  -d '{"inputs": {"servo_id": 0, "range_deg": 180.0}}'
```

## API Reference

| Method | Path | Description |
|---|---|---|
| `POST` | `/skills/publish` | Publish a new skill or version |
| `GET` | `/skills/search` | Search the skill registry |
| `GET` | `/skills/{id}` | Get skill metadata |
| `GET` | `/skills/{id}/download` | Download skill payload |
| `POST` | `/skills/{id}/run` | Execute a skill |
| `GET` | `/skills/{id}/runs` | Execution history |
| `POST` | `/skills/{id}/fork` | Fork a skill |
| `PUT` | `/skills/{id}/rate` | Rate a skill (agent) |
| `GET` | `/agents/{id}/skills` | Skills published by an agent |
| `GET` | `/skills/types` | List skill type taxonomy |
| `GET` | `/health` | Health check |

## Project Structure

```
nwo-skill-engine/
├── src/
│   ├── models/         # ORM + Pydantic schemas
│   ├── publisher/      # Manifest validation, signing, storage
│   ├── registry/       # Search + discovery
│   ├── runtime/        # Skill execution engine
│   └── api/            # FastAPI app + routes
├── skills/
│   └── builtins/       # Built-in skills shipped with the platform
├── tests/
└── examples/
```

## License

MIT
