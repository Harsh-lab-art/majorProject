"""
Shared data model for the OmniTrace forensic engine.

Every parser (Hikvision, Dahua, future OEMs) must produce a list of
ClipEntry objects. Nothing downstream (recovery, hashing, reporting)
is allowed to know which OEM it came from — that is the whole point
of the plugin architecture in the SIH26150 design doc, Section 7.6.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class ClipStatus(str, Enum):
    LIVE = "live"              # present in the file table, not deleted
    DELETED_RECOVERABLE = "deleted_recoverable"   # marked deleted, data not yet overwritten
    DELETED_UNRECOVERABLE = "deleted_unrecoverable"  # marked deleted, data overwritten/gone
    CARVED = "carved"          # found by raw carving, not in any file table


@dataclass
class ClipEntry:
    """One video clip, live or recovered."""
    clip_id: str
    offset: int                 # byte offset into the raw disk image
    length: int                 # length in bytes
    start_ts: Optional[datetime] = None
    end_ts: Optional[datetime] = None
    channel: Optional[int] = None          # camera/channel number, if known
    status: ClipStatus = ClipStatus.LIVE
    codec: Optional[str] = None            # only set when actually known, never guessed
    source_offset_table: Optional[int] = None  # where in the FS we found this entry (debug aid)
    frame_count: Optional[int] = None      # number of frames grouped into this clip (segment)
    frame_type: Optional[str] = None       # raw vendor frame-type byte, e.g. "0xfd"
    validated: Optional[bool] = None       # passed the parser's integrity checks (header + footer)
    # A clip's bytes are the concatenation of these (offset, length) extents. Frames of
    # different channels interleave on disk, so a clip is NOT one contiguous byte range.
    extents: Optional[list] = None

    def byte_ranges(self) -> list:
        return list(self.extents) if self.extents else [(self.offset, self.length)]

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "extents"}
        d["status"] = self.status.value
        d["start_ts"] = self.start_ts.isoformat() if self.start_ts else None
        d["end_ts"] = self.end_ts.isoformat() if self.end_ts else None
        d["extent_count"] = len(self.extents) if self.extents else 1
        if self.extents and len(self.extents) <= 20:
            d["extents"] = [list(e) for e in self.extents]
        return d


@dataclass
class ParseResult:
    """What a parser plugin returns: the clips plus honest counters about what it rejected."""
    clips: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)


class UnsupportedOEMError(Exception):
    """Raised when the OEM detector can't match any registered plugin."""


class CaseStatus(str, Enum):
    PROCESSING = "Processing"
    RECOVERED = "Recovered"
    HASHED = "Hashed"
    REPORT_READY = "Report Ready"
    FAILED = "Failed"
