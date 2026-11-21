"""
Hash + Timestamp Normalize — Section 7.2 (step 5) of the design doc.

Every recovered clip gets an MD5 and a SHA-256 over its exact bytes
(the concatenation of its extents) in the raw image. This is what makes a recovered clip legally
useful: anyone can re-extract those same bytes later and prove they
are bit-for-bit identical to what this tool reported, independent of
whether they trust the tool itself (BSA 2023 Section 63 territory —
see the design doc's References).

Timestamps are normalized to UTC ISO-8601 wherever a parser supplied
one; clips with no timestamp (e.g. Hikvision carved segments in
Month 1, since we don't parse Hikvision's real timestamp fields yet)
are explicitly reported as `start_ts: null`, never guessed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import List

from models import ClipEntry

_CHUNK_SIZE = 4 * 1024 * 1024  # read the clip in 4 MB chunks, not all at once


@dataclass
class HashedClip:
    clip: ClipEntry
    md5: str
    sha256: str


def _hash_extents(image_path: str, extents) -> tuple[str, str]:
    """MD5 + SHA-256 over the concatenation of (offset, length) extents, streamed."""
    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    with open(image_path, "rb") as f:
        for offset, length in extents:
            f.seek(offset)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                md5.update(chunk)
                sha256.update(chunk)
                remaining -= len(chunk)
    return md5.hexdigest(), sha256.hexdigest()


def _hash_byte_range(image_path: str, offset: int, length: int) -> tuple[str, str]:
    return _hash_extents(image_path, [(offset, length)])


def hash_clips(image_path: str, clips: List[ClipEntry]) -> List[HashedClip]:
    hashed = []
    for clip in clips:
        md5_hex, sha256_hex = _hash_extents(image_path, clip.byte_ranges())
        hashed.append(HashedClip(clip=clip, md5=md5_hex, sha256=sha256_hex))
    return hashed


def hash_whole_image(image_path: str) -> tuple[str, str]:
    """
    Case-level integrity hash of the entire raw image, taken once at
    acquisition time. Lets anyone verify the working copy still
    matches what was first ingested, on top of per-clip hashes.
    """
    import os

    size = os.path.getsize(image_path)
    return _hash_byte_range(image_path, 0, size)
