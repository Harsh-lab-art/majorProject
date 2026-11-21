"""
Independent validation: do FFmpeg's own DHAV reader and H.264 decoder agree with what our parser
reported and exported? Skipped automatically where ffmpeg (with the dhav demuxer) is not installed.
"""
import hashlib
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from make_real_dhav import have_ffmpeg, make_real_dhav_image
from pipeline import process_image

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg with the dhav demuxer is not installed")


@pytest.fixture(scope="module")
def case(tmp_path_factory):
    d = tmp_path_factory.mktemp("real")
    img = str(d / "real.img")
    truth = make_real_dhav_image(img, str(d), channels=2, seconds=4, fps=10)
    res = process_image(img, str(d / "out"), dvr_tz="+00:00")
    report = json.load(open(res.json_report_path))
    return d, img, truth, res, report


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-count_packets", "-show_streams", "-show_format",
                          "-of", "json", path], capture_output=True, text=True)
    return json.loads(out.stdout)


def test_one_segment_per_channel_with_all_frames_and_key_frames(case):
    _, _, truth, res, report = case
    assert len(res.hashed_clips) == 2
    for t, hc in zip(truth, sorted(res.hashed_clips, key=lambda h: h.clip.channel)):
        assert hc.clip.channel == t["channel"] and hc.clip.frame_count == t["frames"] == 40
    st = report["parser_stats"]
    assert st["video_frames"] == 80 and st["rejected_no_footer"] == 0 and st["rejected_footer_size_mismatch"] == 0


def test_exported_dav_hash_equals_reported_clip_hash(case):
    d, _, _, res, report = case
    for c in report["clips"]:
        assert c["export"]["dav_sha256_matches_clip"] is True
        path = str(d / "out" / c["export"]["dav"])
        assert hashlib.sha256(open(path, "rb").read()).hexdigest() == c["sha256"]


def test_ffmpeg_reads_the_exported_clip_as_h264_with_our_frame_count(case):
    d, _, truth, _, report = case
    for c in report["clips"]:
        info = probe(str(d / "out" / c["export"]["dav"]))
        assert info["format"]["format_name"] == "dhav"
        v = [s for s in info["streams"] if s["codec_type"] == "video"][0]
        assert v["codec_name"] == "h264" and (v["width"], v["height"]) == (320, 240)
        assert int(v["nb_read_packets"]) == c["frame_count"] == 40   # FFmpeg sees the same number of DHAV packets
        assert int(v["nb_read_frames"]) == 40                       # (weak on its own: the H.264 parser re-splits)


def test_ffmpeg_decodes_every_frame_without_errors(case):
    d, _, _, _, report = case
    for c in report["clips"]:
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(d / "out" / c["export"]["dav"]), "-f", "null", "-"],
                           capture_output=True, text=True)
        assert r.returncode == 0 and r.stderr.strip() == ""


def _packet_times(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "packet=pts_time",
                          "-of", "csv=p=0", path], capture_output=True, text=True).stdout.split()
    return [float(x) for x in out]


def test_ffmpeg_timestamps_agree_with_our_decoded_dates(case):
    """FFmpeg derives pts from the same bit-packed date; its first/last packet must match our start/end.
    This is the check that actually discriminates the field layout (see the negative control below)."""
    from datetime import datetime, timezone
    d, _, _, _, report = case
    for c in report["clips"]:
        times = _packet_times(str(d / "out" / c["export"]["dav"]))
        start = datetime.fromisoformat(c["start_ts"]).astimezone(timezone.utc).timestamp()
        end = datetime.fromisoformat(c["end_ts"]).astimezone(timezone.utc).timestamp()
        assert abs(times[0] - start) < 0.01
        assert 0 <= times[-1] - end < 1.0          # ours is whole seconds; FFmpeg adds the sub-second part


def test_negative_control_the_old_wrong_layout_is_caught_by_the_timestamp_check(tmp_path):
    """Same H.264, but written with the layout my FIRST parser guessed (date before length).
    FFmpeg still finds 'DHAV' and splits the stream, so packet counts look fine - only the timestamps expose it."""
    import struct
    from datetime import datetime, timezone
    from make_real_dhav import h264_access_units
    from parsers.dahua import encode_date
    au = h264_access_units(str(tmp_path / "a.h264"), "testsrc", 2, 10)
    blob = b""
    for k, (key, p) in enumerate(au):
        ext = bytes([0x81, 0, 8, 10, 0x80, 0, 40, 30]) if key else b""
        dt = encode_date(datetime(2026, 11, 20, 10, 0, k // 10))
        head = struct.pack("<4sBBBBIII", b"DHAV", 0xFD if key else 0xFC, 0, 0, 0, dt, len(p), 1000 + k)   # WRONG order
        blob += head + struct.pack("<HBB", 0, len(ext), 0) + ext + p + b"dhav" + struct.pack("<I", 24 + len(ext) + len(p) + 8)
    path = str(tmp_path / "wrong.dav")
    open(path, "wb").write(blob)
    expected = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc).timestamp()
    assert abs(_packet_times(path)[0] - expected) > 1000      # wildly off: the check has teeth


def test_mp4_remux_plays_back_with_the_same_frame_count(case):
    d, _, _, _, report = case
    for c in report["clips"]:
        assert c["export"]["mp4"], c["export"]["mp4_error"]
        info = probe(str(d / "out" / c["export"]["mp4"]))
        v = [s for s in info["streams"] if s["codec_type"] == "video"][0]
        assert v["codec_name"] == "h264" and int(v["nb_read_frames"]) == 40
