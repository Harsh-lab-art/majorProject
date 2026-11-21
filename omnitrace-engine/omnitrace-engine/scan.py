"""
Streaming pattern scanner.

Real DVR disk images are hundreds of GB, so nothing in the engine may read a whole
image into memory. This finds a byte pattern across a file region in fixed-size chunks,
correctly handling matches that straddle a chunk boundary.
"""

from __future__ import annotations

from typing import BinaryIO, Iterator, Tuple

DEFAULT_CHUNK = 16 * 1024 * 1024  # 16 MB


def iter_matches(
    f: BinaryIO,
    pattern: bytes,
    start: int,
    end: int,
    extra: int = 0,
    overlapping: bool = False,
    chunk_size: int | None = None,
) -> Iterator[Tuple[int, bytes]]:
    """
    Yield (absolute_offset, trailing_bytes) for each occurrence of `pattern` in [start, end).

    `extra` = how many bytes AFTER the pattern the caller needs (e.g. the NAL header byte
    after a start code). A match is only reported once all of its `extra` bytes are inside
    the region, so a match with fewer than `extra` bytes left before `end` is dropped.
    `overlapping` = whether a match may begin inside the previous match (matches the old
    behaviour: True for NAL start codes, False for the DHAV magic).
    """
    chunk_size = chunk_size or DEFAULT_CHUNK
    plen = len(pattern)
    need = plen + extra
    carry = b""
    pos = start                 # absolute offset of the next byte to read
    next_allowed = start        # earliest absolute offset a new match may start at
    while pos < end:
        f.seek(pos)          # re-seek every round: callers may move this handle between yields
        data = f.read(min(chunk_size, end - pos))
        if not data:
            break
        buf = carry + data
        base = pos - len(carry)
        pos += len(data)
        i = 0
        while True:
            idx = buf.find(pattern, i)
            if idx == -1 or idx + need > len(buf):
                break  # no more complete matches in this buffer; a partial one is retried next round
            absolute = base + idx
            if absolute >= next_allowed:
                yield absolute, buf[idx + plen: idx + need]
                next_allowed = absolute + (1 if overlapping else plen)
            i = idx + 1
        carry = buf[-(need - 1):] if need > 1 else b""
