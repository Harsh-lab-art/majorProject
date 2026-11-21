"""The streaming scanner must give the same answer as a naive whole-file scan, for ANY chunk size."""
import io
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import scan
from hasher import _hash_byte_range
from make_synthetic_images import make_dahua_image, make_hikvision_image
from pipeline import process_image


def naive(data, pattern, start, end, extra, overlapping):
    out, i, nxt = [], start, start
    region = data[:end]
    while True:
        idx = region.find(pattern, i)
        if idx == -1 or idx < nxt:
            if idx == -1:
                break
        if idx + len(pattern) + extra > end:
            break
        if idx >= nxt:
            out.append((idx, data[idx + len(pattern): idx + len(pattern) + extra]))
            nxt = idx + (1 if overlapping else len(pattern))
        i = idx + 1
    return out


@pytest.mark.parametrize("chunk", [1, 2, 3, 4, 5, 7, 16, 64, 1000, 10**6])
@pytest.mark.parametrize("pattern,extra,overlapping", [(b"DHAV", 0, False), (b"\x00\x00\x01", 1, True)])
def test_matches_equal_naive_scan_for_every_chunk_size(chunk, pattern, extra, overlapping):
    rng = random.Random(1)
    data = bytearray(rng.randbytes(600))
    for pos in (0, 3, 50, 51, 52, 255, 256, 597):   # includes adjacent + boundary-straddling hits
        data[pos:pos + len(pattern)] = pattern
    data[200:204] = b"\x00\x00\x00\x01"             # 4-byte start code form
    data = bytes(data)
    for start, end in [(0, len(data)), (10, 400)]:
        got = list(scan.iter_matches(io.BytesIO(data), pattern, start, end, extra, overlapping, chunk))
        assert got == naive(data, pattern, start, end, extra, overlapping)


def test_pipeline_gives_identical_results_with_tiny_chunks(tmp_path, monkeypatch):
    d = str(tmp_path / "d.img"); h = str(tmp_path / "h.img")
    make_dahua_image(d, n_chunks=5); make_hikvision_image(h, n_clusters=3)
    ref = {p: process_image(p, str(tmp_path / "ref")) for p in (d, h)}
    monkeypatch.setattr(scan, "DEFAULT_CHUNK", 4097)     # awkward size so hits straddle chunks
    for p in (d, h):
        small = process_image(p, str(tmp_path / "small"))
        assert [(c.clip.offset, c.clip.length, c.md5) for c in small.hashed_clips] == \
               [(c.clip.offset, c.clip.length, c.md5) for c in ref[p].hashed_clips]


def test_scanner_survives_the_caller_moving_the_file_handle():
    data = (b"x" * 50 + b"DHAV") * 40
    f = io.BytesIO(data)
    got = []
    for off, _ in scan.iter_matches(f, b"DHAV", 0, len(data), 0, False, 37):
        got.append(off)
        f.seek(0)        # what the Dahua parser does while streaming
        f.read(3)
    assert got == [i * 54 + 50 for i in range(40)]
