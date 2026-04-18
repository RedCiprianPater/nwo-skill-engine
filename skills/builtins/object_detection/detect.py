"""
NWO Skill: Object Detection
Runtime: Python
Entry point: detect.py

Attempts YOLOv8 via ultralytics; falls back to a PIL-based mock
if the model is not installed (for testing / CI environments).

Input:  NWO_SKILL_INPUTS env var (JSON)
Output: NWO_SKILL_OUTPUT_FILE path (JSON)
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


def _mock_detections(image_path: str, confidence_threshold: float, target_classes: list) -> list:
    """
    Mock detection for environments without YOLO installed.
    Returns plausible fake detections for testing skill infrastructure.
    """
    mock = [
        {"class": "robot_arm", "confidence": 0.91, "bbox": [120, 80, 340, 290]},
        {"class": "printed_part", "confidence": 0.78, "bbox": [60, 200, 180, 320]},
        {"class": "servo_bracket", "confidence": 0.65, "bbox": [310, 150, 420, 260]},
    ]
    result = [d for d in mock if d["confidence"] >= confidence_threshold]
    if target_classes:
        result = [d for d in result if d["class"] in target_classes]
    return result


def run_yolo(image_path: str, model_name: str, confidence_threshold: float, target_classes: list) -> dict:
    """Try real YOLOv8 inference; fall back to mock if unavailable."""
    t0 = time.monotonic()

    try:
        from ultralytics import YOLO
        from PIL import Image

        model = YOLO(f"{model_name}.pt")

        if image_path == "camera":
            import cv2
            cap = cv2.VideoCapture(0)
            ret, frame = cap.read()
            cap.release()
            if not ret:
                raise RuntimeError("Could not capture camera frame")
            results = model(frame, conf=confidence_threshold, verbose=False)
        else:
            results = model(image_path, conf=confidence_threshold, verbose=False)

        detections = []
        for r in results:
            for box in r.boxes:
                cls_name = model.names[int(box.cls)]
                if target_classes and cls_name not in target_classes:
                    continue
                detections.append({
                    "class": cls_name,
                    "confidence": float(box.conf),
                    "bbox": [int(x) for x in box.xyxy[0].tolist()],
                })

        # Get image size
        if image_path != "camera":
            from PIL import Image as PILImage
            with PILImage.open(image_path) as img:
                w, h = img.size
        else:
            w, h = 640, 480

        inference_ms = (time.monotonic() - t0) * 1000
        return {
            "detections": detections,
            "count": len(detections),
            "inference_ms": round(inference_ms, 2),
            "image_size": [w, h],
        }

    except ImportError:
        # Fallback: mock detections (no ultralytics installed)
        detections = _mock_detections(image_path, confidence_threshold, target_classes)
        inference_ms = (time.monotonic() - t0) * 1000

        # Get image size if possible
        image_size = [640, 480]
        if image_path != "camera" and os.path.exists(image_path):
            try:
                from PIL import Image
                with Image.open(image_path) as img:
                    image_size = list(img.size)
            except Exception:
                pass

        return {
            "detections": detections,
            "count": len(detections),
            "inference_ms": round(inference_ms, 2),
            "image_size": image_size,
            "_note": "Mock detections — ultralytics not installed",
        }


def main():
    inputs = load_inputs()
    image_path = str(inputs.get("image_path", "camera"))
    model = str(inputs.get("model", "yolov8n"))
    confidence = float(inputs.get("confidence_threshold", 0.5))
    target_classes = list(inputs.get("target_classes", []))

    try:
        outputs = run_yolo(image_path, model, confidence, target_classes)
    except Exception as e:
        outputs = {
            "detections": [],
            "count": 0,
            "inference_ms": 0.0,
            "image_size": [0, 0],
            "error": str(e),
        }

    write_outputs(outputs)


if __name__ == "__main__":
    main()
