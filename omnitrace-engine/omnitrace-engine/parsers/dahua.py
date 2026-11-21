"""
Dahua parser plugin (DHAV frames).

SOURCES for the frame layout used below:
  * FFmpeg libavformat/dhav.c (open source) - field ORDER and the bit-packed date.
  * Rzayeva et al., Information (MDPI) 2025, 16(11):983, Fig. 4 / Sec. 3.2 - header+footer
    layout and the 32-byte overhead.  The two sources agree on field order.

    offset  size  field
      0      4    magic "DHAV"
      4      1    type            (0xfd video, 0xf0 audio per FFmpeg; others exist)
      5      1    subtype
      6      1    channel
      7      1    sub-channel / frame_subnumber
      8      4    frame_number    (u32 LE)
     12      4    frame_length    (u32 LE)  TOTAL frame size, header + payload + footer
     16      4    date            (u32 LE)  bit-packed LOCAL time, see _decode_date
     20      2    16-bit running timestamp (u16 LE)   [FFmpeg: absent when type == 0xf1; unused here]
     22      1    extended-header length N            [unused here]
     23      1    header checksum                     [NOT verified here]
     24      N    extended header (TLV: width/height 0x80/0x82, codec+fps 0x81, audio 0x83/0x8c ...)
   24+N     m    payload (H.264/H.265/audio)   m = frame_length - 32 - N
  len-8      4    footer magic "dhav"
  len-4      4    footer size field == frame_length   [FFmpeg get_duration() relies on this]

Frame types, from FFmpeg dhav_read_packet(): 0xfd = video key frame, 0xfc = video non-key frame
(both belong to the SAME video stream), 0xf0 = audio, 0xf1 = non-media (FFmpeg skips it).

WHAT IS STILL UNVERIFIED ON REAL HARDWARE (do not claim otherwise):
  * Everything above is from published sources, not from a Dahua unit we imaged ourselves.
  * The header checksum is not checked. A frame is accepted only if the header magic, a sane
    frame_length, a decodable date, the footer magic AND footer-size == frame_length all hold.
  * The date has NO timezone: it is the DVR's own local clock. We return naive datetimes and the
    pipeline attaches the DVR offset the user supplies (dvr_tz). We never assume UTC.

Frames that fail validation are COUNTED (stats.rejected_*) but not reported as clips: a header
match alone is exactly the kind of false positive the paper's dual-signature check exists to avoid.

Validated VIDEO frames (0xfd + 0xfc) are grouped into recording SEGMENTS (one ClipEntry per
segment) so a real disk does not yield millions of one-frame "clips". Audio and non-media frames
are counted in stats but are not reported as clips. Grouping rule, per channel video stream:
next frame continues the segment if frame_number is +0..+2 from the previous and the timestamp
gap is <= MAX_GAP_SECONDS (the paper's gap rule uses frame-number jumps > 2 and a gap tolerance
relative to the frame interval; ours is a fixed gap, to be tuned on real data).
"""

from __future__ import annotations

import os
import struct
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from models import ClipEntry, ClipStatus, ParseResult
from parsers.base import BaseParser
from scan import iter_matches

MAGIC = b"DHAV"
FOOTER_MAGIC = b"dhav"
VIDEO_TYPES = (0xFD, 0xFC)   # key frame, non-key frame: one video stream
AUDIO_TYPE = 0xF0
_HEADER = struct.Struct("<4sBBBBIII")   # magic,type,subtype,channel,subchannel,frame_no,frame_len,date
_HEADER_READ = 24                       # we read the full 24-byte header
_FOOTER_SIZE = 8
_MIN_FRAME_LENGTH = 24 + _FOOTER_SIZE   # 32: header + footer with an empty payload
_MAX_FRAME_LENGTH = 64 * 1024 * 1024
_MIN_DHAV_HITS_TO_CONFIRM = 3
MAX_GAP_SECONDS = 10                    # tune on real data
_MAX_FRAME_NUMBER_STEP = 2              # paper: discontinuity if frame numbers differ by more than 2
_DHFS_SIGNATURE = b"DHFS4.1"
_DHFS_OFFSETS = (512, 1024, 2048)       # paper: checked offsets for the file-system signature


def _decode_date(date: int) -> Optional[datetime]:
    """Bit layout from FFmpeg dhav.c get_timeinfo(). Returns None if it is not a real date."""
    sec = date & 0x3F
    minute = (date >> 6) & 0x3F
    hour = (date >> 12) & 0x1F
    day = (date >> 17) & 0x1F
    month = (date >> 22) & 0x0F
    year = ((date >> 26) & 0x3F) + 2000
    try:
        return datetime(year, month, day, hour, minute, sec)   # naive: DVR local time
    except ValueError:
        return None


def encode_date(dt: datetime) -> int:
    """Inverse of _decode_date (used by the test generator)."""
    return (dt.second | (dt.minute << 6) | (dt.hour << 12) | (dt.day << 17)
            | (dt.month << 22) | ((dt.year - 2000) << 26))


class _Segment:
    __slots__ = ("channel", "ftype", "first_ts", "last_ts", "last_no", "frames", "extents", "start")

    def __init__(self, channel, ftype, ts, no, offset, length):
        self.channel, self.ftype = channel, ftype
        self.first_ts = self.last_ts = ts
        self.last_no = no
        self.frames = 1
        self.start = offset
        self.extents: List[List[int]] = [[offset, length]]

    def add(self, ts, no, offset, length):
        self.last_ts, self.last_no = ts, no
        self.frames += 1
        last = self.extents[-1]
        if last[0] + last[1] == offset:          # physically adjacent: merge into one extent
            last[1] += length
        else:
            self.extents.append([offset, length])


class DahuaParser(BaseParser):
    name = "Dahua"
    confidence = "documented"   # published layout; not yet confirmed on our own hardware
    carve_uncovered = False     # validated DHAV frames ARE the video; NAL-scanning the leftovers
                                # (file-system metadata) would only add false positives

    def sniff(self, header: bytes) -> bool:
        for off in _DHFS_OFFSETS:
            if _DHFS_SIGNATURE in header[off: off + 1024]:
                return True
        return header.count(MAGIC) >= _MIN_DHAV_HITS_TO_CONFIRM

    def parse(self, image_path: str) -> ParseResult:
        size = os.path.getsize(image_path)
        stats = {"candidates": 0, "validated_frames": 0, "video_frames": 0, "audio_frames_not_clipped": 0,
                 "non_media_frames": 0, "rejected_bad_header": 0, "rejected_bad_length": 0,
                 "rejected_bad_date": 0, "rejected_no_footer": 0, "rejected_footer_size_mismatch": 0,
                 "skipped_inside_frame": 0}
        open_segments: Dict[int, _Segment] = {}
        finished: List[_Segment] = []
        cursor = 0   # candidates before this offset lie inside a frame we already accepted

        with open(image_path, "rb") as f:
            for offset, _ in iter_matches(f, MAGIC, 0, size):
                stats["candidates"] += 1
                if offset < cursor:
                    stats["skipped_inside_frame"] += 1
                    continue
                f.seek(offset)
                head = f.read(_HEADER_READ)
                if len(head) < _HEADER_READ:
                    stats["rejected_bad_header"] += 1
                    continue
                (_m, ftype, _sub, channel, _subch, frame_no, frame_len, date) = _HEADER.unpack(head[:20])
                if not (_MIN_FRAME_LENGTH <= frame_len <= _MAX_FRAME_LENGTH) or offset + frame_len > size:
                    stats["rejected_bad_length"] += 1
                    continue
                ts = _decode_date(date)
                if ts is None:
                    stats["rejected_bad_date"] += 1
                    continue
                f.seek(offset + frame_len - _FOOTER_SIZE)
                footer = f.read(_FOOTER_SIZE)
                if footer[:4] != FOOTER_MAGIC:
                    stats["rejected_no_footer"] += 1
                    continue
                if struct.unpack("<I", footer[4:8])[0] != frame_len:
                    stats["rejected_footer_size_mismatch"] += 1
                    continue

                stats["validated_frames"] += 1
                cursor = offset + frame_len
                if ftype == AUDIO_TYPE:
                    stats["audio_frames_not_clipped"] += 1
                    continue
                if ftype not in VIDEO_TYPES:
                    stats["non_media_frames"] += 1
                    continue
                stats["video_frames"] += 1
                key = channel                      # key and non-key frames share one stream per channel
                seg = open_segments.get(key)
                step = None if seg is None else frame_no - seg.last_no
                gap = None if seg is None else (ts - seg.last_ts).total_seconds()
                if seg is not None and 0 <= step <= _MAX_FRAME_NUMBER_STEP and 0 <= gap <= MAX_GAP_SECONDS:
                    seg.add(ts, frame_no, offset, frame_len)
                else:
                    if seg is not None:
                        finished.append(seg)
                    open_segments[key] = _Segment(channel, 0xFD, ts, frame_no, offset, frame_len)

        finished.extend(open_segments.values())
        finished.sort(key=lambda s: s.start)
        clips = [self._to_clip(i, s) for i, s in enumerate(finished)]
        stats["segments"] = len(clips)
        return ParseResult(clips=clips, stats=stats)

    @staticmethod
    def _to_clip(index: int, s: _Segment) -> ClipEntry:
        extents = [tuple(e) for e in s.extents]
        return ClipEntry(
            clip_id=f"dahua-{index:06d}",
            offset=extents[0][0],
            length=sum(e[1] for e in extents),
            start_ts=s.first_ts,
            end_ts=s.last_ts,
            channel=s.channel,
            status=ClipStatus.CARVED,       # we do not parse the DHFS index, so we cannot say live vs deleted
            codec=None,                      # codec lives in extended header fields we do not parse yet
            frame_count=s.frames,
            frame_type="video",
            validated=True,
            extents=extents,
        )


PLUGIN = DahuaParser()
