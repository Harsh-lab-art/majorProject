"""
Acquisition module: create a verified forensic image of a DVR/NVR hard disk.

What it does (the same guarantees `dd conv=noerror,sync` + `md5sum` give, in one step):
  * Reads the source READ-ONLY, in fixed chunks, never loading it into memory.
  * Hashes MD5 + SHA-256 WHILE copying ("hash on acquisition"), then re-reads the finished image
    and hashes it again ("verification hash"). Both must match.
  * Bad sectors: a failed chunk is retried sector by sector; unreadable sectors are written as
    zeros (so offsets stay correct) and listed in the record. Nothing is skipped silently.
  * Never overwrites: refuses if the destination exists (evidence must not be clobbered).
  * Writes a sidecar <image>.acquisition.json (source, size, examiner, times, hashes, errors).
    The pipeline picks that record up automatically and checks the image still matches it.

E01 (EnCase / FTK Imager) images are converted to raw with `ewf_to_raw` if libewf-python is
installed (pip install libewf-python); the conversion is hashed the same way.

WRITE BLOCKING: opening a disk read-only does NOT stop the operating system from writing to it
(e.g. Windows auto-mount). Always attach the DVR disk through a hardware write-blocker.
This is a procedural requirement, recorded in the SOP and in every acquisition record.

Usage:
    python acquire.py --source \\\\.\\PhysicalDrive2 --out case42.img --examiner "SI Sharma"
    python acquire.py --source /dev/sdb --out case42.img --examiner "SI Sharma"
    python acquire.py --e01 seized.E01 --out case42.img --examiner "SI Sharma"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timezone
from typing import Callable, Optional

DEFAULT_CHUNK = 4 * 1024 * 1024
TOOL = "OmniTrace acquire.py"
WRITE_BLOCK_NOTE = "Source must be attached via a hardware write-blocker; read-only open alone does not prevent OS writes."
_MAX_LISTED_BAD_SECTORS = 1000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_file(path: str, chunk: int = DEFAULT_CHUNK) -> tuple:
    md5, sha = hashlib.md5(), hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            md5.update(block)
            sha.update(block)
    return md5.hexdigest(), sha.hexdigest()


def _source_size(src) -> Optional[int]:
    try:
        size = src.seek(0, 2)
        src.seek(0)
        return size or None
    except OSError:
        return None


def _read_sectorwise(src, pos: int, want: int, sector: int, bad: list) -> tuple:
    """Re-read a failed chunk one sector at a time. Returns (data, every_sector_failed)."""
    out, failed = bytearray(), 0
    for off in range(pos, pos + want, sector):
        n = min(sector, pos + want - off)
        try:
            src.seek(off)
            data = src.read(n)
        except OSError:
            bad.append(off)
            failed += 1
            out += b"\x00" * n
            continue
        if not data:
            break
        out += data
    return bytes(out), failed > 0 and failed * sector >= want


def _copy_stream(src, dst, total: Optional[int], chunk: int, sector: int,
                 progress: Optional[Callable[[int, Optional[int]], None]]) -> dict:
    md5, sha = hashlib.md5(), hashlib.sha256()
    bad: list = []
    pos = 0
    while total is None or pos < total:
        want = chunk if total is None else min(chunk, total - pos)
        try:
            src.seek(pos)
            data = src.read(want)
        except OSError:
            data, all_failed = _read_sectorwise(src, pos, want, sector, bad)
            if all_failed and total is None:     # unknown size and nothing readable: end of device
                for _ in range(len(data) // sector):
                    bad.pop()
                break
        if not data:
            break
        md5.update(data)
        sha.update(data)
        dst.write(data)
        pos += len(data)
        if progress:
            progress(pos, total)
    return {"bytes_copied": pos, "md5": md5.hexdigest(), "sha256": sha.hexdigest(),
            "bad_sector_count": len(bad), "bad_sector_offsets": bad[:_MAX_LISTED_BAD_SECTORS],
            "bad_sectors_zero_filled": bool(bad)}


def _finish(record: dict, dest: str, verify: bool) -> dict:
    if verify:
        md5, sha = hash_file(dest)
        record["verification"] = {"md5": md5, "sha256": sha, "verified_at_utc": _now(),
                                  "matches_acquisition_hash": md5 == record["image_md5"] and sha == record["image_sha256"]}
    with open(dest + ".acquisition.json", "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    return record


def _base_record(source: str, dest: str, examiner, case_ref, notes, sector: int) -> dict:
    return {"tool": TOOL, "tool_host": platform.node(), "tool_os": platform.platform(),
            "source": source, "image_path": os.path.abspath(dest), "image_format": "raw (dd)",
            "examiner": examiner, "case_ref": case_ref, "notes": notes, "sector_size": sector,
            "write_blocker_note": WRITE_BLOCK_NOTE, "started_utc": _now()}


def acquire_image(source: str, dest: str, examiner: Optional[str] = None, case_ref: Optional[str] = None,
                  notes: Optional[str] = None, size: Optional[int] = None, chunk: int = DEFAULT_CHUNK,
                  sector: int = 512, verify: bool = True,
                  progress: Optional[Callable[[int, Optional[int]], None]] = None) -> dict:
    """Image `source` (a device path or file) to raw file `dest`. Returns the acquisition record."""
    if chunk % sector:
        raise ValueError("chunk must be a multiple of the sector size")
    record = _base_record(source, dest, examiner, case_ref, notes, sector)
    with open(source, "rb", buffering=0) as src, open(dest, "xb") as dst:   # "x": never overwrite
        total = size if size is not None else _source_size(src)
        record["source_size_reported"] = total
        result = _copy_stream(src, dst, total, chunk, sector, progress)
    record.update(finished_utc=_now(), image_size=result["bytes_copied"], image_md5=result["md5"],
                  image_sha256=result["sha256"], bad_sector_count=result["bad_sector_count"],
                  bad_sector_offsets=result["bad_sector_offsets"],
                  bad_sectors_zero_filled=result["bad_sectors_zero_filled"],
                  complete=total is None or result["bytes_copied"] == total)
    return _finish(record, dest, verify)


def ewf_to_raw(e01_path: str, dest: str, examiner: Optional[str] = None, case_ref: Optional[str] = None,
               notes: Optional[str] = None, verify: bool = True, progress=None) -> dict:
    """Convert an E01 (EWF) image set to raw, hashing as it goes. Needs libewf-python."""
    try:
        import pyewf  # type: ignore
    except ImportError as exc:
        raise RuntimeError("E01 support needs libewf-python: pip install libewf-python") from exc
    handle = pyewf.handle()
    handle.open(pyewf.glob(e01_path))
    try:
        total = handle.get_media_size()
        record = _base_record(e01_path, dest, examiner, case_ref, notes, 512)
        record["source_format"] = "E01 (EWF)"
        with open(dest, "xb") as dst:
            result = _copy_stream(handle, dst, total, DEFAULT_CHUNK, 512, progress)
    finally:
        handle.close()
    record.update(finished_utc=_now(), image_size=result["bytes_copied"], image_md5=result["md5"],
                  image_sha256=result["sha256"], bad_sector_count=0, bad_sector_offsets=[],
                  bad_sectors_zero_filled=False, complete=result["bytes_copied"] == total)
    return _finish(record, dest, verify)


def _print_progress(done: int, total: Optional[int]) -> None:
    if total:
        sys.stderr.write(f"\r  {done / 1024**2:,.0f} / {total / 1024**2:,.0f} MB ({100 * done / total:5.1f}%)")
    else:
        sys.stderr.write(f"\r  {done / 1024**2:,.0f} MB")
    sys.stderr.flush()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Create a verified forensic image of a DVR/NVR disk.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--source", help="device or file to image, e.g. \\\\.\\PhysicalDrive2 or /dev/sdb")
    src.add_argument("--e01", help="E01 image to convert to raw")
    ap.add_argument("--out", required=True, help="raw image to create (must not exist)")
    ap.add_argument("--examiner")
    ap.add_argument("--case-ref")
    ap.add_argument("--notes")
    ap.add_argument("--size", type=int, help="source size in bytes, if the OS cannot report it")
    ap.add_argument("--no-verify", action="store_true")
    a = ap.parse_args(argv)
    if a.source:
        rec = acquire_image(a.source, a.out, a.examiner, a.case_ref, a.notes, a.size, verify=not a.no_verify,
                            progress=_print_progress)
    else:
        rec = ewf_to_raw(a.e01, a.out, a.examiner, a.case_ref, a.notes, verify=not a.no_verify, progress=_print_progress)
    sys.stderr.write("\n")
    print(json.dumps({k: rec[k] for k in ("image_path", "image_size", "image_md5", "image_sha256",
                                          "bad_sector_count", "complete")}, indent=2))
    v = rec.get("verification")
    if v is not None:
        print("verification:", "MATCH" if v["matches_acquisition_hash"] else "MISMATCH")
        return 0 if v["matches_acquisition_hash"] else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
