import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custody import GENESIS_HASH, CustodyLog


def _log(tmp_path):
    log = CustodyLog(str(tmp_path / "c.custody.jsonl"))
    log.append("evidence_received", "SI Sharma", {"filename": "dvr.img"})
    log.append("image_hashed", "engine", {"sha256": "ab" * 32})
    log.append("report_downloaded", "Inspector Rao", {"ip": "10.0.0.5"})
    return log


def test_chain_is_valid_and_linked(tmp_path):
    log = _log(tmp_path)
    e = log.entries()
    assert [x["seq"] for x in e] == [1, 2, 3]
    assert e[0]["prev_hash"] == GENESIS_HASH and e[1]["prev_hash"] == e[0]["entry_hash"]
    v = log.verify()
    assert v["valid"] and v["entry_count"] == 3 and v["head_hash"] == e[-1]["entry_hash"]


def _rewrite(log, fn):
    lines = open(log.path, encoding="utf-8").read().splitlines()
    lines = fn(lines)
    open(log.path, "w", encoding="utf-8").write("\n".join(lines) + "\n")


def test_editing_an_entry_is_detected(tmp_path):
    log = _log(tmp_path)

    def tamper(lines):
        e = json.loads(lines[1])
        e["details"]["sha256"] = "cd" * 32
        lines[1] = json.dumps(e)
        return lines
    _rewrite(log, tamper)
    v = log.verify()
    assert not v["valid"] and any("entry 2" in p and "modified" in p for p in v["problems"])


def test_deleting_an_entry_is_detected(tmp_path):
    log = _log(tmp_path)
    _rewrite(log, lambda lines: [lines[0], lines[2]])
    v = log.verify()
    assert not v["valid"] and any("prev_hash" in p for p in v["problems"])


def test_empty_log(tmp_path):
    v = CustodyLog(str(tmp_path / "none.jsonl")).verify()
    assert v["valid"] and v["entry_count"] == 0 and v["head_hash"] == GENESIS_HASH


def test_non_json_details_are_stored_as_hashed(tmp_path):
    from datetime import datetime
    log = CustodyLog(str(tmp_path / "d.jsonl"))
    log.append("x", "y", {"when": datetime(2026, 1, 1)})
    assert log.verify()["valid"]
