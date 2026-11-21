"""
Turn Manish's spreadsheet (CSV) into the truth.json the harness reads.

    python tools/convert_truth.py --csv ground_truth.csv --tz +05:30 --out truth.json

CSV columns (see tools/ground_truth_template.csv):
    id, channel, start, end, kind, sha256
  start/end : 'YYYY-MM-DD HH:MM:SS' exactly as the DVR displays it
  kind      : 'deleted' (removed on purpose) or 'live' (left on the DVR)
  sha256    : optional - SHA-256 of an exported copy of that clip, if the DVR let you export one
--tz is the DVR's clock offset from UTC (India = +05:30). Check the DVR's time-zone setting;
do not guess, a wrong value silently wrecks the temporal-accuracy number.
"""
import argparse
import csv
import json
import re
from datetime import datetime

KINDS = {"deleted", "live"}


def convert(csv_path: str, tz: str) -> dict:
    if not re.fullmatch(r"[+-]\d{2}:\d{2}", tz):
        raise SystemExit("--tz must look like +05:30")
    clips = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for n, row in enumerate(csv.DictReader(f), start=2):
            row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            if not row.get("id"):
                continue  # blank line
            kind = row.get("kind", "").lower()
            if kind not in KINDS:
                raise SystemExit(f"line {n}: kind must be 'deleted' or 'live', got '{kind}'")
            times = {}
            for key in ("start", "end"):
                try:
                    datetime.strptime(row[key], "%Y-%m-%d %H:%M:%S")
                except (KeyError, ValueError):
                    raise SystemExit(f"line {n}: {key} must be 'YYYY-MM-DD HH:MM:SS', got '{row.get(key, '')}'")
                times[key] = row[key].replace(" ", "T") + tz
            if times["end"] < times["start"]:
                raise SystemExit(f"line {n}: end is before start")
            clip = {"id": row["id"], "channel": int(row["channel"]) if row.get("channel") else None,
                    "start": times["start"], "end": times["end"], "kind": kind}
            if row.get("sha256"):
                clip["sha256"] = row["sha256"].lower()
            clips.append(clip)
    if not any(c["kind"] == "deleted" for c in clips):
        raise SystemExit("no rows with kind=deleted: nothing to measure recovery against")
    return {"clips": clips}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--tz", required=True)
    ap.add_argument("--out", default="truth.json")
    a = ap.parse_args()
    with open(a.out, "w") as f:
        json.dump(convert(a.csv, a.tz), f, indent=2)
    print(f"wrote {a.out}")
