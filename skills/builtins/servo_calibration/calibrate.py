"""
NWO Skill: Servo Calibration
Runtime: Python
Entry point: calibrate.py

Reads NWO_SKILL_INPUTS from environment, runs calibration,
writes JSON outputs to NWO_SKILL_OUTPUT_FILE.

Input contract:
  servo_id        : int   — servo channel index
  controller_type : str   — pca9685 | dynamixel | direct
  port            : str   — serial port path
  range_deg       : float — full range of motion in degrees
  step_deg        : float — sweep step size

Output contract:
  min_pwm         : int
  max_pwm         : int
  center_pwm      : int
  calibration_data: dict  — {angle_str: pwm_value}
  success         : bool
"""

import json
import os
import sys
import time


def load_inputs() -> dict:
    raw = os.environ.get("NWO_SKILL_INPUTS", "{}")
    return json.loads(raw)


def write_outputs(outputs: dict) -> None:
    out_file = os.environ.get("NWO_SKILL_OUTPUT_FILE")
    if out_file:
        with open(out_file, "w") as f:
            json.dump(outputs, f, indent=2)
    else:
        print(json.dumps(outputs))


def angle_to_pwm(angle_deg: float, range_deg: float) -> int:
    """Convert angle to PWM value (500–2500 µs range, standard RC servo)."""
    min_pwm = 500
    max_pwm = 2500
    pct = (angle_deg + range_deg / 2) / range_deg
    return int(min_pwm + pct * (max_pwm - min_pwm))


def calibrate_pca9685(servo_id: int, range_deg: float, step_deg: float, port: str) -> dict:
    """
    Calibrate via PCA9685 I2C PWM controller.
    In a real deployment this imports adafruit_pca9685 and smbus2.
    Here we simulate the sweep for portability.
    """
    import math

    calibration_data: dict[str, int] = {}
    angles = []
    a = -range_deg / 2
    while a <= range_deg / 2 + 0.001:
        angles.append(round(a, 1))
        a += step_deg

    for angle in angles:
        pwm = angle_to_pwm(angle, range_deg)
        calibration_data[str(angle)] = pwm
        # In real hardware: pca.channels[servo_id].duty_cycle = pwm_to_duty(pwm)
        time.sleep(0.05)

    min_pwm = min(calibration_data.values())
    max_pwm = max(calibration_data.values())
    center_pwm = angle_to_pwm(0.0, range_deg)

    return {
        "min_pwm": min_pwm,
        "max_pwm": max_pwm,
        "center_pwm": center_pwm,
        "calibration_data": calibration_data,
        "success": True,
    }


def calibrate_dynamixel(servo_id: int, range_deg: float, step_deg: float, port: str) -> dict:
    """
    Calibrate a Dynamixel servo.
    Uses position control mode sweep via the Dynamixel SDK.
    Simulated for portability.
    """
    calibration_data: dict[str, int] = {}
    # Dynamixel position range: 0–4095 (for XL430 in joint mode)
    dyn_max = 4095
    angles = []
    a = -range_deg / 2
    while a <= range_deg / 2 + 0.001:
        angles.append(round(a, 1))
        a += step_deg

    for angle in angles:
        pct = (angle + range_deg / 2) / range_deg
        position = int(pct * dyn_max)
        calibration_data[str(angle)] = position
        time.sleep(0.05)

    return {
        "min_pwm": 0,
        "max_pwm": dyn_max,
        "center_pwm": int(dyn_max / 2),
        "calibration_data": calibration_data,
        "success": True,
    }


def main():
    inputs = load_inputs()

    servo_id = int(inputs.get("servo_id", 0))
    controller_type = str(inputs.get("controller_type", "pca9685"))
    port = str(inputs.get("port", "/dev/ttyUSB0"))
    range_deg = float(inputs.get("range_deg", 180.0))
    step_deg = float(inputs.get("step_deg", 10.0))

    try:
        if controller_type == "pca9685":
            outputs = calibrate_pca9685(servo_id, range_deg, step_deg, port)
        elif controller_type == "dynamixel":
            outputs = calibrate_dynamixel(servo_id, range_deg, step_deg, port)
        else:
            # Generic: return standard PWM range
            outputs = {
                "min_pwm": 500,
                "max_pwm": 2500,
                "center_pwm": 1500,
                "calibration_data": {
                    str(a): angle_to_pwm(a, range_deg)
                    for a in range(int(-range_deg / 2), int(range_deg / 2) + 1, int(step_deg))
                },
                "success": True,
            }
    except Exception as e:
        outputs = {
            "min_pwm": None,
            "max_pwm": None,
            "center_pwm": None,
            "calibration_data": {},
            "success": False,
            "error": str(e),
        }

    write_outputs(outputs)


if __name__ == "__main__":
    main()
