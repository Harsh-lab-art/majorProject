"""
Accuracy harness: compares what the engine recovered against Manish's ground-truth log.

    python evaluate.py --report <case>.json --truth truth.json [--tolerance 2] [--out result.json]

Inputs
  --report : the engine's JSON report (GET /report/<id>.json, or the file in the engine's data dir)
  --truth  : ground truth built from Manish's CSV by tools/convert_truth.py

Definitions (OURS - not identical to any paper's; see README before comparing to published numbers)
  recovery rate     = deleted ground-truth clips with >=1 recovered clip inside their time window
                      (same channel when both known) / all deleted ground-truth clips.
                      Measures PRESENCE, not completeness of the footage.
  temporal accuracy = of the recovered ground-truth clips, share whose earliest recovered timestamp
                      is within --tolerance seconds of the true start.
  false-positive    = recovered clips that carry a timestamp but fall in NO ground-truth window
                      (deleted or live) / recovered clips that carry a timestamp.
  unverifiable      = recovered clips with NO timestamp (e.g. Hikvision carved segments today).
                      They cannot be checked by time, so they are reported, never silently counted.
  exact (hash)      = if a truth clip lists the sha256 of an exported copy and some recovered clip has
                      the same sha256, it counts as recovered regardless of time. Only valid when the
                      export is byte-identical to the on-disk stream - confirm that before relying on it.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import Optional


def _parse_ts(value: Optional[str], what: str, assume_tz=None) -> Optional[datetime]:
    if value is None:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None and assume_tz is not None:
        return dt.replace(tzinfo=assume_tz)
    if dt.tzinfo is None:
        raise ValueError(
            f"{what} '{value}' has no timezone offset. Refusing to guess: a DVR clock is often "
            f"local time, and guessing wrong silently ruins temporal accuracy. Use tools/convert_truth.py --tz."
        )
    return dt


def evaluate(report: dict, truth: dict, tolerance_s: float = 2.0, dvr_tz: Optional[str] = None) -> dict:
    recovered = report.get("clips", [])
    assume = None
    if dvr_tz:
        from pipeline import _parse_tz
        assume = _parse_tz(dvr_tz)
    rec = []
    for c in recovered:
        rec.append({**c, "_ts": _parse_ts(c.get("start_ts"), f"recovered clip {c.get('clip_id')} start_ts", assume)})

    gt = []
    for t in truth["clips"]:
        gt.append({
            **t,
            "_start": _parse_ts(t["start"], f"truth clip {t['id']} start"),
            "_end": _parse_ts(t["end"], f"truth clip {t['id']} end"),
        })
    deleted = [t for t in gt if t["kind"] == "deleted"]

    def in_window(c, t):
        if c["_ts"] is None:
            return False
        if c.get("channel") is not None and t.get("channel") is not None and c["channel"] != t["channel"]:
            return False
        lo = (t["_start"] - _td(tolerance_s)); hi = (t["_end"] + _td(tolerance_s))
        return lo <= c["_ts"] <= hi

    details = []
    n_recovered = n_temporal_ok = 0
    errors = []
    sha_index = {c.get("sha256"): c for c in rec if c.get("sha256")}
    for t in deleted:
        exact = bool(t.get("sha256")) and t["sha256"] in sha_index
        matches = [c for c in rec if in_window(c, t)]
        found = exact or bool(matches)
        temporal_ok = None
        err = None
        if matches:
            earliest = min(c["_ts"] for c in matches)
            err = abs((earliest - t["_start"]).total_seconds())
            temporal_ok = err <= tolerance_s
            errors.append(err)
            n_temporal_ok += temporal_ok
        n_recovered += found
        details.append({
            "id": t["id"], "recovered": found, "match": "exact-hash" if exact else ("time-window" if matches else None),
            "matching_clips": len(matches), "start_error_s": err, "temporal_ok": temporal_ok,
        })

    stamped = [c for c in rec if c["_ts"] is not None]
    false_pos = [c for c in stamped if not any(in_window(c, t) for t in gt)]
    unverifiable = [c for c in rec if c["_ts"] is None]
    matched_with_time = sum(1 for d in details if d["temporal_ok"] is not None)

    def ratio(a, b):
        return None if b == 0 else a / b

    return {
        "tolerance_s": tolerance_s,
        "ground_truth_deleted": len(deleted),
        "recovered_deleted": n_recovered,
        "recovery_rate": ratio(n_recovered, len(deleted)),
        "temporal_accuracy": ratio(n_temporal_ok, matched_with_time),
        "mean_abs_start_error_s": (sum(errors) / len(errors)) if errors else None,
        "recovered_clips_total": len(rec),
        "recovered_with_timestamp": len(stamped),
        "false_positives": len(false_pos),
        "false_positive_rate": ratio(len(false_pos), len(stamped)),
        "unverifiable_no_timestamp": len(unverifiable),
        "per_truth_clip": details,
    }


def _td(seconds: float):
    from datetime import timedelta
    return timedelta(seconds=seconds)


def _pct(x):
    return "n/a" if x is None else f"{x * 100:.1f}%"


def format_summary(r: dict) -> str:
    lines = [
        f"Ground-truth deleted clips : {r['ground_truth_deleted']}",
        f"Recovered                  : {r['recovered_deleted']}   -> recovery rate     {_pct(r['recovery_rate'])}",
        f"Temporal accuracy (±{r['tolerance_s']}s)  : {_pct(r['temporal_accuracy'])}"
        + (f"   (mean start error {r['mean_abs_start_error_s']:.1f}s)" if r["mean_abs_start_error_s"] is not None else ""),
        f"Recovered clips w/ timestamp: {r['recovered_with_timestamp']}   false positives {r['false_positives']} -> {_pct(r['false_positive_rate'])}",
        f"Unverifiable (no timestamp): {r['unverifiable_no_timestamp']}   (reported, not counted for or against)",
    ]
    missed = [d["id"] for d in r["per_truth_clip"] if not d["recovered"]]
    if missed:
        lines.append("MISSED: " + ", ".join(missed))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", required=True)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--tolerance", type=float, default=2.0, help="seconds (default 2)")
    ap.add_argument("--dvr-tz", help="offset to attach if the report was made without one, e.g. +05:30")
    ap.add_argument("--out", help="also write the full result as JSON")
    args = ap.parse_args()
    with open(args.report) as f:
        report = json.load(f)
    with open(args.truth) as f:
        truth = json.load(f)
    result = evaluate(report, truth, args.tolerance, args.dvr_tz)
    print(format_summary(result))
    if args.out:
        with open(args.out, "w") as f:
            json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()
