"""
Chain of Custody — tamper-evident, append-only log per case.

Each entry records WHO did WHAT and WHEN (UTC), plus the SHA-256 of the previous entry.
The entry's own hash covers all of that, so the entries form a hash chain (the same idea as a
blockchain or git history): editing, deleting or re-ordering any past entry breaks every hash
after it, and `verify()` reports exactly where.

What this does NOT protect against: someone deleting the whole file, or rewriting the entire
chain from the start. For that, the chain HEAD hash is printed in the PDF report and can be
written into the case diary / FIR; anyone can later check the log still ends at that hash.

File format: JSON Lines (<case_id>.custody.jsonl), one entry per line, human-readable.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from typing import List, Optional

GENESIS_HASH = "0" * 64
_LOCK = threading.Lock()


def _canonical(entry: dict) -> bytes:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _entry_hash(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "entry_hash"}
    return hashlib.sha256(_canonical(body)).hexdigest()


class CustodyLog:
    def __init__(self, path: str):
        self.path = path

    def entries(self) -> List[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def head_hash(self) -> str:
        e = self.entries()
        return e[-1]["entry_hash"] if e else GENESIS_HASH

    def append(self, action: str, actor: Optional[str] = None, details: Optional[dict] = None) -> dict:
        with _LOCK:
            existing = self.entries()
            entry = {
                "seq": len(existing) + 1,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "actor": actor or "system",
                "action": action,
                "details": details or {},
                "prev_hash": existing[-1]["entry_hash"] if existing else GENESIS_HASH,
            }
            entry = json.loads(json.dumps(entry, default=str))   # what we hash == what we store
            entry["entry_hash"] = _entry_hash(entry)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, sort_keys=True) + "\n")
            return entry

    def verify(self) -> dict:
        problems, prev = [], GENESIS_HASH
        entries = self.entries()
        for i, e in enumerate(entries, start=1):
            if e.get("seq") != i:
                problems.append(f"entry {i}: sequence number is {e.get('seq')}, expected {i}")
            if e.get("prev_hash") != prev:
                problems.append(f"entry {i}: prev_hash does not match entry {i - 1}")
            if _entry_hash(e) != e.get("entry_hash"):
                problems.append(f"entry {i}: contents were modified after it was written")
            prev = e.get("entry_hash")
        return {"valid": not problems, "entry_count": len(entries),
                "head_hash": prev if entries else GENESIS_HASH, "problems": problems}
