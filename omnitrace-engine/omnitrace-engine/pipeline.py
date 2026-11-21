"""
The whole engine, end to end, in one function. This is what app.py's HTTP layer and cli.py
call — the Node API never needs to know any of the internals below exist (design doc, 7.6).

    evidence in
      -> 1. integrity hash of the whole image (MD5 + SHA-256) [+ check against acquisition record]
      -> 2. device identification   (vendor / model / firmware / format family)
      -> 3. OEM routing             (parser plugin; Generic fallback; or examiner-forced)
      -> 4. structured parsing      (vendor-specific)
      -> 5. recovery / carving      (vendor-agnostic)
      -> 6. per-clip hashing
      -> 7. export playable copies  (.dav / raw stream, + .mp4 if ffmpeg exists)
      -> 8. AI analytics            (optional: motion / faces / objects)
      -> 9. timeline                (UTC + clock-drift normalisation, gaps, cross-camera correlation)
      -> 10. integrity re-check      (image unchanged by processing)
      -> 11. JSON + PDF report
    Every step is written to the case's hash-chained chain-of-custody log.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, replace
from datetime import timedelta, timezone
from typing import List, Optional

from analytics import analyze_video
from custody import CustodyLog
from detector import DetectionResult, build_default_registry, detect_oem
from export import export_clip_dav, remux_to_mp4
from hasher import HashedClip, hash_clips, hash_whole_image
from identify import identify_device
from models import ClipEntry, ClipStatus
from recovery import recover_additional_clips
from report import build_json_report, write_json_report, write_pdf_report
from timeline import build_timeline

logger = logging.getLogger("omnitrace.pipeline")

ENGINE_VERSION = "0.3.0"
ENGINE_ACTOR = f"omnitrace-engine/{ENGINE_VERSION}"
EXPORT_MAX_BYTES = int(float(os.environ.get("OMNITRACE_EXPORT_MAX_GB", "2")) * 1024**3)


@dataclass
class CaseResult:
    case_id: str
    oem: str
    oem_confidence: str
    hashed_clips: List[HashedClip]
    json_report_path: str
    pdf_report_path: str
    custody_path: Optional[str] = None
    device: Optional[dict] = None
    timeline: Optional[dict] = None
    integrity: Optional[dict] = None


def _parse_tz(dvr_tz: str) -> timezone:
    m = re.fullmatch(r"([+-])(\d{2}):(\d{2})", dvr_tz)
    if not m or int(m.group(2)) > 14 or int(m.group(3)) > 59:
        raise ValueError("dvr_tz must look like +05:30")
    delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
    return timezone(delta if m.group(1) == "+" else -delta)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _acquisition_check(image_path: str, image_hash: tuple) -> Optional[dict]:
    """If the image was made with acquire.py, confirm it still matches its acquisition hashes."""
    sidecar = image_path + ".acquisition.json"
    if not os.path.exists(sidecar):
        return None
    with open(sidecar, encoding="utf-8") as f:
        rec = json.load(f)
    return {"record": rec, "hash_matches_acquisition":
            rec.get("image_md5") == image_hash[0] and rec.get("image_sha256") == image_hash[1]}


def _route(image_path: str, force_oem: Optional[str]):
    registry = build_default_registry()
    if force_oem:
        plugin = next((p for p in registry if p.name.lower() == force_oem.lower()), None)
        if plugin is None:
            raise ValueError(f"force_oem must be one of {[p.name for p in registry]}")
        return plugin, DetectionResult(oem=plugin.name, confidence="forced by examiner")
    detection = detect_oem(image_path, registry)
    return next(p for p in registry if p.name == detection.oem), detection


def _export(image_path: str, output_dir: str, case_id: str, hashed: List[HashedClip]):
    """Write each clip's own bytes to a file (its SHA-256 equals the clip hash) + best-effort MP4."""
    exportable = [hc for hc in hashed if hc.clip.validated or hc.clip.status == ClipStatus.CARVED]
    total = sum(hc.clip.length for hc in exportable)
    if not exportable:
        return {}, None
    if total > EXPORT_MAX_BYTES:
        return {}, f"clip export skipped: {total} bytes exceeds the {EXPORT_MAX_BYTES} byte guard (OMNITRACE_EXPORT_MAX_GB)"
    clip_dir = os.path.join(output_dir, case_id)
    os.makedirs(clip_dir, exist_ok=True)
    exports = {}
    for hc in exportable:
        is_dhav = bool(hc.clip.validated)                  # validated clips are DHAV frame segments
        ext, key = ("dav", "dav") if is_dhav else ("es", "raw")   # carved = raw H.264/H.265 elementary stream
        src = os.path.join(clip_dir, f"{hc.clip.clip_id}.{ext}")
        sha = export_clip_dav(image_path, hc.clip, src)
        entry = {key: f"{case_id}/{hc.clip.clip_id}.{ext}", f"{key}_sha256_matches_clip": sha == hc.sha256}
        mp4 = os.path.join(clip_dir, f"{hc.clip.clip_id}.mp4")
        err = remux_to_mp4(src, mp4)
        entry["mp4"] = f"{case_id}/{hc.clip.clip_id}.mp4" if err is None else None
        entry["mp4_error"] = err
        exports[hc.clip.clip_id] = entry
    return exports, None


def _run_analytics(output_dir: str, case_id: str, exports: dict) -> dict:
    results = {}
    clip_dir = os.path.join(output_dir, case_id)
    for clip_id, entry in exports.items():
        rel = entry.get("mp4") or entry.get("dav") or entry.get("raw")
        res = analyze_video(os.path.join(output_dir, rel), thumb_dir=clip_dir, thumb_prefix=clip_id)
        res["source_file"] = rel
        res["thumbnails"] = [f"{case_id}/{t}" for t in res.get("thumbnails", [])]
        results[clip_id] = res
    return results


def process_image(image_path: str, output_dir: str, case_id: Optional[str] = None, dvr_tz: Optional[str] = None,
                  export_clips: bool = True, examiner: Optional[str] = None, clock_offset_seconds: float = 0.0,
                  run_analytics: bool = False, force_oem: Optional[str] = None, case_meta: Optional[dict] = None,
                  original_filename: Optional[str] = None, verify_after: bool = True) -> CaseResult:
    """
    Run one raw disk image through the full pipeline. Raises UnsupportedOEMError if nothing
    recognises the image (and force_oem was not given), ValueError for bad parameters.
    """
    case_id = case_id or str(uuid.uuid4())
    os.makedirs(output_dir, exist_ok=True)
    tz = _parse_tz(dvr_tz) if dvr_tz else None
    custody_path = os.path.join(output_dir, f"{case_id}.custody.jsonl")
    custody = CustodyLog(custody_path)
    who = examiner or "unspecified examiner"
    image_size = os.path.getsize(image_path)

    custody.append("evidence_received", who, {
        "filename": original_filename or os.path.basename(image_path), "size_bytes": image_size,
        "case_meta": case_meta or {}, "engine_version": ENGINE_VERSION})
    try:
        # 1. integrity
        image_hash = hash_whole_image(image_path)
        acquisition = _acquisition_check(image_path, image_hash)
        custody.append("image_hashed", ENGINE_ACTOR, {
            "md5": image_hash[0], "sha256": image_hash[1],
            "acquisition_record_match": acquisition["hash_matches_acquisition"] if acquisition else None})

        # 2. device identification
        device = identify_device(image_path).to_dict()
        custody.append("device_identified", ENGINE_ACTOR, {
            k: device[k] for k in ("vendor", "model", "firmware", "format_family", "confidence")})

        # 3. routing
        plugin, detection = _route(image_path, force_oem)
        custody.append("parser_selected", ENGINE_ACTOR, {"plugin": plugin.name, "confidence": detection.confidence})
        logger.info("[%s] routed to %s plugin (confidence=%s)", case_id, plugin.name, detection.confidence)

        # 4. parse
        parsed = plugin.parse(image_path)
        known_clips: List[ClipEntry] = parsed.clips
        if tz:  # the DVR clock has no timezone of its own: attach the offset the investigator supplied
            known_clips = [replace(c, start_ts=c.start_ts.replace(tzinfo=tz) if c.start_ts else None,
                                   end_ts=c.end_ts.replace(tzinfo=tz) if c.end_ts else None) for c in known_clips]

        # 5. recover
        carved = (recover_additional_clips(image_path, image_size, known_clips, clip_id_prefix=case_id[:8])
                  if plugin.carve_uncovered else [])
        all_clips = known_clips + carved
        custody.append("clips_recovered", ENGINE_ACTOR, {"structured": len(known_clips), "carved": len(carved),
                                                          "parser_stats": parsed.stats})

        # 6. hash
        hashed = hash_clips(image_path, all_clips)
        custody.append("clips_hashed", ENGINE_ACTOR, {"count": len(hashed), "algorithms": ["MD5", "SHA-256"]})

        # 7. export
        exports, export_note = _export(image_path, output_dir, case_id, hashed) if export_clips else ({}, None)
        if exports or export_note:
            custody.append("clips_exported", ENGINE_ACTOR, {
                "count": len(exports), "note": export_note,
                "all_exports_match_clip_hash": all(v.get("dav_sha256_matches_clip", v.get("raw_sha256_matches_clip"))
                                                   for v in exports.values())})

        # 8. analytics
        analytics = {}
        if run_analytics and exports:
            analytics = _run_analytics(output_dir, case_id, exports)
            custody.append("analytics_run", ENGINE_ACTOR, {
                "clips_analysed": len(analytics),
                "clips_with_motion": sum(1 for a in analytics.values() if a.get("motion_events")),
                "errors": sum(1 for a in analytics.values() if a.get("error"))})

        # 9. timeline
        timeline = build_timeline(hashed, clock_offset_seconds=clock_offset_seconds, analytics=analytics)
        custody.append("timeline_built", ENGINE_ACTOR, {
            "basis": timeline["time_basis"], "clock_offset_seconds": clock_offset_seconds,
            "gaps": len(timeline["gaps"]), "motion_incidents": len(timeline["correlated_motion_incidents"])})

        # 10. integrity re-check
        integrity = {"image_md5": image_hash[0], "image_sha256": image_hash[1], "image_size": image_size,
                     "verified_after_processing": None, "acquisition": acquisition}
        if verify_after:
            integrity["verified_after_processing"] = hash_whole_image(image_path) == image_hash
            custody.append("integrity_verified", ENGINE_ACTOR, {"image_unchanged": integrity["verified_after_processing"]})

        # 11. report
        report = build_json_report(
            case_id, detection.oem, detection.confidence, image_hash, hashed, dvr_tz=dvr_tz,
            parser_stats=parsed.stats, exports=exports, export_note=export_note, device=device,
            integrity=integrity, timeline=timeline, analytics=analytics, examiner=examiner,
            case_meta=case_meta, engine_version=ENGINE_VERSION, custody_entries=custody.entries(),
            custody_verification=custody.verify())
        json_path = os.path.join(output_dir, f"{case_id}.json")
        pdf_path = os.path.join(output_dir, f"{case_id}.pdf")
        write_json_report(report, json_path)
        write_pdf_report(report, pdf_path, base_dir=output_dir)
        custody.append("report_generated", ENGINE_ACTOR, {"json_sha256": _sha256_file(json_path),
                                                           "pdf_sha256": _sha256_file(pdf_path)})
    except Exception as exc:
        custody.append("processing_failed", ENGINE_ACTOR, {"error": f"{type(exc).__name__}: {exc}"})
        raise

    return CaseResult(case_id=case_id, oem=detection.oem, oem_confidence=detection.confidence,
                      hashed_clips=hashed, json_report_path=json_path, pdf_report_path=pdf_path,
                      custody_path=custody_path, device=device, timeline=timeline, integrity=integrity)
