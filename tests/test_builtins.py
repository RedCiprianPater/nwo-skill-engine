"""
Unit tests for built-in skill scripts.
Runs each skill's main() function directly with mocked I/O.
No network or hardware required.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

BUILTINS = Path(__file__).parent.parent / "skills" / "builtins"


def _run_skill(skill_module_path: Path, inputs: dict) -> dict:
    """Run a skill script with controlled inputs and capture outputs."""
    with tempfile.TemporaryDirectory() as tmp:
        out_file = Path(tmp) / "outputs.json"
        env = {
            **os.environ,
            "NWO_SKILL_INPUTS": json.dumps(inputs),
            "NWO_SKILL_OUTPUT_FILE": str(out_file),
        }
        # Import and run via exec to avoid polluting sys.modules
        code = skill_module_path.read_text()
        namespace: dict = {"__name__": "__main__"}
        with patch.dict(os.environ, env):
            exec(compile(code, str(skill_module_path), "exec"), namespace)
            if "main" in namespace:
                with patch.dict(os.environ, env):
                    namespace["main"]()
        if out_file.exists():
            return json.loads(out_file.read_text())
        return {}


# ── Servo calibration ──────────────────────────────────────────────────────────

def test_servo_calibration_pca9685():
    script = BUILTINS / "servo_calibration" / "calibrate.py"
    if not script.exists():
        pytest.skip("Built-in skill not found")

    result = _run_skill(script, {
        "servo_id": 0,
        "controller_type": "pca9685",
        "range_deg": 180.0,
        "step_deg": 30.0,
    })
    assert result.get("success") is True
    assert "min_pwm" in result
    assert "max_pwm" in result
    assert "center_pwm" in result
    assert "calibration_data" in result
    assert isinstance(result["calibration_data"], dict)
    assert len(result["calibration_data"]) > 0


def test_servo_calibration_dynamixel():
    script = BUILTINS / "servo_calibration" / "calibrate.py"
    if not script.exists():
        pytest.skip("Built-in skill not found")

    result = _run_skill(script, {
        "servo_id": 1,
        "controller_type": "dynamixel",
        "range_deg": 300.0,
        "step_deg": 60.0,
    })
    assert result.get("success") is True
    assert result["max_pwm"] == 4095


def test_servo_calibration_pwm_range():
    script = BUILTINS / "servo_calibration" / "calibrate.py"
    if not script.exists():
        pytest.skip("Built-in skill not found")

    result = _run_skill(script, {
        "servo_id": 0,
        "controller_type": "pca9685",
        "range_deg": 180.0,
        "step_deg": 45.0,
    })
    assert result["min_pwm"] < result["center_pwm"] < result["max_pwm"]


# ── Object detection ──────────────────────────────────────────────────────────

def test_object_detection_mock(tmp_path):
    script = BUILTINS / "object_detection" / "detect.py"
    if not script.exists():
        pytest.skip("Built-in skill not found")

    # Create a dummy image file so path validation passes
    from PIL import Image as PILImage
    img_path = tmp_path / "test.jpg"
    PILImage.new("RGB", (640, 480), color=(100, 100, 100)).save(img_path)

    result = _run_skill(script, {
        "image_path": str(img_path),
        "model": "yolov8n",
        "confidence_threshold": 0.5,
        "target_classes": [],
    })
    assert "detections" in result
    assert "count" in result
    assert isinstance(result["detections"], list)
    assert "inference_ms" in result


def test_object_detection_with_class_filter(tmp_path):
    script = BUILTINS / "object_detection" / "detect.py"
    if not script.exists():
        pytest.skip("Built-in skill not found")

    from PIL import Image as PILImage
    img_path = tmp_path / "test.jpg"
    PILImage.new("RGB", (320, 240)).save(img_path)

    result = _run_skill(script, {
        "image_path": str(img_path),
        "model": "yolov8n",
        "confidence_threshold": 0.5,
        "target_classes": ["robot_arm"],
    })
    # All returned detections should match the filter
    for det in result.get("detections", []):
        assert det["class"] == "robot_arm"


# ── Assembly sequence ─────────────────────────────────────────────────────────

def test_assembly_dry_run():
    script = BUILTINS / "assembly_sequence" / "assemble.py"
    if not script.exists():
        pytest.skip("Built-in skill not found")

    plan = {
        "name": "Test Plan",
        "steps": [
            {"name": "step_1", "skill_id": "skill-abc", "inputs": {"x": 1}},
            {"name": "step_2", "skill_id": "skill-xyz", "inputs": {"y": 2}},
        ],
    }
    result = _run_skill(script, {"plan": plan, "dry_run": True})
    assert result["status"] == "success"
    assert result["steps_total"] == 2
    assert result["steps_completed"] == 2
    assert all(s["status"] == "dry_run" for s in result["execution_log"])


def test_assembly_empty_plan():
    script = BUILTINS / "assembly_sequence" / "assemble.py"
    if not script.exists():
        pytest.skip("Built-in skill not found")

    result = _run_skill(script, {"plan": {"name": "Empty", "steps": []}, "dry_run": True})
    assert result["steps_total"] == 0
    assert result["status"] == "success"


def test_assembly_condition_skip():
    script = BUILTINS / "assembly_sequence" / "assemble.py"
    if not script.exists():
        pytest.skip("Built-in skill not found")

    plan = {
        "name": "Conditional Plan",
        "steps": [
            {
                "name": "conditional_step",
                "skill_id": "skill-abc",
                "inputs": {},
                "condition": {"field": "count", "op": "gt", "value": 100},
                # prev_outputs is empty → count = None → condition fails → step skipped
            },
        ],
    }
    result = _run_skill(script, {"plan": plan, "dry_run": False})
    assert result["execution_log"][0]["status"] == "skipped"
