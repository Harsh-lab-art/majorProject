"""
Generic (vendor-agnostic) parser plugin — the fallback for every OEM we have no structured
parser for yet (CP Plus units that do not use DHAV, Uniview, Matrix, TP-Link, Godrej,
Honeywell, ...).

It does no file-system parsing at all. It only claims an image when the sniff window holds an
unmistakable H.264 or H.265 stream start, and then hands the whole image to the shared
OEM-agnostic carver in recovery.py. That way an unknown recorder still yields hashed, exported
evidence instead of an "unsupported" error.

Sniff rule (strict on purpose, because file-system metadata is full of big-endian "1"s that
look like the start code 00 00 00 01):
  * H.264: a 4-byte start code followed by an SPS NAL (type 7) whose next byte is a real
    profile_idc, AND a PPS NAL (type 8).
  * H.265: the three parameter-set NAL headers VPS (40 01), SPS (42 01) and PPS (44 01), each
    after a 4-byte start code.

An examiner can also force this plugin (pipeline `force_oem="Generic"`) when the video does
not start in the first 8 MB.
"""

from __future__ import annotations

import re

from models import ParseResult
from parsers.base import BaseParser

_H264_PROFILES = {66, 77, 88, 100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135}
_H264_SPS = re.compile(rb"\x00\x00\x00\x01([\x27\x47\x67])(.)", re.S)
_H264_PPS = re.compile(rb"\x00\x00\x00\x01[\x28\x48\x68]")
_H265_SETS = (b"\x00\x00\x00\x01\x40\x01", b"\x00\x00\x00\x01\x42\x01", b"\x00\x00\x00\x01\x44\x01")


def looks_like_h264(data: bytes) -> bool:
    has_sps = any(m.group(2)[0] in _H264_PROFILES for m in _H264_SPS.finditer(data))
    return has_sps and _H264_PPS.search(data) is not None


def looks_like_h265(data: bytes) -> bool:
    return all(s in data for s in _H265_SETS)


class GenericParser(BaseParser):
    name = "Generic"
    confidence = "none"          # no vendor knowledge at all: carving only
    carve_uncovered = True

    def sniff(self, header: bytes) -> bool:
        return looks_like_h264(header) or looks_like_h265(header)

    def parse(self, image_path: str) -> ParseResult:
        return ParseResult(clips=[], stats={
            "note": "generic fallback: no vendor file-system parsing; all clips come from raw H.264/H.265 carving"})


PLUGIN = GenericParser()
