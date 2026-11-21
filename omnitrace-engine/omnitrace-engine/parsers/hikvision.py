"""
Hikvision parser plugin.

HONEST SCOPE NOTE (important — read before extending this file):
Unlike Dahua's DHAV container, Hikvision does not use a simple,
publicly-reliable magic-number-delimited format for its video data.
Real Hikvision file-table parsing (reading the DVR's internal
allocation table to know exactly where each clip lives, live or
deleted-but-not-yet-overwritten) requires reverse-engineering an
actual image from a real unit — see Han et al. 2015 ("Analysis of
the HIKVISION DVR File System", cited in the design doc's References)
for the published starting point, and Section 7.7 of the design doc
for how Manish's handoff gives us a real image to calibrate against.

Until that calibration happens, this plugin only does two honest
things:
  1. `sniff()` — a real, cheap heuristic: Hikvision system partitions
     the first 4 KB for the published signature b"HIKVISION@HANGZHOU" (the master-sector marker) in their
     header/system area. Good enough to route to this plugin; not a
     formal magic number the way DHAV is.
  2. `parse()` — returns an EMPTY list of "known-live" clips. We are
     not pretending to understand the Hikvision file table yet. All
     Hikvision recovery in Month 1 therefore comes entirely from the
     shared generic carver in recovery.py (raw H.264/H.265 NAL-unit
     scanning), which is OEM-agnostic and doesn't need this plugin to
     know anything about Hikvision's internals.

Replacing this TODO with real file-table parsing is explicitly a
Month 2 task once we have a real Hikvision image to test against —
do not hand-wave fake byte offsets into this file before then.
"""

from __future__ import annotations

from models import ParseResult
from parsers.base import BaseParser

# Documented signature (MDPI Information 2025, 16(11):983 checks it at offsets 512/1024/2048;
# two independent secondary sources name the same string). NOT yet seen on a real unit by us.
SIGNATURE_STRING = b"HIKVISION@HANGZHOU"
_SNIFF_BYTES = 4096


class HikvisionParser(BaseParser):
    name = "Hikvision"
    confidence = "documented"  # published signature, not yet confirmed on our own hardware

    def sniff(self, header: bytes) -> bool:
        return SIGNATURE_STRING in header[:_SNIFF_BYTES]

    def parse(self, image_path: str) -> ParseResult:
        # TODO: real file-table parsing needs Han & Jeong 2015 (Springer, paywalled) or a real
        # image. The Hikvision frame layout printed in the MDPI 2025 paper is a single unverified
        # source with no hex dumps, so it is deliberately NOT implemented here.
        return ParseResult(clips=[], stats={"note": "no Hikvision file-table parsing yet; carving only"})


PLUGIN = HikvisionParser()
