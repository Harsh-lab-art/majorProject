import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import identify
from identify import identify_device


def _img(tmp_path, body: bytes, name="x.img"):
    p = tmp_path / name
    p.write_bytes(body)
    return str(p)


def test_cp_plus_on_dahua_family_filesystem(tmp_path):
    body = b"\x00" * 512 + b"DHFS4.1" + b"\x00" * 3000 + b"model=CP-UVR-0401E1-IC2\x00" + b"\x00" * 100 + b"CP PLUS\x00"
    d = identify_device(_img(tmp_path, body))
    assert d.vendor == "CP Plus" and d.confidence == "strong"
    assert d.model == "CP-UVR-0401E1-IC2"
    assert d.format_family == "Dahua"
    assert "OEM" in d.note


def test_hikvision_signature_brand_and_firmware(tmp_path):
    body = b"\x00" * 512 + b"HIKVISION@HANGZHOU" + b"\x00" * 900 + b"DS-7608NI-K2\x00V4.30.085 build 200916\x00"
    d = identify_device(_img(tmp_path, body))
    assert d.vendor == "Hikvision" and d.confidence == "strong"
    assert d.model == "DS-7608NI-K2"
    assert d.firmware == "V4.30.085 build 200916"


def test_vendor_inferred_from_filesystem_only(tmp_path):
    d = identify_device(_img(tmp_path, b"\x00" * 1024 + b"DHFS4.1" + b"\x00" * 1000))
    assert d.vendor == "Dahua" and d.confidence == "inferred_from_format" and d.model is None


def test_brand_only_is_weak(tmp_path):
    d = identify_device(_img(tmp_path, b"\x00" * 100 + b"Honeywell Security\x00" + b"\x00" * 100))
    assert d.vendor == "Honeywell" and d.confidence == "weak"


def test_nothing_found(tmp_path):
    d = identify_device(_img(tmp_path, b"\x11" * 5000))
    assert d.vendor is None and d.confidence == "none"


def test_evidence_offsets_point_at_the_bytes(tmp_path):
    body = b"\xaa" * 777 + b"VIGI NVR1004H\x00" + b"\xaa" * 50 + b"NVR301-04S\x00uniview"
    path = _img(tmp_path, body)
    d = identify_device(path)
    raw = open(path, "rb").read()
    assert d.evidence
    for e in d.evidence:
        assert raw[e["offset"]: e["offset"] + len(e["match"])].decode() == e["match"]
    # TP-Link model (2) vs Uniview model + brand (3): Uniview wins, both kept as evidence
    assert d.vendor == "Uniview" and d.confidence == "strong"
    assert d.scores == {"TP-Link": 2, "Uniview": 3}


def test_tied_vendors_flagged_as_conflict(tmp_path):
    d = identify_device(_img(tmp_path, b"\x00" * 10 + b"VIGI NVR1004H\x00NVR301-04S\x00" + b"\x00" * 10))
    assert d.confidence.endswith("+conflict") and "confirm against the device label" in d.note


def test_match_straddling_chunk_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(identify, "_CHUNK", 256)
    body = b"\x00" * 250 + b"DH-XVR5108HS-I3\x00" + b"\x00" * 600
    d = identify_device(_img(tmp_path, body))
    assert d.model == "DH-XVR5108HS-I3"
    assert len([e for e in d.evidence if e["kind"] == "model"]) == 1   # found once, not twice
