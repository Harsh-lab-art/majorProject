"""
Device Identification module (problem statement: "automatically identify DVR models").

Two separate questions, answered separately on purpose:

  1. FORMAT FAMILY: which on-disk file system / container is this? Decided by published
     file-system signatures (DHFS4.1 -> Dahua family, HIKVISION@HANGZHOU -> Hikvision family).
     This is what routes the image to a parser plugin (see detector.py).
  2. VENDOR / MODEL / FIRMWARE: who made the box? Decided by vendor model-number patterns and
     brand strings found in the system area of the disk (the same idea as running `strings`).

They differ in practice: many CP Plus (and other OEM-rebadged) recorders are built on Dahua
platforms and write a Dahua-family file system, so "Dahua format" does not mean "Dahua device".

HONESTY NOTES
  * Model-number patterns are the vendors' public catalogue naming schemes (e.g. Hikvision
    "DS-7608NI-...", Dahua "DH-XVR...", CP Plus "CP-UVR-...", TP-Link "VIGI NVR1004H",
    Uniview "NVR301-04S"). They are NOT a documented on-disk field: we only report where in the
    image we saw them, and an examiner should confirm against the device label.
  * Brand-only hits (e.g. the word "Honeywell") are weak evidence and are labelled "weak".
  * Every hit is returned with its byte offset as evidence, so the result can be re-checked
    with any hex editor. Nothing is inferred silently.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

IDENT_WINDOW_BYTES = 64 * 1024 * 1024   # system areas live near the start of the disk
_CHUNK = 4 * 1024 * 1024
_OVERLAP = 128                           # longer than any pattern, so boundary matches are found
_MAX_EVIDENCE = 40

# Published file-system signatures -> format family (and the vendor that family implies).
_FAMILY_RULES = [
    ("Dahua", re.compile(rb"DHFS4\.1")),
    ("Hikvision", re.compile(rb"HIKVISION@HANGZHOU")),
]

_B = rb"(?<![A-Za-z0-9])"   # left word boundary that works on raw bytes

# (vendor, kind, weight, pattern). kind is "model" (weight 2) or "brand" (weight 1).
_VENDOR_RULES = [
    ("Hikvision", "model", 2, _B + rb"i?DS-[6-9][0-9]{3}[A-Z0-9][A-Z0-9\-/]{1,24}"),
    ("Hikvision", "brand", 1, rb"(?i)hikvision"),
    ("Dahua", "model", 2, _B + rb"DHI?-(?:XVR|NVR|HCVR)[0-9][A-Z0-9\-/]{1,24}"),
    ("Dahua", "brand", 1, rb"(?i)dahua"),
    ("CP Plus", "model", 2, _B + rb"CP-(?:UVR|UNR|VNR|XVR|DVR)-?[0-9A-Z][A-Z0-9\-/]{1,24}"),
    ("CP Plus", "brand", 1, rb"(?i)cp[ \-]?plus"),
    ("Uniview", "model", 2, _B + rb"NVR30[1-8]-[0-9]{2}[A-Z0-9\-]{0,12}"),
    ("Uniview", "brand", 1, rb"(?i)uniview"),
    ("TP-Link", "model", 2, _B + rb"VIGI[ _]?NVR[0-9]{4}[A-Z0-9\-]{0,8}"),
    ("TP-Link", "brand", 1, rb"(?i)tp-?link"),
    ("Matrix", "model", 2, rb"(?i)satatya[ _\-]?[A-Z0-9\-]{0,16}"),
    ("Matrix", "brand", 1, rb"(?i)matrix[ _]comsec"),
    ("Honeywell", "brand", 1, rb"(?i)honeywell"),
    ("Godrej", "brand", 1, rb"(?i)godrej"),
]
_FIRMWARE = re.compile(rb"(?<![A-Za-z0-9.])V[0-9]{1,2}\.[0-9]{1,3}\.[0-9]{1,4}(?:[ ._]?build[ ._]?[0-9]{6})?")

_COMPILED = [(v, k, w, re.compile(p)) for v, k, w, p in _VENDOR_RULES]


@dataclass
class DeviceIdentification:
    vendor: Optional[str]
    confidence: str              # strong | weak | inferred_from_format | none ; "+conflict" if tied
    model: Optional[str]
    firmware: Optional[str]
    format_family: Optional[str]
    scores: Dict[str, int] = field(default_factory=dict)
    evidence: List[dict] = field(default_factory=list)
    bytes_scanned: int = 0
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _scan(image_path: str, window: int):
    """Yield (absolute_offset, rule_tuple_or_tag, matched_bytes) over the first `window` bytes."""
    with open(image_path, "rb") as f:
        pos, carry = 0, b""
        while pos < window:
            data = f.read(min(_CHUNK, window - pos))
            if not data:
                break
            buf, base = carry + data, pos - len(carry)
            pos += len(data)
            for fam, rx in _FAMILY_RULES:
                for m in rx.finditer(buf):
                    yield base + m.start(), ("family", fam), m.group()
            for rule in _COMPILED:
                for m in rule[3].finditer(buf):
                    yield base + m.start(), rule, m.group()
            for m in _FIRMWARE.finditer(buf):
                yield base + m.start(), ("firmware",), m.group()
            carry = buf[-_OVERLAP:]
        yield pos, None, b""   # sentinel carrying bytes scanned


def identify_device(image_path: str, window: int = IDENT_WINDOW_BYTES) -> DeviceIdentification:
    families: Counter = Counter()
    per_vendor_kind: Dict[str, Dict[str, int]] = {}
    models: Dict[str, Counter] = {}
    firmware: Counter = Counter()
    evidence: List[dict] = []
    seen = set()
    scanned = 0

    for offset, rule, raw in _scan(image_path, window):
        if rule is None:
            scanned = offset
            continue
        key = (offset, raw)
        if key in seen:          # the overlap region is scanned twice
            continue
        seen.add(key)
        text = raw.decode("ascii", "replace").strip()
        if rule[0] == "family":
            families[rule[1]] += 1
            kind, vendor = "format_signature", rule[1]
        elif rule[0] == "firmware":
            firmware[text] += 1
            kind, vendor = "firmware", None
        else:
            vendor, kind, weight, _ = rule
            kinds = per_vendor_kind.setdefault(vendor, {})
            kinds[kind] = max(kinds.get(kind, 0), weight)
            if kind == "model":
                models.setdefault(vendor, Counter())[text] += 1
        if len(evidence) < _MAX_EVIDENCE:
            evidence.append({"offset": offset, "kind": kind, "vendor": vendor, "match": text})

    scores = {v: sum(k.values()) for v, k in per_vendor_kind.items()}
    family = families.most_common(1)[0][0] if families else None
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))

    vendor, confidence, note = None, "none", ""
    if ranked:
        vendor, top = ranked[0]
        agrees_with_format = family == vendor
        confidence = "strong" if top >= 3 or (agrees_with_format and top >= 1) else "weak"
        if len(ranked) > 1 and ranked[1][1] == top:
            confidence += "+conflict"
            note = f"tied vendor evidence: {[v for v, s in ranked if s == top]}; confirm against the device label"
        if family and family != vendor:
            note = (note + "; " if note else "") + (
                f"{vendor} device strings on a {family}-family file system (common for OEM-rebadged units)")
    elif family:
        vendor, confidence = family, "inferred_from_format"
        note = "no vendor/model strings found; vendor inferred from the file-system signature only"
    else:
        note = "no vendor, model or file-system signature found in the scanned window"

    model = models[vendor].most_common(1)[0][0] if vendor in models else None
    fw = firmware.most_common(1)[0][0] if firmware else None
    return DeviceIdentification(vendor=vendor, confidence=confidence, model=model, firmware=fw,
                                format_family=family, scores=scores, evidence=evidence,
                                bytes_scanned=scanned, note=note)
