"""
OmniTrace command line — run the engine without the web stack (lab use, demos, validation).

    python cli.py analyze  IMAGE --out reports/ [--tz +05:30] [--clock-offset 300]
                           [--examiner "SI Sharma"] [--case-ref FIR-123/2026] [--analytics]
                           [--force-oem Generic]
    python cli.py acquire  --source \\\\.\\PhysicalDrive2 --out case.img --examiner "SI Sharma"
    python cli.py identify IMAGE
    python cli.py custody  reports/<case_id>.custody.jsonl
    python cli.py analytics VIDEO_FILE
"""

from __future__ import annotations

import argparse
import json
import logging
import sys


def _analyze(a) -> int:
    from models import UnsupportedOEMError
    from pipeline import process_image
    meta = {k: v for k, v in (("case_ref", a.case_ref), ("agency", a.agency), ("notes", a.notes)) if v}
    try:
        r = process_image(a.image, a.out, dvr_tz=a.tz, examiner=a.examiner, clock_offset_seconds=a.clock_offset,
                          run_analytics=a.analytics, force_oem=a.force_oem, case_meta=meta)
    except UnsupportedOEMError as exc:
        print(f"Unsupported image: {exc}\nTip: --force-oem Generic carves any H.264/H.265 video.", file=sys.stderr)
        return 3
    tl, dev = r.timeline, r.device
    print(f"Case        {r.case_id}")
    print(f"Device      {dev['vendor'] or 'unknown'} {dev['model'] or ''} fw={dev['firmware'] or '-'} ({dev['confidence']})")
    print(f"Parser      {r.oem} ({r.oem_confidence})")
    print(f"Image hash  SHA-256 {r.integrity['image_sha256']}")
    print(f"Unchanged   {r.integrity['verified_after_processing']}")
    print(f"Clips       {len(r.hashed_clips)}  (timed {tl['timed_clip_count']}, gaps {len(tl['gaps'])}, "
          f"motion incidents {len(tl['correlated_motion_incidents'])})")
    print(f"Reports     {r.json_report_path}\n            {r.pdf_report_path}")
    print(f"Custody     {r.custody_path}")
    return 0


def _acquire(a) -> int:
    import acquire
    args = ["--out", a.out] + (["--source", a.source] if a.source else ["--e01", a.e01])
    for flag, val in (("--examiner", a.examiner), ("--case-ref", a.case_ref)):
        if val:
            args += [flag, val]
    return acquire.main(args)


def _identify(a) -> int:
    from identify import identify_device
    print(json.dumps(identify_device(a.image).to_dict(), indent=2))
    return 0


def _custody(a) -> int:
    from custody import CustodyLog
    log = CustodyLog(a.file)
    for e in log.entries():
        print(f"{e['seq']:>3}  {e['timestamp_utc'][:19]}  {e['actor'][:24]:<24}  {e['action']}")
    v = log.verify()
    print(f"\nchain {'VALID' if v['valid'] else 'BROKEN'}  head={v['head_hash']}")
    for p in v["problems"]:
        print("  !", p)
    return 0 if v["valid"] else 2


def _analytics(a) -> int:
    from analytics import analyze_video
    print(json.dumps(analyze_video(a.video, sample_fps=a.sample_fps), indent=2))
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser(prog="omnitrace", description="OmniTrace multi-vendor DVR/NVR forensic engine")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("analyze", help="run the full pipeline on a raw image")
    p.add_argument("image")
    p.add_argument("--out", default="reports")
    p.add_argument("--tz", help="DVR clock offset from UTC, e.g. +05:30")
    p.add_argument("--clock-offset", type=float, default=0.0, help="DVR time minus true time, seconds")
    p.add_argument("--examiner")
    p.add_argument("--case-ref")
    p.add_argument("--agency")
    p.add_argument("--notes")
    p.add_argument("--analytics", action="store_true")
    p.add_argument("--force-oem", choices=["Dahua", "Hikvision", "Generic"])
    p.set_defaults(fn=_analyze)

    p = sub.add_parser("acquire", help="create a verified forensic image")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--source")
    g.add_argument("--e01")
    p.add_argument("--out", required=True)
    p.add_argument("--examiner")
    p.add_argument("--case-ref")
    p.set_defaults(fn=_acquire)

    p = sub.add_parser("identify", help="identify vendor / model / firmware only")
    p.add_argument("image")
    p.set_defaults(fn=_identify)

    p = sub.add_parser("custody", help="print and verify a chain-of-custody log")
    p.add_argument("file")
    p.set_defaults(fn=_custody)

    p = sub.add_parser("analytics", help="run video analytics on one video file")
    p.add_argument("video")
    p.add_argument("--sample-fps", type=float, default=2.0)
    p.set_defaults(fn=_analytics)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
