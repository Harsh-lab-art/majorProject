"""
Machine-Learning / Video Analytics module: motion, face and object detection on recovered clips.

Runs on the EXPORTED copy of a clip (.mp4, else .dav / raw stream), never on the evidence image,
and only adds findings: it never changes a clip or its hashes.

  * Motion  - OpenCV MOG2 background subtraction on sampled frames; consecutive frames whose
              foreground ratio >= motion_threshold are grouped into motion events. Reliable.
  * Faces   - OpenCV's bundled Haar cascade (frontal faces). Fast, no download, but produces false
              positives and misses side views: findings are INDICATIVE leads, to be confirmed by a
              person. Labelled as such in the report.
  * Objects - YOLOv8 (COCO, 80 classes) through OpenCV DNN, only if an ONNX model is configured:
              set OMNITRACE_YOLO_ONNX=/path/to/yolov8n.onnx (export with
              `yolo export model=yolov8n.pt format=onnx`). Without it, object detection is reported
              as "not configured" rather than silently skipped.

Frames are SAMPLED (default 2 per second) so analysis stays fast on long footage.
Thumbnails of the strongest frame in the first few motion events are saved next to the clip so
the PDF report can show them.
"""

from __future__ import annotations

import os
from typing import List, Optional

COCO_CLASSES = (
    "person bicycle car motorcycle airplane bus train truck boat traffic_light fire_hydrant stop_sign "
    "parking_meter bench bird cat dog horse sheep cow elephant bear zebra giraffe backpack umbrella handbag "
    "tie suitcase frisbee skis snowboard sports_ball kite baseball_bat baseball_glove skateboard surfboard "
    "tennis_racket bottle wine_glass cup fork knife spoon bowl banana apple sandwich orange broccoli carrot "
    "hot_dog pizza donut cake chair couch potted_plant bed dining_table toilet tv laptop mouse remote "
    "keyboard cell_phone microwave oven toaster sink refrigerator book clock vase scissors teddy_bear "
    "hair_drier toothbrush").split()

_WARMUP_SAMPLES = 2        # background model needs a couple of frames before motion means anything
_MAX_DETECTIONS_LISTED = 50


def _cv2():
    try:
        import cv2  # noqa: WPS433
        return cv2
    except ImportError:
        return None


def analytics_available() -> dict:
    cv2 = _cv2()
    model = os.environ.get("OMNITRACE_YOLO_ONNX")
    return {"opencv": cv2.__version__ if cv2 else None,
            "motion": cv2 is not None, "faces": cv2 is not None,
            "objects": bool(cv2 and model and os.path.exists(model))}


def _load_yolo(cv2, model_path: Optional[str]):
    if not model_path or not os.path.exists(model_path):
        return None, "not configured (set OMNITRACE_YOLO_ONNX to a YOLOv8 .onnx file)"
    try:
        return cv2.dnn.readNetFromONNX(model_path), None
    except Exception as exc:   # bad/unsupported model file
        return None, f"model failed to load: {exc}"


def _yolo_detect(cv2, net, frame, conf_threshold=0.4, nms_threshold=0.45) -> List[tuple]:
    import numpy as np
    h, w = frame.shape[:2]
    blob = cv2.dnn.blobFromImage(frame, 1 / 255.0, (640, 640), swapRB=True, crop=False)
    net.setInput(blob)
    out = net.forward()
    rows = out[0].T if out.shape[1] < out.shape[2] else out[0]      # (8400, 84)
    scores = rows[:, 4:]
    cls = scores.argmax(axis=1)
    conf = scores[np.arange(len(cls)), cls]
    keep = conf >= conf_threshold
    if not keep.any():
        return []
    rows, cls, conf = rows[keep], cls[keep], conf[keep]
    sx, sy = w / 640.0, h / 640.0
    boxes = [[int((r[0] - r[2] / 2) * sx), int((r[1] - r[3] / 2) * sy), int(r[2] * sx), int(r[3] * sy)] for r in rows]
    idx = cv2.dnn.NMSBoxes(boxes, conf.tolist(), conf_threshold, nms_threshold)
    idx = list(np.array(idx).flatten()) if len(idx) else []
    return [(COCO_CLASSES[int(cls[i])] if cls[i] < len(COCO_CLASSES) else str(int(cls[i])), float(conf[i]), boxes[i])
            for i in idx]


def analyze_video(path: str, sample_fps: float = 2.0, motion_threshold: float = 0.01, max_samples: int = 7200,
                  detect_faces: bool = True, object_model: Optional[str] = None, thumb_dir: Optional[str] = None,
                  thumb_prefix: str = "clip", max_thumbs: int = 3) -> dict:
    cv2 = _cv2()
    if cv2 is None:
        return {"error": "opencv-python is not installed (pip install opencv-python-headless)"}
    if not os.path.exists(path):
        return {"error": f"video file not found: {os.path.basename(path)}"}
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return {"error": "video could not be opened by OpenCV (unsupported container or codec)"}

    fps = cap.get(cv2.CAP_PROP_FPS)
    fps_assumed = not fps or fps != fps or fps <= 0 or fps > 240
    fps = 25.0 if fps_assumed else float(fps)
    step = max(1, int(round(fps / sample_fps)))
    sample_interval = step / fps

    bg = cv2.createBackgroundSubtractorMOG2(history=100, varThreshold=25, detectShadows=False)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    face_cascade = None
    if detect_faces:
        face_cascade = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
        if face_cascade.empty():
            face_cascade = None
    net, object_status = _load_yolo(cv2, object_model or os.environ.get("OMNITRACE_YOLO_ONNX"))

    motion_events, cur = [], None
    faces = {"frames_with_faces": 0, "max_faces_in_frame": 0, "detections": []}
    objects = {"counts": {}, "detections": []}
    thumbs: List[str] = []
    idx = sampled = 0
    truncated = False

    def close_event():
        nonlocal cur
        if cur is None:
            return
        frame = cur.pop("_frame")
        if thumb_dir and len(thumbs) < max_thumbs and frame is not None:
            os.makedirs(thumb_dir, exist_ok=True)
            name = f"{thumb_prefix}_motion{len(motion_events)}.jpg"
            th = cv2.resize(frame, (480, int(frame.shape[0] * 480 / frame.shape[1])))
            if cv2.imwrite(os.path.join(thumb_dir, name), th):
                thumbs.append(name)
                cur["thumbnail"] = name
        motion_events.append(cur)
        cur = None

    while True:
        if not cap.grab():
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if ok and frame is not None:
                t = idx / fps
                small = cv2.resize(frame, (320, max(1, int(frame.shape[0] * 320 / frame.shape[1]))))
                fg = cv2.morphologyEx(bg.apply(small), cv2.MORPH_OPEN, kernel)
                ratio = cv2.countNonZero(fg) / float(fg.size)
                if sampled >= _WARMUP_SAMPLES and ratio >= motion_threshold:
                    if cur is not None and t - cur["end_s"] <= 1.5 * sample_interval:
                        cur["end_s"] = round(t, 3)
                        if ratio > cur["peak_ratio"]:
                            cur.update(peak_ratio=round(ratio, 4), peak_s=round(t, 3), _frame=frame.copy())
                    else:
                        close_event()
                        cur = {"start_s": round(t, 3), "end_s": round(t, 3), "peak_ratio": round(ratio, 4),
                               "peak_s": round(t, 3), "_frame": frame.copy()}
                elif cur is not None and t - cur["end_s"] > 1.5 * sample_interval:
                    close_event()

                if face_cascade is not None:
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    found = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(24, 24))
                    if len(found):
                        faces["frames_with_faces"] += 1
                        faces["max_faces_in_frame"] = max(faces["max_faces_in_frame"], len(found))
                        if len(faces["detections"]) < _MAX_DETECTIONS_LISTED:
                            faces["detections"].append({"t": round(t, 3), "count": int(len(found))})
                if net is not None:
                    for name, conf, box in _yolo_detect(cv2, net, frame):
                        objects["counts"][name] = objects["counts"].get(name, 0) + 1
                        if len(objects["detections"]) < _MAX_DETECTIONS_LISTED:
                            objects["detections"].append({"t": round(t, 3), "class": name, "conf": round(conf, 3), "box": box})
                sampled += 1
                if sampled >= max_samples:
                    truncated = True
                    break
        idx += 1
    close_event()
    cap.release()

    if sampled == 0:
        return {"error": "no decodable frames (stream damaged, encrypted, or codec not supported)",
                "frames_read": idx}
    return {
        "fps": round(fps, 3), "fps_assumed": fps_assumed, "frames_read": idx, "frames_sampled": sampled,
        "sample_fps": sample_fps, "duration_s": round(idx / fps, 3), "truncated": truncated,
        "motion": {"method": "OpenCV MOG2 background subtraction", "threshold": motion_threshold},
        "motion_events": motion_events,
        "faces": {**faces, "method": "OpenCV Haar cascade (frontal) - indicative only, confirm manually"}
        if face_cascade is not None else {"status": "disabled"},
        "objects": {**objects, "method": "YOLOv8 (COCO) via OpenCV DNN"} if net is not None else {"status": object_status},
        "thumbnails": thumbs,
    }
