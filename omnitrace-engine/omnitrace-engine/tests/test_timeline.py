import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hasher import HashedClip
from models import ClipEntry, ClipStatus
from timeline import build_timeline

IST = timezone(timedelta(hours=5, minutes=30))
T0 = datetime(2026, 3, 1, 10, 0, 0, tzinfo=IST)


def hc(cid, ch, start_s, end_s, tz=True):
    s, e = T0 + timedelta(seconds=start_s), T0 + timedelta(seconds=end_s)
    if not tz:
        s, e = s.replace(tzinfo=None), e.replace(tzinfo=None)
    return HashedClip(ClipEntry(cid, 0, 10, start_ts=s, end_ts=e, channel=ch, status=ClipStatus.CARVED), "m", "s")


def test_utc_normalisation_and_clock_offset():
    tl = build_timeline([hc("a", 1, 0, 10)], clock_offset_seconds=300)     # DVR 5 min fast
    assert tl["timezone_normalized"] and tl["time_basis"] == "UTC"
    ev = tl["events"][0]
    assert ev["start"] == "2026-03-01T04:25:00+00:00"                     # 10:00 IST - 5 min -> 04:25 UTC
    assert ev["raw_start_dvr_clock"] == "2026-03-01T10:00:00+05:30"       # original kept


def test_naive_times_are_flagged_not_converted():
    tl = build_timeline([hc("a", 1, 0, 10, tz=False)])
    assert not tl["timezone_normalized"] and "no dvr_tz" in tl["time_basis"]


def test_gaps_per_channel():
    clips = [hc("a", 1, 0, 60), hc("b", 1, 70, 120), hc("c", 1, 600, 660), hc("d", 2, 0, 660)]
    tl = build_timeline(clips, gap_threshold_seconds=60)
    assert len(tl["gaps"]) == 1
    g = tl["gaps"][0]
    assert g["channel"] == 1 and g["duration_s"] == 480
    assert tl["channels"]["1"]["gap_count"] == 1 and tl["channels"]["2"]["gap_count"] == 0


def test_concurrent_windows_need_two_cameras():
    tl = build_timeline([hc("a", 1, 0, 30), hc("b", 2, 20, 50), hc("c", 3, 500, 510)])
    w = tl["concurrent_recording_windows"]
    assert len(w) == 1 and w[0]["channels"] == [1, 2] and w[0]["duration_s"] == 50


def test_motion_correlated_across_cameras_within_tolerance():
    clips = [hc("a", 1, 0, 120), hc("b", 2, 0, 120), hc("c", 3, 0, 120)]
    analytics = {
        "a": {"motion_events": [{"start_s": 10, "end_s": 14}]},
        "b": {"motion_events": [{"start_s": 17, "end_s": 20}]},     # 3 s after camera 1: same incident
        "c": {"motion_events": [{"start_s": 90, "end_s": 92}]},     # alone: not correlated
    }
    tl = build_timeline(clips, analytics=analytics, correlation_tolerance_seconds=5)
    assert len(tl["motion_events"]) == 3
    inc = tl["correlated_motion_incidents"]
    assert len(inc) == 1 and inc[0]["channels"] == [1, 2] and inc[0]["duration_s"] == 10


def test_untimed_clips_counted():
    untimed = HashedClip(ClipEntry("x", 0, 10, status=ClipStatus.CARVED), "m", "s")
    tl = build_timeline([untimed, hc("a", 1, 0, 5)])
    assert tl["untimed_clip_count"] == 1 and tl["timed_clip_count"] == 1
