"""
Builds fake-but-structurally-valid raw disk images so the pipeline can be tested before a real
extracted image exists (design doc, Section 7.7).

HONESTY NOTE: the Dahua generator follows the SAME published layout the parser implements
(FFmpeg dhav.c + MDPI 2025). So passing tests prove the parser matches the published spec and
that its validation/grouping logic works - NOT that real Dahua firmware follows that spec.

    python tests/make_synthetic_images.py
"""

from __future__ import annotations

import os
import random
import struct
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from parsers.dahua import encode_date  # noqa: E402

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "..", "sample_data")
_HEAD = struct.Struct("<4sBBBBIII")


def quiet_bytes(rng: random.Random, n: int) -> bytes:
    """Seeded filler that can never contain a NAL start code or the DHAV magic by accident,
    so the tests are deterministic (os.urandom made them flaky)."""
    return rng.randbytes(n).replace(b"\x00\x00\x01", b"\x00\x02\x01").replace(b"DHAV", b"DHAX")


def dhav_frame(ftype: int, channel: int, frame_no: int, dt: datetime, payload: bytes,
               footer: bool = True, bad_len: int | None = None) -> bytes:
    total = 24 + len(payload) + 8
    head = _HEAD.pack(b"DHAV", ftype, 0, channel, 0, frame_no, bad_len if bad_len is not None else total,
                      encode_date(dt)) + struct.pack("<HBB", 0, 0, 0)
    tail = (b"dhav" + struct.pack("<I", total)) if footer else b"\x00" * 8
    return head + payload + tail


def make_dahua_image(path: str, n_chunks: int = 5, seed: int = 42, frames_per_segment: int = 12,
                     interleave: bool = False, decoys: bool = True) -> list:
    """
    Writes `n_chunks` recording segments (each `frames_per_segment` frames, 1 s apart, channel i%4),
    60 s apart in time. Returns what it REALLY wrote, for tests to use as independent ground truth.
    interleave=True emits frames round-robin across segments, as multi-channel DVRs do.
    decoys=True sprinkles in things a naive "find DHAV" carver would wrongly report.
    """
    rng = random.Random(seed)
    now = int(time.time()) - n_chunks * 600
    segments = []
    for i in range(n_chunks):
        segments.append({"channel": i % 4, "ts": now + i * 60, "frames": frames_per_segment})

    def payload():
        return quiet_bytes(rng, rng.randint(200, 1500))

    def frames_of(i, seg):
        for k in range(seg["frames"]):
            dt = datetime.fromtimestamp(seg["ts"] + k, tz=timezone.utc).replace(tzinfo=None)
            p = payload()
            if decoys and i == 0 and k == 3:
                p = p[:40] + b"DHAV" + quiet_bytes(rng, 20) + p[40:]     # a DHAV look-alike INSIDE a real frame
            yield dhav_frame(0xFD, seg["channel"], 1000 + k, dt, p)

    streams = [list(frames_of(i, s)) for i, s in enumerate(segments)]
    order = []
    if interleave:
        for k in range(frames_per_segment):
            for st in streams:
                order.append(st[k])
    else:
        for st in streams:
            order.extend(st)

    with open(path, "wb") as f:
        f.write(quiet_bytes(rng, 512))                 # junk before the first frame
        f.write(b"DHFS4.1".ljust(64, b"\x00"))         # file-system signature
        for n, fr in enumerate(order):
            f.write(fr)
            f.write(quiet_bytes(rng, rng.randint(0, 64)))   # small inter-frame gap
            if decoys and n == 5:
                dt = datetime(2026, 1, 1, 0, 0, 0)
                f.write(dhav_frame(0xFD, 9, 1, dt, payload(), bad_len=0xFFFFFFFF))   # absurd length
                f.write(dhav_frame(0xFD, 9, 2, dt, payload(), footer=False))          # truncated: no footer
                bad_date = _HEAD.pack(b"DHAV", 0xFD, 0, 9, 0, 3, 40, (13 << 22)) + b"\x00" * 4 + b"x" * 8 + b"dhav" + b"\x00" * 4
                f.write(bad_date)                                                       # impossible month
    return segments


def make_hikvision_image(path: str, n_clusters: int = 3, seed: int = 7) -> None:
    """HIKVISION@HANGZHOU at offset 512 (published master-sector marker), then separated NAL clusters."""
    rng = random.Random(seed)
    with open(path, "wb") as f:
        f.write(b"\x00" * 512)
        f.write(b"HIKVISION@HANGZHOU")
        f.write(quiet_bytes(rng, 1024))
        for _ in range(n_clusters):
            f.write(quiet_bytes(rng, 3 * 1024 * 1024))  # big gap so clusters stay separate clips
            for _ in range(rng.randint(5, 10)):
                f.write(b"\x00\x00\x01" + bytes([rng.choice([1, 5, 7, 8])]))
                f.write(quiet_bytes(rng, rng.randint(500, 4000)))
        f.write(quiet_bytes(rng, 1024))


def main() -> None:
    os.makedirs(SAMPLE_DIR, exist_ok=True)
    d = os.path.join(SAMPLE_DIR, "synthetic_dahua.img")
    h = os.path.join(SAMPLE_DIR, "synthetic_hikvision.img")
    make_dahua_image(d)
    make_hikvision_image(h)
    print(f"Wrote {d} ({os.path.getsize(d)} bytes)")
    print(f"Wrote {h} ({os.path.getsize(h)} bytes)")


if __name__ == "__main__":
    main()
