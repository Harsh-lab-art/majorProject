"""
End-to-end tests against the synthetic images in tests/make_synthetic_images.py.
These prove the detector -> parser -> recovery -> hasher -> report
chain actually works, without needing Manish's real hardware yet.

Run with:
    pytest tests/ -v
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from make_synthetic_images import make_dahua_image, make_hikvision_image
from models import ClipStatus, UnsupportedOEMError
from parsers.dahua import DahuaParser
from pipeline import process_image


@pytest.fixture()
def dahua_image(tmp_path):
    path = str(tmp_path / "dahua.img")
    make_dahua_image(path, n_chunks=5)
    return path


@pytest.fixture()
def hikvision_image(tmp_path):
    path = str(tmp_path / "hikvision.img")
    make_hikvision_image(path, n_clusters=3)
    return path


def test_dahua_detected_and_parsed(dahua_image, tmp_path):
    result = process_image(dahua_image, output_dir=str(tmp_path / "out"))
    assert result.oem == "Dahua"
    assert result.oem_confidence == "documented"
    assert len(result.hashed_clips) == 5           # 5 segments of 12 frames, NOT 60 one-frame clips
    for hc in result.hashed_clips:
        assert hc.clip.frame_count == 12 and hc.clip.validated is True
        assert hc.clip.status == ClipStatus.CARVED  # we do not parse the DHFS index: cannot claim "live"
        assert hc.clip.end_ts > hc.clip.start_ts
        assert len(hc.md5) == 32 and len(hc.sha256) == 64


def test_dahua_hash_matches_manual_recompute(dahua_image, tmp_path):
    result = process_image(dahua_image, output_dir=str(tmp_path / "out"))
    import hashlib
    raw = open(dahua_image, "rb").read()
    first = result.hashed_clips[0]
    joined = b"".join(raw[o:o + l] for o, l in first.clip.extents)
    assert first.md5 == hashlib.md5(joined).hexdigest()
    assert first.sha256 == hashlib.sha256(joined).hexdigest()


def test_decoys_are_counted_and_rejected_not_reported(dahua_image, tmp_path):
    import json
    stats = json.load(open(process_image(dahua_image, str(tmp_path / "o")).json_report_path))["parser_stats"]
    assert stats["validated_frames"] == 60
    assert stats["rejected_bad_length"] >= 1 and stats["rejected_no_footer"] >= 1
    assert stats["rejected_bad_date"] >= 1 and stats["skipped_inside_frame"] >= 1


def test_interleaved_channels_group_per_channel_and_hash_only_own_frames(tmp_path):
    import hashlib
    img = str(tmp_path / "i.img")
    make_dahua_image(img, n_chunks=3, interleave=True)
    res = process_image(img, str(tmp_path / "o"))
    assert len(res.hashed_clips) == 3
    raw = open(img, "rb").read()
    for hc in res.hashed_clips:
        assert len(hc.clip.extents) > 1                       # frames of other channels sit between
        joined = b"".join(raw[o:o + l] for o, l in hc.clip.extents)
        assert hc.sha256 == hashlib.sha256(joined).hexdigest()   # clip hash = its own frames only
        assert sum(l for _, l in hc.clip.extents) == hc.clip.length


def test_sniff_accepts_documented_signatures_only():
    p = DahuaParser()
    assert p.sniff(b"\x00" * 512 + b"DHFS4.1" + b"\x00" * 2000)
    assert not p.sniff(os.urandom(4096))
    from parsers.hikvision import HikvisionParser
    h = HikvisionParser()
    assert h.sniff(b"\x00" * 512 + b"HIKVISION@HANGZHOU")
    assert not h.sniff(b"HIKVISION only, no suffix")


def test_hikvision_detected_and_carved(hikvision_image, tmp_path):
    result = process_image(hikvision_image, output_dir=str(tmp_path / "out"))
    assert result.oem == "Hikvision"
    assert result.oem_confidence == "documented"
    # parser.parse() returns [] by design (see parsers/hikvision.py);
    # every clip here must come from the shared carver instead.
    assert len(result.hashed_clips) >= 3
    for hc in result.hashed_clips:
        assert hc.clip.status == ClipStatus.CARVED


def test_reports_are_written(dahua_image, tmp_path):
    out_dir = str(tmp_path / "out")
    result = process_image(dahua_image, output_dir=out_dir)
    assert os.path.exists(result.json_report_path)
    assert os.path.exists(result.pdf_report_path)
    assert os.path.getsize(result.pdf_report_path) > 0


def test_unsupported_image_raises(tmp_path):
    junk_path = str(tmp_path / "junk.img")
    with open(junk_path, "wb") as f:
        f.write(os.urandom(4096))
    with pytest.raises(UnsupportedOEMError):
        process_image(junk_path, output_dir=str(tmp_path / "out"))


def test_isolated_noise_hit_is_not_reported_as_a_clip(tmp_path):
    """A single plausible NAL start in otherwise random data is noise, not video."""
    import random
    from make_synthetic_images import quiet_bytes
    rng = random.Random(3)
    img = str(tmp_path / "noise.img")
    with open(img, "wb") as f:
        f.write(b"\x00" * 512 + b"HIKVISION@HANGZHOU" + quiet_bytes(rng, 5000))
        f.write(b"\x00\x00\x01\x65" + quiet_bytes(rng, 3000))       # ONE lone hit
        f.write(quiet_bytes(rng, 3 * 1024 * 1024))
    assert process_image(img, str(tmp_path / "o")).hashed_clips == []
