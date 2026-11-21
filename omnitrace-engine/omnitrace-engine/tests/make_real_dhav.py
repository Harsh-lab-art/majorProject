"""
Builds a DHAV disk image whose payloads are REAL H.264 (made by ffmpeg/libx264), laid out the way
FFmpeg's own dhav.c reads them (type/channel/frame no./length/date/16-bit ts/ext-length/TLV ext
header, 'dhav'+size footer, key frames 0xfd, non-key 0xfc). Because FFmpeg is an independent
implementation, "FFmpeg can read the clip we exported" is a genuine cross-check of our parser.

Still synthetic: it proves we agree with FFmpeg's reading of the format, not that a given
DVR's firmware writes it that way.
"""
from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from parsers.dahua import encode_date  # noqa: E402

_HEAD = struct.Struct("<4sBBBBIII")
W, H = 320, 240


def have_ffmpeg() -> bool:
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-demuxers"], capture_output=True, text=True).stdout
    return " dhav " in out


def h264_access_units(tmp_path: str, pattern: str, seconds: int, fps: int):
    """Return [(is_key, bytes)] access units of a real H.264 stream, split at AUD NAL units."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"{pattern}=size={W}x{H}:rate={fps}",
         "-t", str(seconds), "-c:v", "libx264", "-g", str(fps), "-bf", "0",
         "-x264-params", f"aud=1:keyint={fps}:min-keyint={fps}:scenecut=0", "-f", "h264", tmp_path],
        check=True)
    d = open(tmp_path, "rb").read()
    starts = []
    for m in re.finditer(rb"\x00\x00\x01", d):
        if d[m.end()] & 0x1F == 9:                      # access unit delimiter
            starts.append(m.start() - 1 if m.start() > 0 and d[m.start() - 1] == 0 else m.start())
    starts.append(len(d))
    units = []
    for a, b in zip(starts, starts[1:]):
        au = d[a:b]
        is_key = any((au[m.end()] & 0x1F) == 5 for m in re.finditer(rb"\x00\x00\x01", au) if m.end() < len(au))
        units.append((is_key, au))
    return units


def dhav_video_frame(is_key: bool, channel: int, frame_no: int, dt: datetime, ms: int, payload: bytes, fps: int) -> bytes:
    ext = bytes([0x81, 0x00, 0x08, fps, 0x80, 0x00, W // 8, H // 8]) if is_key else b""   # codec 8 = H.264
    total = 24 + len(ext) + len(payload) + 8
    head = _HEAD.pack(b"DHAV", 0xFD if is_key else 0xFC, 0, channel, 0, frame_no, total, encode_date(dt))
    head += struct.pack("<HBB", ms % 65536, len(ext), 0)
    return head + ext + payload + b"dhav" + struct.pack("<I", total)


def make_real_dhav_image(path: str, workdir: str, channels: int = 2, seconds: int = 4, fps: int = 10,
                         start: datetime = datetime(2026, 11, 20, 10, 0, 0)) -> list:
    """Interleaves `channels` real H.264 streams, frame by frame. Returns what was written per channel."""
    patterns = ["testsrc", "testsrc2", "smptebars", "rgbtestsrc"]
    streams = [h264_access_units(os.path.join(workdir, f"c{c}.h264"), patterns[c % 4], seconds, fps) for c in range(channels)]
    n = min(len(s) for s in streams)
    with open(path, "wb") as f:
        f.write(b"\x00" * 512 + b"DHFS4.1".ljust(64, b"\x00"))
        for k in range(n):
            dt = start + timedelta(seconds=k // fps)
            for c in range(channels):
                is_key, au = streams[c][k]
                f.write(dhav_video_frame(is_key, c, 1000 + k, dt, int((k % fps) * 1000 / fps), au, fps))
    return [{"channel": c, "start": start, "frames": n, "keyframes": sum(1 for kf, _ in streams[c][:n] if kf),
             "fps": fps} for c in range(channels)]
