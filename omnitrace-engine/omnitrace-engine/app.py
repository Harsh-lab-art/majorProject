"""
HTTP Layer (Flask) — Section 7.6 of the design doc.

A thin wrapper so the Node/Express API can call the engine over HTTP. No forensic logic here
(that is pipeline.py and everything it calls).

    python app.py        # listens on http://localhost:5000

Endpoints (contract the Node API integrates against; existing fields unchanged):

    POST /process   multipart/form-data
        image                 raw disk image file (required)
        dvr_tz                DVR clock offset from UTC, e.g. "+05:30"
        clock_offset_seconds  DVR displayed time minus true time, in seconds (drift correction)
        examiner              name / ID recorded in the chain of custody
        case_ref, agency, notes   free-text case metadata printed on the report
        analytics             "1" to run motion/face/object analytics on recovered clips
        force_oem             "Dahua" | "Hikvision" | "Generic" to override auto-detection
      200 -> {case_id, oem, oem_confidence, clip_count, report_json_url, report_pdf_url,
              custody_url, device, integrity_ok}
      400 bad parameters, 422 no plugin recognised the image

    GET  /report/<case_id>.json | .pdf | /<case_id>/<file>   report and exported clip files
    GET  /custody/<case_id>       custody entries + hash-chain verification
    POST /custody/<case_id>       JSON {actor, action, details}: append an event (e.g. a download)
    GET  /capabilities            supported parsers, vendors identified, analytics availability
    GET  /health
"""

from __future__ import annotations

import logging
import os
import re
import tempfile

from flask import Flask, jsonify, request, send_from_directory

from analytics import analytics_available
from custody import CustodyLog
from detector import build_default_registry
from identify import _VENDOR_RULES
from models import UnsupportedOEMError
from pipeline import ENGINE_VERSION, process_image

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("omnitrace.app")

DATA_DIR = os.environ.get("OMNITRACE_DATA_DIR", os.path.join(tempfile.gettempdir(), "omnitrace_reports"))
os.makedirs(DATA_DIR, exist_ok=True)
_CASE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(float(os.environ.get("OMNITRACE_MAX_UPLOAD_GB", "2")) * 1024**3)


def _custody(case_id: str):
    if not _CASE_ID.match(case_id):
        return None
    path = os.path.join(DATA_DIR, f"{case_id}.custody.jsonl")
    return CustodyLog(path) if os.path.exists(path) else None


@app.get("/health")
def health():
    return jsonify({"status": "ok", "engine_version": ENGINE_VERSION})


@app.get("/capabilities")
def capabilities():
    return jsonify({
        "engine_version": ENGINE_VERSION,
        "parsers": [{"name": p.name, "confidence": p.confidence, "carving": p.carve_uncovered}
                    for p in build_default_registry()],
        "vendors_identified": sorted({v for v, *_ in _VENDOR_RULES}),
        "analytics": analytics_available(),
    })


@app.post("/process")
def process():
    if "image" not in request.files:
        return jsonify({"error": "multipart field 'image' is required"}), 400
    form = request.form
    try:
        clock_offset = float(form.get("clock_offset_seconds") or 0)
    except ValueError:
        return jsonify({"error": "clock_offset_seconds must be a number"}), 400
    meta = {k: form[k] for k in ("case_ref", "agency", "notes") if form.get(k)}

    uploaded = request.files["image"]
    with tempfile.NamedTemporaryFile(delete=False, suffix=".img") as tmp:
        uploaded.save(tmp.name)
        image_path = tmp.name
    try:
        result = process_image(
            image_path, output_dir=DATA_DIR, dvr_tz=form.get("dvr_tz") or None,
            examiner=form.get("examiner") or None, clock_offset_seconds=clock_offset,
            run_analytics=form.get("analytics") in ("1", "true", "yes"),
            force_oem=form.get("force_oem") or None, case_meta=meta, original_filename=uploaded.filename)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except UnsupportedOEMError as exc:
        logger.warning("Unsupported image: %s", exc)
        return jsonify({"error": str(exc)}), 422
    finally:
        os.unlink(image_path)

    return jsonify({
        "case_id": result.case_id,
        "oem": result.oem,
        "oem_confidence": result.oem_confidence,
        "clip_count": len(result.hashed_clips),
        "report_json_url": f"/report/{result.case_id}.json",
        "report_pdf_url": f"/report/{result.case_id}.pdf",
        "custody_url": f"/custody/{result.case_id}",
        "device": {k: result.device.get(k) for k in ("vendor", "model", "firmware", "confidence")},
        "integrity_ok": result.integrity.get("verified_after_processing"),
    })


@app.get("/report/<path:filename>")
def get_report(filename: str):
    return send_from_directory(DATA_DIR, filename)


@app.get("/custody/<case_id>")
def get_custody(case_id: str):
    log = _custody(case_id)
    if log is None:
        return jsonify({"error": "no custody log for this case"}), 404
    return jsonify({"case_id": case_id, "entries": log.entries(), "verification": log.verify()})


@app.post("/custody/<case_id>")
def add_custody(case_id: str):
    log = _custody(case_id)
    if log is None:
        return jsonify({"error": "no custody log for this case"}), 404
    body = request.get_json(silent=True) or {}
    if not body.get("action") or not body.get("actor"):
        return jsonify({"error": "JSON body needs 'actor' and 'action'"}), 400
    details = body.get("details") if isinstance(body.get("details"), dict) else {}
    return jsonify(log.append(str(body["action"])[:64], str(body["actor"])[:128], details)), 201


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=os.environ.get("FLASK_DEBUG") == "1")
