import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from analytics import analyze_video


def _video(path, frames=60, fps=10, move=range(20, 40)):
    """Grey scene with a white square that is still, then moves during `move`, then still again."""
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), fps, (320, 240))
    assert w.isOpened()
    x = 20
    for i in range(frames):
        if i in move:
            x += 8
        img = np.full((240, 320, 3), 90, np.uint8)
        cv2.rectangle(img, (x, 100), (x + 40, 140), (255, 255, 255), -1)
        w.write(img)
    w.release()
    return path


def test_motion_event_found_in_the_right_place(tmp_path):
    p = _video(str(tmp_path / "v.avi"))
    res = analyze_video(p, sample_fps=5, thumb_dir=str(tmp_path / "th"), thumb_prefix="clip1")
    assert "error" not in res
    assert res["frames_read"] == 60 and res["fps"] == 10
    ev = res["motion_events"]
    assert len(ev) == 1
    assert 1.6 <= ev[0]["start_s"] <= 2.4 and 3.6 <= ev[0]["end_s"] <= 4.4   # movement is frames 20-39
    assert res["thumbnails"] == ["clip1_motion0.jpg"]
    assert os.path.exists(tmp_path / "th" / "clip1_motion0.jpg")
    assert res["objects"]["status"].startswith("not configured")


def test_static_scene_has_no_motion(tmp_path):
    p = _video(str(tmp_path / "s.avi"), move=range(0))
    res = analyze_video(p, sample_fps=5)
    assert res["motion_events"] == [] and res["faces"]["frames_with_faces"] == 0


def test_undecodable_file_reports_error_not_crash(tmp_path):
    p = tmp_path / "junk.dav"
    p.write_bytes(os.urandom(5000))
    assert "error" in analyze_video(str(p))


def test_pipeline_with_analytics_on_undecodable_synthetic_clips(tmp_path):
    from make_synthetic_images import make_dahua_image
    from pipeline import process_image
    img = str(tmp_path / "d.img")
    make_dahua_image(img, n_chunks=2)
    r = process_image(img, str(tmp_path / "out"), run_analytics=True)
    import json
    rep = json.load(open(r.json_report_path))
    assert all(c["analytics"] and "error" in c["analytics"] for c in rep["clips"])   # random payloads: honest error
    assert os.path.getsize(r.pdf_report_path) > 0
