"""
Clip export: turn a recovered clip back into something a person can play.

A Dahua clip is a list of byte extents (its own frames, in disk order). Concatenating those
extents yields a valid DHAV stream, i.e. a `.dav` file FFmpeg and Dahua players can read.
Because the file is exactly the bytes we hashed, **its SHA-256 equals the clip's reported
SHA-256**: anyone can re-hash the exported file and check it against the report. We assert that.

The MP4 is a convenience remux (`ffmpeg -c copy`, no re-encoding) and is best-effort: it needs
ffmpeg on the PATH and a stream FFmpeg can parse. The `.dav` is the evidentiary copy.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

from models import ClipEntry

_CHUNK = 4 * 1024 * 1024


@dataclass
class ExportResult:
    dav_path: str
    sha256: str
    mp4_path: Optional[str] = None
    mp4_error: Optional[str] = None


def export_clip_dav(image_path: str, clip: ClipEntry, out_path: str) -> str:
    """Write the clip's extents to out_path; return the SHA-256 of what was written."""
    h = hashlib.sha256()
    with open(image_path, "rb") as src, open(out_path, "wb") as dst:
        for offset, length in clip.byte_ranges():
            src.seek(offset)
            remaining = length
            while remaining > 0:
                chunk = src.read(min(_CHUNK, remaining))
                if not chunk:
                    break
                h.update(chunk)
                dst.write(chunk)
                remaining -= len(chunk)
    return h.hexdigest()


def find_ffmpeg() -> Optional[str]:
    """OMNITRACE_FFMPEG env var, else `ffmpeg` on PATH, else the binary bundled by imageio-ffmpeg."""
    env = os.environ.get("OMNITRACE_FFMPEG")
    if env and os.path.exists(env):
        return env
    if shutil.which("ffmpeg"):
        return shutil.which("ffmpeg")
    try:
        import imageio_ffmpeg  # optional: pip install imageio-ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def remux_to_mp4(dav_path: str, mp4_path: str, timeout_s: int = 120) -> Optional[str]:
    """Stream-copy into MP4. Returns None on success, otherwise a short error string."""
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        return "ffmpeg not installed"
    try:
        r = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", dav_path, "-c", "copy", "-movflags", "+faststart", mp4_path],
            capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return "ffmpeg timed out"
    if r.returncode != 0:
        return (r.stderr.strip().splitlines() or ["ffmpeg failed"])[-1][:200]
    return None
