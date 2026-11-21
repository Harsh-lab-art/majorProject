"""
Timeline Analysis module: timestamp normalisation, recording gaps, cross-camera correlation.

TIMESTAMP NORMALISATION (two separate corrections, both supplied by the examiner):
  1. Time zone: DVR clocks have no zone. The pipeline attaches `dvr_tz` (e.g. +05:30); here we
     convert to UTC. Without it, times stay naive ("DVR local") and are flagged as such.
  2. Clock drift: DVR clocks are often minutes or years off. At seizure the examiner notes the
     DVR's displayed time against a trusted reference (phone/NTP).
         clock_offset_seconds = DVR displayed time - true time   (positive = DVR runs fast)
     corrected time = DVR time - clock_offset_seconds.
  Both the raw DVR time and the corrected UTC time are kept, so nothing is overwritten.

GAPS: per channel, any stretch longer than `gap_threshold_seconds` with no recovered footage.
A gap is NOT proof of deletion (the DVR may have been off, or recording on motion only), but
it tells the examiner where to look, and where carving found nothing.

CORRELATION:
  * concurrent_recording_windows: periods where 2+ cameras have footage at the same time.
  * correlated_motion_incidents: periods where analytics saw motion on 2+ cameras within
    `correlation_tolerance_seconds` of each other (e.g. a person walking past camera 1 then 2).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional


def normalize_ts(ts: Optional[datetime], clock_offset_seconds: float = 0.0) -> Optional[datetime]:
    if ts is None:
        return None
    t = ts - timedelta(seconds=clock_offset_seconds)
    return t.astimezone(timezone.utc) if t.tzinfo else t


def _iso(t: Optional[datetime]) -> Optional[str]:
    return t.isoformat() if t else None


def _merge_windows(events: List[dict], tolerance_s: float, min_channels: int = 2) -> List[dict]:
    """Merge time-overlapping events (within tolerance); keep windows spanning >= min_channels."""
    out, cur = [], None
    tol = timedelta(seconds=tolerance_s)
    for ev in sorted(events, key=lambda e: e["_start"]):
        if cur and ev["_start"] <= cur["_end"] + tol:
            cur["_end"] = max(cur["_end"], ev["_end"])
            cur["channels"].add(ev["channel"])
            cur["ids"].append(ev["id"])
        else:
            if cur:
                out.append(cur)
            cur = {"_start": ev["_start"], "_end": ev["_end"], "channels": {ev["channel"]}, "ids": [ev["id"]]}
    if cur:
        out.append(cur)
    return [{"start": _iso(w["_start"]), "end": _iso(w["_end"]),
             "duration_s": round((w["_end"] - w["_start"]).total_seconds(), 3),
             "channels": sorted(w["channels"], key=str), "ids": w["ids"]}
            for w in out if len(w["channels"]) >= min_channels]


def build_timeline(hashed_clips, clock_offset_seconds: float = 0.0, gap_threshold_seconds: float = 60.0,
                   correlation_tolerance_seconds: float = 5.0, analytics: Optional[Dict[str, dict]] = None) -> dict:
    events, untimed = [], 0
    for hc in hashed_clips:
        c = hc.clip
        start = normalize_ts(c.start_ts, clock_offset_seconds)
        if start is None:
            untimed += 1
            continue
        end = normalize_ts(c.end_ts, clock_offset_seconds) or start
        events.append({"id": c.clip_id, "channel": c.channel if c.channel is not None else "unknown",
                       "_start": start, "_end": end, "status": c.status.value, "sha256": hc.sha256,
                       "raw_start_dvr_clock": _iso(c.start_ts)})
    events.sort(key=lambda e: e["_start"])
    tz_known = bool(events) and events[0]["_start"].tzinfo is not None

    # per-channel summary + gaps
    by_channel: Dict[object, List[dict]] = defaultdict(list)
    for ev in events:
        by_channel[ev["channel"]].append(ev)
    channels, all_gaps = {}, []
    for ch, evs in sorted(by_channel.items(), key=lambda kv: str(kv[0])):
        gaps, covered_until = [], None
        for ev in evs:
            if covered_until is not None and (ev["_start"] - covered_until).total_seconds() > gap_threshold_seconds:
                gap = {"channel": ch, "from": _iso(covered_until), "to": _iso(ev["_start"]),
                       "duration_s": round((ev["_start"] - covered_until).total_seconds(), 3)}
                gaps.append(gap)
                all_gaps.append(gap)
            covered_until = ev["_end"] if covered_until is None else max(covered_until, ev["_end"])
        channels[str(ch)] = {
            "clip_count": len(evs), "first": _iso(evs[0]["_start"]), "last": _iso(max(e["_end"] for e in evs)),
            "recorded_seconds": round(sum((e["_end"] - e["_start"]).total_seconds() for e in evs), 3),
            "gap_count": len(gaps)}

    concurrent = _merge_windows(events, 0.0)

    # motion events -> absolute time, then correlate across cameras
    motion_events = []
    for ev in events:
        res = (analytics or {}).get(ev["id"]) or {}
        for i, m in enumerate(res.get("motion_events") or []):
            motion_events.append({
                "id": f"{ev['id']}#m{i}", "clip_id": ev["id"], "channel": ev["channel"],
                "_start": ev["_start"] + timedelta(seconds=m["start_s"]),
                "_end": ev["_start"] + timedelta(seconds=m["end_s"]), "peak_ratio": m.get("peak_ratio")})
    motion_incidents = _merge_windows(motion_events, correlation_tolerance_seconds)

    def public(ev):
        out = {k: v for k, v in ev.items() if not k.startswith("_")}
        out["start"], out["end"] = _iso(ev["_start"]), _iso(ev["_end"])
        out["duration_s"] = round((ev["_end"] - ev["_start"]).total_seconds(), 3)
        return out

    return {
        "timezone_normalized": tz_known,
        "time_basis": "UTC" if tz_known else "DVR local clock (no dvr_tz supplied)",
        "clock_offset_seconds": clock_offset_seconds,
        "gap_threshold_seconds": gap_threshold_seconds,
        "correlation_tolerance_seconds": correlation_tolerance_seconds,
        "earliest": _iso(events[0]["_start"]) if events else None,
        "latest": _iso(max(e["_end"] for e in events)) if events else None,
        "timed_clip_count": len(events),
        "untimed_clip_count": untimed,
        "channels": channels,
        "gaps": all_gaps,
        "concurrent_recording_windows": concurrent,
        "motion_events": [public(m) for m in motion_events],
        "correlated_motion_incidents": motion_incidents,
        "events": [public(e) for e in events],
    }
