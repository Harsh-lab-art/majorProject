import hashlib
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from acquire import _copy_stream, acquire_image


def _source(tmp_path, n=3 * 1024 * 1024 + 123):
    data = os.urandom(n)
    p = tmp_path / "disk.bin"
    p.write_bytes(data)
    return str(p), data


def test_image_is_bit_exact_and_hashed_during_copy(tmp_path):
    src, data = _source(tmp_path)
    dest = str(tmp_path / "case.img")
    rec = acquire_image(src, dest, examiner="SI Sharma", case_ref="FIR-1", chunk=1024 * 1024)
    assert open(dest, "rb").read() == data
    assert rec["image_md5"] == hashlib.md5(data).hexdigest()
    assert rec["image_sha256"] == hashlib.sha256(data).hexdigest()
    assert rec["verification"]["matches_acquisition_hash"] and rec["complete"]
    assert rec["bad_sector_count"] == 0
    side = json.load(open(dest + ".acquisition.json"))
    assert side["examiner"] == "SI Sharma" and "write-blocker" in side["write_blocker_note"]


def test_never_overwrites_existing_evidence(tmp_path):
    src, _ = _source(tmp_path, 4096)
    dest = tmp_path / "case.img"
    dest.write_bytes(b"precious")
    with pytest.raises(FileExistsError):
        acquire_image(src, str(dest))
    assert dest.read_bytes() == b"precious"


class _FlakyDisk(io.BytesIO):
    """Raises OSError on any read touching bytes [bad_from, bad_to)."""

    def __init__(self, data, bad_from, bad_to):
        super().__init__(data)
        self.bad = (bad_from, bad_to)

    def read(self, n=-1):
        pos = self.tell()
        if pos < self.bad[1] and pos + n > self.bad[0]:
            raise OSError("I/O error")
        return super().read(n)


def test_bad_sectors_are_zero_filled_and_listed():
    data = bytes(range(256)) * 64          # 16 KiB
    disk = _FlakyDisk(data, 4096, 4096 + 1024)   # sectors at 4096 and 4608 unreadable
    out = io.BytesIO()
    res = _copy_stream(disk, out, len(data), chunk=8192, sector=512, progress=None)
    img = out.getvalue()
    assert len(img) == len(data)                         # offsets preserved
    assert img[4096:5120] == b"\x00" * 1024
    assert img[:4096] == data[:4096] and img[5120:] == data[5120:]
    assert res["bad_sector_offsets"] == [4096, 4608] and res["bad_sectors_zero_filled"]
    assert res["sha256"] == hashlib.sha256(img).hexdigest()


def test_pipeline_checks_image_against_acquisition_record(tmp_path):
    from make_synthetic_images import make_dahua_image
    from pipeline import process_image
    raw = str(tmp_path / "raw.img")
    make_dahua_image(raw, n_chunks=2)
    dest = str(tmp_path / "case.img")
    acquire_image(raw, dest, examiner="SI Sharma")
    r = process_image(dest, str(tmp_path / "out"))
    assert r.integrity["acquisition"]["hash_matches_acquisition"] is True
    with open(dest, "r+b") as f:          # simulate the image being altered after acquisition
        f.seek(10)
        f.write(b"\xff")
    r2 = process_image(dest, str(tmp_path / "out2"))
    assert r2.integrity["acquisition"]["hash_matches_acquisition"] is False
