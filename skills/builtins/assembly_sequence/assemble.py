"""
NWO Skill: Assembly Sequence Runner
Runtime: Python
Entry point: assemble.py

Orchestrates a multi-step assembly plan by invoking sub-skills
via the NWO Skill Engine API.

Plan format:
{
  "name": "Print and assemble servo bracket",
  "steps": [
    {
      "name": "print_part",
      "skill_id": "<skill-uuid>",
      "inputs": {"file_url": "...", "printer_id": "my-voron"},
      "on_fail": "abort"          // abort | continue | retry
    },
    {
      "name": "detect_printed_part",
      "skill_id": "<skill-uuid>",
      "inputs": {"image_path": "camera"},
      "condition": {              // Optional: only run if previous step output matches
        "field": "count",
        "op": "gt",
        "value": 0
      },
      "on_fail": "abort"
    }
  ]
}
"""

import json
import os
import time


def load_inputs() -> dict:
    return json.loads(os.environ.get("NWO_SKILL_INPUTS", "{}"))


def write_outputs(outputs: dict) -> None:
    out_file = os.environ.get("NWO_SKILL_OUTPUT_FILE")
    if out_file:
        with open(out_file, "w") as f:
            json.dump(outputs, f, indent=2)
    else:
        print(json.dumps(outputs))


def evaluate_condition(condition: dict, prev_outputs: dict) -> bool:
    """Evaluate a simple field condition against previous step outputs."""
    if not condition:
        return True
    field = condition.get("field", "")
    op = condition.get("op", "eq")
    value = condition.get("value")
    actual = prev_outputs.get(field)
    if actual is None:
        return False
    ops = {
        "eq": lambda a, b: a == b,
        "ne": lambda a, b: a != b,
        "gt": lambda a, b: a > b,
        "lt": lambda a, b: a < b,
        "gte": lambda a, b: a >= b,
        "lte": lambda a, b: a <= b,
        "contains": lambda a, b: b in a if hasattr(a, "__contains__") else False,
    }
    fn = ops.get(op, lambda a, b: True)
    try:
        return fn(actual, value)
    except Exception:
        return False


def invoke_skill(api_url: str, skill_id: str, inputs: dict, caller_agent_id: str | None) -> dict:
    """Call the NWO Skill Engine to execute a sub-skill."""
    import httpx
    payload = {"inputs": inputs}
    if caller_agent_id:
        payload["caller_agent_id"] = caller_agent_id
    r = httpx.post(f"{api_url}/skills/{skill_id}/run", json=payload, timeout=300.0)
    r.raise_for_status()
    return r.json()


def main():
    inputs = load_inputs()
    plan = inputs.get("plan", {})
    api_url = str(inputs.get("skill_api_url", "http://localhost:8003")).rstrip("/")
    caller_agent_id = inputs.get("caller_agent_id")
    dry_run = bool(inputs.get("dry_run", False))

    steps = plan.get("steps", [])
    execution_log = []
    steps_completed = 0
    failed_step = None
    prev_outputs: dict = {}

    for i, step in enumerate(steps):
        step_name = step.get("name", f"step_{i+1}")
        skill_id = step.get("skill_id", "")
        step_inputs = step.get("inputs", {})
        on_fail = step.get("on_fail", "abort")
        condition = step.get("condition")

        # Evaluate condition
        if condition and not evaluate_condition(condition, prev_outputs):
            execution_log.append({
                "step": step_name,
                "skill_id": skill_id,
                "status": "skipped",
                "reason": "condition not met",
                "outputs": {},
                "error": None,
                "duration_ms": 0,
            })
            continue

        if dry_run:
            execution_log.append({
                "step": step_name,
                "skill_id": skill_id,
                "status": "dry_run",
                "inputs": step_inputs,
                "outputs": {},
                "error": None,
                "duration_ms": 0,
            })
            steps_completed += 1
            continue

        t0 = time.monotonic()
        status = "success"
        outputs_: dict = {}
        error = None

        try:
            result = invoke_skill(api_url, skill_id, step_inputs, caller_agent_id)
            if result.get("status") not in ("success",):
                raise RuntimeError(result.get("error") or f"Step returned status: {result.get('status')}")
            outputs_ = result.get("outputs", {})
            prev_outputs = outputs_
            steps_completed += 1
        except Exception as e:
            error = str(e)
            status = "failed"
            if on_fail == "abort":
                failed_step = step_name
                execution_log.append({
                    "step": step_name,
                    "skill_id": skill_id,
                    "status": status,
                    "outputs": outputs_,
                    "error": error,
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                })
                break
            # on_fail == "continue": log and move on

        execution_log.append({
            "step": step_name,
            "skill_id": skill_id,
            "status": status,
            "outputs": outputs_,
            "error": error,
            "duration_ms": int((time.monotonic() - t0) * 1000),
        })

    overall_status = "success"
    if failed_step:
        overall_status = "failed"
    elif steps_completed < len(steps):
        overall_status = "partial"

    write_outputs({
        "status": overall_status,
        "steps_total": len(steps),
        "steps_completed": steps_completed,
        "execution_log": execution_log,
        "failed_step": failed_step,
    })


if __name__ == "__main__":
    main()
