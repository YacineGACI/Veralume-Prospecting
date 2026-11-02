"""How much a photo's verticals lean, measured with Veralume's own code.

Runs inside Veralume's environment (it imports `veralume`), started by `screen.py`.
Geometry only: line detection and, with --geocalib, the local GeoCalib model. No
provider is called and nothing is generated, so it costs nothing.

stdin: one JSON object per line, {"id": 12, "path": "/abs/photo.jpg"}
stdout: one JSON object per line, {"id", "lean_deg", "n_lines", "width", "height",
        "roll_deg", "pitch_deg", "error"}
"""

from __future__ import annotations

import json
import sys

import cv2
try:  # Veralume's current layout, then its older one
    from veralume.core.images.io import fit_max_side, load_image
    from veralume.core.images.lines import detect_segments, vertical_error
except ImportError:
    from veralume.imaging.io import fit_max_side, load_image
    from veralume.verification.lines import detect_segments, vertical_error

_MAX_SIDE = 1600
# Fewer long near-vertical lines than this: not a room or a building (portrait, stock art).
_MIN_LINES = 12


def _face_detector():
    """YuNet (OpenCV model zoo, MIT), path given as --faces=<onnx>. None if unavailable."""
    path = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--faces=")), None)
    if not path:
        return None
    try:
        return cv2.FaceDetectorYN.create(path, "", (320, 320), 0.75)
    except cv2.error:
        return None


def _has_face(detector, rgb) -> bool:
    if detector is None:
        return False
    small, _ = fit_max_side(rgb, 640)
    bgr = cv2.cvtColor(small, cv2.COLOR_RGB2BGR)
    detector.setInputSize((bgr.shape[1], bgr.shape[0]))
    _, faces = detector.detect(bgr)
    if faces is None:
        return False
    side = min(bgr.shape[:2])
    return any(f[2] >= side / 14 for f in faces)  # ignore tiny faces in a picture frame


def main() -> None:
    faces = _face_detector()
    estimator = None
    if "--geocalib" in sys.argv:
        from veralume.config.settings import load_settings
        from veralume.domain.geometry import CameraModel
        from veralume.geometry.geocalib_adapter import GeoCalibEstimator

        try:
            estimator = GeoCalibEstimator(load_settings())
            if not estimator.capability().available:
                estimator = None
        except Exception:  # optional: lean is still measured without it
            estimator = None
    for line in sys.stdin:
        job = json.loads(line)
        out: dict = {"id": job["id"], "lean_deg": None, "n_lines": 0, "roll_deg": None,
                     "pitch_deg": None, "error": None}  # fmt: skip
        try:
            img = load_image(open(job["path"], "rb").read(), max_side=_MAX_SIDE)
            out["width"], out["height"] = img.width, img.height
            if _has_face(faces, img.rgb):
                out["error"] = "people in the photo: not a listing photo"
                print(json.dumps(out), flush=True)
                continue
            lean, n = vertical_error(detect_segments(img.rgb))
            out["n_lines"] = n
            if lean is None or n < _MIN_LINES:
                out["error"] = f"too few straight lines ({n}) to measure"
            else:
                out["lean_deg"] = lean
            if estimator is not None:
                calib = estimator.calibrate(img.rgb, img.metadata, CameraModel.PINHOLE)
                out["roll_deg"], out["pitch_deg"] = calib.roll_deg, calib.pitch_deg
        except Exception as e:  # report and keep going
            out["error"] = f"{type(e).__name__}: {e}"[:300]
        print(json.dumps(out), flush=True)


if __name__ == "__main__":
    main()
