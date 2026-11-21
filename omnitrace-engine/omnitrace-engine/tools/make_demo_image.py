"""
Builds a DEMO disk image for presentations: a multi-camera Dahua-format (DHAV) recorder disk
carrying REAL H.264 video with staged events, so every engine module has something to show.

    python tools/make_demo_image.py --out sample_data/demo_cpplus.img
    python cli.py analyze sample_data/demo_cpplus.img --out reports --tz +05:30 --analytics \
        --examiner "Demo Examiner" --case-ref DEMO-001

What is staged (all SYNTHETIC — say so when presenting):
  * System area with a CP Plus model string + firmware on a Dahua-family file system
    -> device identification shows "CP Plus ... on a Dahua-family file system".
  * 3 cameras, 2 recording sessions each, 5 minutes apart -> recording gaps on the timeline.
  * A "person" walks across camera 0, then camera 1 a moment later -> a correlated
    cross-camera motion incident. Camera 2 sees nothing in session 1 (static scene).
  * Frames of all cameras interleaved on disk, as real multi-channel recorders write them.

Needs an ffmpeg with libx264 (on PATH, OMNITRACE_FFMPEG, or `pip install imageio-ffmpeg`) and OpenCV.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from export import find_ffmpeg  # noqa: E402
from make_real_dhav import dhav_video_frame  # noqa: E402

W, H, FPS, SECONDS = 320, 240, 10, 12


def _scene(cam: int, walk: tuple | None, path: str) -> None:
    """Static 'room' + optional walking figure between walk=(t_start, t_end) seconds."""
    rng = np.random.default_rng(cam)
    base = np.zeros((H, W, 3), np.uint8)
    base[:] = (70 + 20 * cam, 80, 90)
    cv2.rectangle(base, (0, 170), (W, H), (60, 60, 60), -1)                      # floor
    for _ in range(6):                                                         # furniture
        x, y = int(rng.integers(0, W - 60)), int(rng.integers(40, 150))
        cv2.rectangle(base, (x, y), (x + 50, y + 30), tuple(int(c) for c in rng.integers(40, 200, 3)), -1)
    cv2.putText(base, f"CAM {cam + 1}", (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    out = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), FPS, (W, H))
    for i in range(FPS * SECONDS):
        img = base.copy()
        t = i / FPS
        if walk and walk[0] <= t <= walk[1]:
            x = int(-30 + (W + 60) * (t - walk[0]) / (walk[1] - walk[0]))
            cv2.rectangle(img, (x, 110), (x + 26, 185), (30, 30, 160), -1)       # body
            cv2.circle(img, (x + 13, 98), 11, (150, 180, 220), -1)              # head
        out.write(img)
    out.release()


def _h264_units(ffmpeg: str, src: str, dst: str):
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", src, "-c:v", "libx264", "-g", str(FPS),
                    "-bf", "0", "-pix_fmt", "yuv420p",
                    "-x264-params", f"aud=1:keyint={FPS}:min-keyint={FPS}:scenecut=0", "-f", "h264", dst], check=True)
    d = open(dst, "rb").read()
    starts = [m.start() - 1 if m.start() > 0 and d[m.start() - 1] == 0 else m.start()
              for m in re.finditer(rb"\x00\x00\x01", d) if m.end() < len(d) and d[m.end()] & 0x1F == 9]
    starts.append(len(d))
    units = []
    for a, b in zip(starts, starts[1:]):
        au = d[a:b]
        units.append((any(au[m.end()] & 0x1F == 5 for m in re.finditer(rb"\x00\x00\x01", au) if m.end() < len(au)), au))
    return units


def make_demo_image(out_path: str, start: datetime = datetime(2026, 11, 20, 22, 14, 0)) -> dict:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise SystemExit("ffmpeg with libx264 is required (pip install imageio-ffmpeg)")
    sessions = [  # (offset from start, per-camera walk windows in seconds)
        (timedelta(0), [(2.0, 5.0), (6.5, 9.5), None]),
        (timedelta(minutes=5), [None, (1.0, 4.0), (3.0, 6.5)]),
    ]
    with tempfile.TemporaryDirectory() as tmp, open(out_path, "wb") as f:
        f.write(b"\x00" * 512 + b"DHFS4.1".ljust(64, b"\x00"))
        f.write(b"\x00" * 1024 + b"DEVTYPE=CP-UVR-0401E1-IC2\x00SWVER=V4.1.2 build 220415\x00BRAND=CP PLUS\x00")
        f.write(b"\x00" * 4096)
        frame_no = 1000
        for s, (offset, walks) in enumerate(sessions):
            streams = []
            for cam, walk in enumerate(walks):
                avi = os.path.join(tmp, f"s{s}c{cam}.avi")
                _scene(cam, walk, avi)
                streams.append(_h264_units(ffmpeg, avi, os.path.join(tmp, f"s{s}c{cam}.h264")))
            n = min(len(st) for st in streams)
            for k in range(n):
                dt = start + offset + timedelta(seconds=k // FPS)
                for cam, st in enumerate(streams):
                    is_key, au = st[k]
                    f.write(dhav_video_frame(is_key, cam, frame_no + k, dt, int((k % FPS) * 1000 / FPS), au, FPS))
            frame_no += n + 100
            f.write(b"\x00" * 65536)
    return {"path": out_path, "size": os.path.getsize(out_path), "cameras": 3, "sessions": len(sessions)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "sample_data", "demo_cpplus.img"))
    a = ap.parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    print(make_demo_image(a.out))
