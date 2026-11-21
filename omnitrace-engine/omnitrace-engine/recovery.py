"""
Recovery / Carving — Section 7.2 (step 4) and 7.6 of the design doc.

This is the shared stage that runs AFTER an OEM parser plugin, and it
is deliberately OEM-agnostic: it has no idea whether the bytes came
from a Hikvision or a Dahua image. It does one job — find raw H.264/
H.265 video data that no parser already accounted for — using the
same technique classic file-carving tools (PhotoRec, Scalpel) use:
scan for the codec's own start-code markers and treat dense runs of
them as a recoverable clip, regardless of what file system (or lack
of one) surrounds them.

This is also precisely what makes Hikvision support usable in Month 1
even though parsers/hikvision.py honestly returns zero structured
clips: every byte of Hikvision footage still gets a pass through here.
"""

from __future__ import annotations

from typing import List, Tuple

from models import ClipEntry, ClipStatus
from scan import iter_matches

# Annex-B start codes. We accept both the 3-byte and 4-byte forms;
# in practice most encoders emit the 4-byte form at clip/GOP starts
# and the 3-byte form between NAL units within a frame.
_START_CODE_3 = b"\x00\x00\x01"

# If we go this many bytes without seeing another plausible NAL start,
# we consider the current run of video data finished and start a new
# carved segment on the next hit. This threshold is a tuning knob —
# raise it if real test data shows clips getting split up too eagerly,
# lower it if unrelated video chunks are getting merged into one.
_MAX_GAP_BYTES = 2 * 1024 * 1024  # 2 MB

# A byte immediately after a start code is only treated as a real NAL
# header if its low 5 bits look like a plausible H.264/H.265 NAL unit
# type. This cuts down heavily on false positives from random zero
# bytes elsewhere on the disk.
# Real video produces many NAL starts per second. A lone `00 00 01 xx` in random-looking data
# (about 6 per GB by chance) is noise, so a segment needs at least this many starts to count.
# Tune on real data; the cost of a high value is dropping very short real fragments.
_MIN_NAL_HITS_PER_SEGMENT = 3
_PLAUSIBLE_NAL_TYPES = set(range(1, 24))  # covers slice, IDR, SPS, PPS, SEI, etc.


def find_uncovered_regions(image_size: int, known_clips: List[ClipEntry]) -> List[Tuple[int, int]]:
    """
    Given the clips a parser already found, return the (start, end)
    byte ranges nothing has claimed yet. Carving only runs on these
    ranges, so we never re-report something the parser already knows
    about as if it were newly "recovered".
    """
    if not known_clips:
        return [(0, image_size)]

    covered = sorted((o, o + l) for c in known_clips for (o, l) in c.byte_ranges())
    gaps: List[Tuple[int, int]] = []
    cursor = 0
    for start, end in covered:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < image_size:
        gaps.append((cursor, image_size))
    return gaps


def _find_nal_starts(f, region_start: int, region_end: int) -> List[int]:
    """Absolute offsets of plausible NAL unit starts in [region_start, region_end), streamed."""
    hits = []
    for offset, trailing in iter_matches(f, _START_CODE_3, region_start, region_end, extra=1, overlapping=True):
        if (trailing[0] & 0x1F) in _PLAUSIBLE_NAL_TYPES:
            hits.append(offset)
    return hits


def carve_region(image_path: str, region_start: int, region_end: int, clip_id_prefix: str) -> List[ClipEntry]:
    """
    Run NAL-start-code carving over one byte range of the image (streamed, never loaded
    whole) and return merged carved segments as ClipEntry objects.
    """
    with open(image_path, "rb") as f:
        hits = _find_nal_starts(f, region_start, region_end)
    if not hits:
        return []

    segments: List[Tuple[int, int]] = []
    seg_start = seg_end = hits[0]
    count = 1
    for h in hits[1:]:
        if h - seg_end > _MAX_GAP_BYTES:
            if count >= _MIN_NAL_HITS_PER_SEGMENT:
                segments.append((seg_start, seg_end))
            seg_start, count = h, 0
        seg_end = h
        count += 1
    if count >= _MIN_NAL_HITS_PER_SEGMENT:
        segments.append((seg_start, seg_end))

    clips = []
    for i, (s, e) in enumerate(segments):
        clips.append(
            ClipEntry(
                clip_id=f"{clip_id_prefix}-carved-{i:06d}",
                offset=s,
                length=(e - s) or 1,
                status=ClipStatus.CARVED,
                codec="H264/H265 (carved, codec not disambiguated)",
            )
        )
    return clips


def recover_additional_clips(image_path: str, image_size: int, known_clips: List[ClipEntry], clip_id_prefix: str = "carved") -> List[ClipEntry]:
    """
    Public entry point: find everything in `image_path` that
    `known_clips` doesn't already cover, and carve it.
    """
    recovered: List[ClipEntry] = []
    for region_start, region_end in find_uncovered_regions(image_size, known_clips):
        recovered.extend(carve_region(image_path, region_start, region_end, clip_id_prefix))
    return recovered
