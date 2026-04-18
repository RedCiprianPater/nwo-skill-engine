# Contributing to NWO Skill Engine

## Development setup

```bash
git clone https://github.com/nworobotics/nwo-skill-engine
cd nwo-skill-engine
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

## Running tests

```bash
pytest tests/ -v
```

## Writing a built-in skill

1. Create a directory under `skills/builtins/<skill_name>/`
2. Add `manifest.json` — see `skills/builtins/servo_calibration/manifest.json` as a template
3. Add your entry point script (e.g. `calibrate.py`)
4. Validate: `nwo-skill validate skills/builtins/<skill_name>/manifest.json`
5. Pack: `nwo-skill pack skills/builtins/<skill_name>/`
6. Test by publishing to a local instance: `nwo-skill publish skills/builtins/<skill_name>/ --agent-id <id>`

## Skill I/O contract

All skills communicate via environment variables:

```python
import json, os

# Read inputs
inputs = json.loads(os.environ.get("NWO_SKILL_INPUTS", "{}"))
my_param = inputs.get("my_param", default_value)

# Write outputs
outputs = {"result": computed_value}
out_path = os.environ.get("NWO_SKILL_OUTPUT_FILE", "outputs.json")
with open(out_path, "w") as f:
    json.dump(outputs, f)
```

## Adding a new skill type

1. Add the new type to `SkillType` enum in `src/models/manifest.py`
2. Update `MANIFEST_SCHEMA` in the same file
3. Add a built-in example under `skills/builtins/`
4. Update the README table

## Code style

```bash
ruff check src/ tests/
ruff format src/ tests/
```
