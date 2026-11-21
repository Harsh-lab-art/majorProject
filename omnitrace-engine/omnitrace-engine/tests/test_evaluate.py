"""
These tests check the harness MATH against a truth built from what the generator really wrote.
They do NOT measure engine accuracy on real footage - that needs Manish's real image.
"""
import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import pytest

from convert_truth import convert
from evaluate import evaluate
from make_synthetic_images import make_dahua_image, make_hikvision_image
from pipeline import process_image


def iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def truth_from(written, kind="deleted", span=30, shift=0):
    return {"clips": [{"id": f"c{i}", "channel": w["channel"], "start": iso(w["ts"] - shift),
                       "end": iso(w["ts"] - shift + span), "kind": kind} for i, w in enumerate(written)]}


def test_report_without_dvr_tz_is_refused_by_the_harness(tmp_path):
    img = str(tmp_path / "d.img")
    written = make_dahua_image(img, n_chunks=2)
    report = json.load(open(process_image(img, str(tmp_path / "o")).json_report_path))
    assert "NO offset" in report["timestamps_note"]
    with pytest.raises(ValueError, match="no timezone"):
        evaluate(report, truth_from(written))
    # ...but the harness can be told the offset explicitly
    assert evaluate(report, truth_from(written), dvr_tz="+00:00")["recovery_rate"] == 1.0


def test_dvr_tz_offset_is_applied_so_a_wrong_offset_breaks_temporal_accuracy(tmp_path):
    img = str(tmp_path / "d.img")
    written = make_dahua_image(img, n_chunks=3)
    res = process_image(img, str(tmp_path / "o"), dvr_tz="+05:30")
    report = json.load(open(res.json_report_path))
    assert report["clips"][0]["start_ts"].endswith("+05:30")
    # truth says the clock was UTC; engine was told +05:30 -> 5.5 h apart -> nothing matches
    assert evaluate(report, truth_from(written))["recovery_rate"] == 0.0


@pytest.fixture()
def dahua(tmp_path):
    img = str(tmp_path / "d.img")
    written = make_dahua_image(img, n_chunks=5)
    res = process_image(img, str(tmp_path / "out"), dvr_tz="+00:00")
    return written, json.load(open(res.json_report_path)), res


def test_perfect_recovery(dahua):
    written, report, _ = dahua
    r = evaluate(report, truth_from(written))
    assert (r["recovery_rate"], r["temporal_accuracy"], r["false_positive_rate"]) == (1.0, 1.0, 0.0)


def test_missed_clip_lowers_recovery_rate(dahua):
    written, report, _ = dahua
    t = truth_from(written)
    t["clips"].append({"id": "ghost", "channel": 0, "start": iso(written[0]["ts"] + 7200),
                       "end": iso(written[0]["ts"] + 7230), "kind": "deleted"})
    r = evaluate(report, t)
    assert r["ground_truth_deleted"] == 6 and r["recovered_deleted"] == 5
    assert r["recovery_rate"] == pytest.approx(5 / 6)
    assert [d["id"] for d in r["per_truth_clip"] if not d["recovered"]] == ["ghost"]


def test_clip_not_in_truth_is_a_false_positive(dahua):
    written, report, _ = dahua
    r = evaluate(report, truth_from(written[:4]))
    assert r["false_positives"] == 1 and r["false_positive_rate"] == pytest.approx(1 / 5)


def test_live_clips_are_not_false_positives_and_not_counted_as_deleted(dahua):
    written, report, _ = dahua
    t = truth_from(written[:3], "deleted")
    t["clips"] += truth_from(written[3:], "live")["clips"]
    r = evaluate(report, t)
    assert r["ground_truth_deleted"] == 3 and r["false_positives"] == 0 and r["recovery_rate"] == 1.0


def test_wrong_timestamp_is_found_but_not_temporally_accurate(dahua):
    written, report, _ = dahua
    # truth says clips began 10 s earlier than the recovered timestamp: still inside the window, but 10 s off
    r = evaluate(report, truth_from(written, span=60, shift=10), tolerance_s=2)
    assert r["recovery_rate"] == 1.0 and r["temporal_accuracy"] == 0.0
    assert r["mean_abs_start_error_s"] == pytest.approx(10)


def test_wrong_channel_does_not_match(dahua):
    written, report, _ = dahua
    t = truth_from(written)
    for c in t["clips"]:
        c["channel"] = 9
    assert evaluate(report, t)["recovery_rate"] == 0.0


def test_untimestamped_clips_are_reported_not_hidden(tmp_path):
    img = str(tmp_path / "h.img")
    make_hikvision_image(img, n_clusters=3)
    res = process_image(img, str(tmp_path / "out"))
    report = json.load(open(res.json_report_path))
    t = {"clips": [{"id": "x", "channel": 1, "start": iso(0), "end": iso(60), "kind": "deleted"}]}
    r = evaluate(report, t)
    assert r["unverifiable_no_timestamp"] == len(report["clips"]) and r["false_positives"] == 0
    assert r["recovery_rate"] == 0.0


def test_exact_hash_match_works_without_timestamps(tmp_path):
    img = str(tmp_path / "h.img")
    make_hikvision_image(img, n_clusters=3)
    report = json.load(open(process_image(img, str(tmp_path / "out")).json_report_path))
    t = {"clips": [{"id": "x", "start": iso(0), "end": iso(60), "kind": "deleted", "sha256": report["clips"][0]["sha256"]}]}
    r = evaluate(report, t)
    assert r["recovery_rate"] == 1.0 and r["per_truth_clip"][0]["match"] == "exact-hash"


def test_naive_timestamps_are_refused(dahua):
    written, report, _ = dahua
    t = truth_from(written)
    t["clips"][0]["start"] = "2026-11-20T10:00:00"
    with pytest.raises(ValueError, match="no timezone"):
        evaluate(report, t)


def test_convert_truth_applies_timezone_and_validates(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("id,channel,start,end,kind,sha256\nA,1,2026-11-20 10:00:00,2026-11-20 10:10:00,deleted,\n")
    out = convert(str(p), "+05:30")
    assert out["clips"][0]["start"] == "2026-11-20T10:00:00+05:30" and out["clips"][0]["channel"] == 1
    bad = tmp_path / "b.csv"
    bad.write_text("id,channel,start,end,kind,sha256\nA,1,20/11/2026 10:00,2026-11-20 10:10:00,deleted,\n")
    with pytest.raises(SystemExit):
        convert(str(bad), "+05:30")
    with pytest.raises(SystemExit):
        convert(str(p), "IST")


def test_cli_end_to_end(dahua, tmp_path):
    written, _, res = dahua
    tpath = tmp_path / "truth.json"
    tpath.write_text(json.dumps(truth_from(written)))
    root = os.path.join(os.path.dirname(__file__), "..")
    out = subprocess.run([sys.executable, "evaluate.py", "--report", res.json_report_path, "--truth", str(tpath)],
                         cwd=root, capture_output=True, text=True)
    assert out.returncode == 0 and "100.0%" in out.stdout
