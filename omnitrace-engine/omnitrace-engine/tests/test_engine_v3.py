"""Generic fallback parser, report sections, custody wiring and the HTTP endpoints."""

import io
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from make_synthetic_images import make_dahua_image, quiet_bytes
from models import UnsupportedOEMError
from parsers.generic import GenericParser, looks_like_h264, looks_like_h265
from pipeline import process_image

SPS = b"\x00\x00\x00\x01\x67\x64\x00\x1f"   # SPS, profile_idc 100 (High)
PPS = b"\x00\x00\x00\x01\x68\xee\x3c\x80"


def make_unknown_vendor_image(path, seed=5):
    """A Uniview-labelled disk with no vendor file system we parse, but real-looking H.264 starts."""
    rng = random.Random(seed)
    with open(path, "wb") as f:
        f.write(b"\x00" * 1024 + b"UNIVIEW\x00NVR301-04S\x00V2.4.0 build 210101\x00")
        f.write(quiet_bytes(rng, 4096))
        for _ in range(2):
            f.write(quiet_bytes(rng, 3 * 1024 * 1024))
            f.write(SPS + PPS)
            for _ in range(8):
                f.write(b"\x00\x00\x01" + bytes([rng.choice([1, 5])]) + quiet_bytes(rng, rng.randint(500, 3000)))
        f.write(quiet_bytes(rng, 1024))


def test_generic_sniff_is_strict():
    assert looks_like_h264(SPS + PPS)
    assert not looks_like_h264(SPS)                                 # SPS without PPS
    assert not looks_like_h264(b"\x00\x00\x00\x01\x67\x05" + PPS)   # 5 is not a real profile_idc
    assert looks_like_h265(b"\x00\x00\x00\x01\x40\x01..\x00\x00\x00\x01\x42\x01..\x00\x00\x00\x01\x44\x01")
    assert not GenericParser().sniff(os.urandom(1 << 16))


def test_unknown_vendor_routes_to_generic_and_is_identified(tmp_path):
    img = str(tmp_path / "unv.img")
    make_unknown_vendor_image(img)
    r = process_image(img, str(tmp_path / "out"))
    assert r.oem == "Generic" and r.oem_confidence == "none"
    assert r.device["vendor"] == "Uniview" and r.device["model"] == "NVR301-04S"
    assert r.device["firmware"] == "V2.4.0 build 210101"
    assert len(r.hashed_clips) >= 2
    rep = json.load(open(r.json_report_path))
    for c in rep["clips"]:
        assert c["export"]["raw_sha256_matches_clip"] is True         # exported .es == hashed bytes


def test_force_generic_on_unrecognised_image(tmp_path):
    junk = tmp_path / "junk.img"
    junk.write_bytes(quiet_bytes(random.Random(1), 8192))
    with pytest.raises(UnsupportedOEMError):
        process_image(str(junk), str(tmp_path / "o"))
    r = process_image(str(junk), str(tmp_path / "o2"), force_oem="generic")
    assert r.oem == "Generic" and r.oem_confidence == "forced by examiner" and r.hashed_clips == []
    with pytest.raises(ValueError):
        process_image(str(junk), str(tmp_path / "o3"), force_oem="Sony")


def test_report_sections_and_custody(tmp_path):
    img = str(tmp_path / "d.img")
    make_dahua_image(img, n_chunks=4)
    r = process_image(img, str(tmp_path / "out"), dvr_tz="+05:30", examiner="SI Sharma",
                      clock_offset_seconds=60, case_meta={"case_ref": "FIR-42/2026"})
    rep = json.load(open(r.json_report_path))
    assert rep["integrity"]["verified_after_processing"] is True
    assert rep["device"]["format_family"] == "Dahua"
    assert rep["timeline"]["timezone_normalized"] and rep["timeline"]["clock_offset_seconds"] == 60
    assert rep["timeline"]["timed_clip_count"] == 4
    assert rep["custody"]["verification"]["valid"]
    actions = [e["action"] for e in rep["custody"]["entries"]]
    assert actions[0] == "evidence_received" and "integrity_verified" in actions
    assert rep["custody"]["entries"][0]["actor"] == "SI Sharma"
    from custody import CustodyLog
    log = CustodyLog(r.custody_path)
    assert log.entries()[-1]["action"] == "report_generated" and log.verify()["valid"]
    assert rep["case_meta"]["case_ref"] == "FIR-42/2026"
    assert open(r.pdf_report_path, "rb").read(4) == b"%PDF"


def test_failure_is_recorded_in_custody(tmp_path):
    junk = tmp_path / "junk.img"
    junk.write_bytes(b"\x01" * 4096)
    with pytest.raises(UnsupportedOEMError):
        process_image(str(junk), str(tmp_path / "o"), case_id="failcase")
    from custody import CustodyLog
    e = CustodyLog(str(tmp_path / "o" / "failcase.custody.jsonl")).entries()
    assert e[-1]["action"] == "processing_failed" and "UnsupportedOEMError" in e[-1]["details"]["error"]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "DATA_DIR", str(tmp_path))
    return app_module.app.test_client()


def test_http_process_custody_and_capabilities(client, tmp_path):
    img = str(tmp_path / "d.img")
    make_dahua_image(img, n_chunks=2)
    caps = client.get("/capabilities").get_json()
    assert {p["name"] for p in caps["parsers"]} == {"Dahua", "Hikvision", "Generic"}
    assert {"CP Plus", "Uniview", "Matrix", "TP-Link", "Godrej", "Honeywell"} <= set(caps["vendors_identified"])

    with open(img, "rb") as f:
        resp = client.post("/process", data={"image": (io.BytesIO(f.read()), "d.img"), "dvr_tz": "+05:30",
                                             "examiner": "SI Sharma", "case_ref": "FIR-7"},
                           content_type="multipart/form-data")
    body = resp.get_json()
    assert resp.status_code == 200 and body["clip_count"] == 2 and body["integrity_ok"] is True
    cid = body["case_id"]

    cust = client.get(f"/custody/{cid}").get_json()
    assert cust["verification"]["valid"]
    n = cust["verification"]["entry_count"]
    added = client.post(f"/custody/{cid}", json={"actor": "Inspector Rao", "action": "report_downloaded"})
    assert added.status_code == 201
    assert client.get(f"/custody/{cid}").get_json()["verification"]["entry_count"] == n + 1
    assert client.post(f"/custody/{cid}", json={"action": "x"}).status_code == 400
    assert client.get("/custody/does-not-exist").status_code == 404
    assert client.get("/custody/..%2f..%2fetc").status_code == 404

    bad = client.post("/process", data={"image": (io.BytesIO(b"x"), "d.img"), "clock_offset_seconds": "abc"},
                      content_type="multipart/form-data")
    assert bad.status_code == 400
