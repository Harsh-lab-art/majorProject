"""
Report Writer — Section 7.2 (step 6) of the design doc.

Produces two artifacts per case, both built from the same data so they can never disagree:
  - JSON report: what the Node API stores/returns and the React dashboard renders.
    (All keys the API already reads are unchanged; new sections are additive.)
  - PDF report: the human / court-readable document (fpdf2, pure Python):
      1 Case summary  2 Evidence integrity  3 Device identification  4 Recovered clips
      5 Timeline (drawn chart, gaps, correlated incidents)  6 Video analytics (+ thumbnails)
      7 Chain of custody  8 Method and limitations
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from hasher import HashedClip

MAX_PDF_CLIP_ROWS = 300


def build_json_report(case_id: str, oem: str, oem_confidence: str, image_hash: tuple, hashed_clips: List[HashedClip],
                      dvr_tz: str | None = None, parser_stats: dict | None = None,
                      exports: dict | None = None, export_note: str | None = None, device: dict | None = None,
                      integrity: dict | None = None, timeline: dict | None = None, analytics: dict | None = None,
                      examiner: str | None = None, case_meta: dict | None = None, engine_version: str | None = None,
                      custody_entries: list | None = None, custody_verification: dict | None = None) -> dict:
    exports, analytics = exports or {}, analytics or {}
    return {
        "dvr_tz": dvr_tz,
        "timestamps_note": ("Clip timestamps are the DVR's own local clock" + (f" with the supplied offset {dvr_tz}." if dvr_tz else "; NO offset was supplied, so they carry no timezone.")),
        "parser_stats": parser_stats or {},
        "export_note": export_note,
        "case_id": case_id,
        "oem": oem,
        "oem_detection_confidence": oem_confidence,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine_version": engine_version,
        "examiner": examiner,
        "case_meta": case_meta or {},
        "image_md5": image_hash[0],
        "image_sha256": image_hash[1],
        "integrity": integrity or {},
        "device": device or {},
        "clip_count": len(hashed_clips),
        "clips": [
            {
                **hc.clip.to_dict(),
                "md5": hc.md5,
                "sha256": hc.sha256,
                "export": exports.get(hc.clip.clip_id),
                "analytics": analytics.get(hc.clip.clip_id),
            }
            for hc in hashed_clips
        ],
        "timeline": timeline or {},
        "custody": {"entries": custody_entries or [], "verification": custody_verification or {},
                    "note": "Entries after report generation (e.g. 'report_generated', downloads) are in the "
                            "case's .custody.jsonl file; verify it with GET /custody/<case_id>."},
    }


def write_json_report(report: dict, out_path: str) -> None:
    Path(out_path).write_text(json.dumps(report, indent=2, default=str))


# ---------------------------------------------------------------- PDF helpers

def _t(x) -> str:
    """fpdf core fonts are latin-1 only: never let a stray character crash the report."""
    return str(x).encode("latin-1", "replace").decode("latin-1")


def _parse_iso(s):
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


class _PDF(FPDF):
    case_id = ""

    def header(self):
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(110)
        self.cell(0, 5, _t(f"OmniTrace Forensic Report  |  Case {self.case_id}"), align="R",
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(0)
        self.ln(1)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(110)
        self.cell(0, 8, f"Page {self.page_no()}/{{nb}}", align="C")
        self.set_text_color(0)

    def h1(self, text):
        if self.get_y() > self.h - 50:
            self.add_page()
        self.ln(3)
        self.set_font("Helvetica", "B", 13)
        self.set_fill_color(230, 236, 245)
        self.cell(0, 8, _t(text), fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(1)

    def kv(self, key, value, mono=False):
        self.set_font("Helvetica", "B", 9)
        self.cell(48, 6, _t(key))
        self.set_font("Courier" if mono else "Helvetica", "", 8 if mono else 9)
        self.multi_cell(0, 6, _t(value if value not in (None, "") else "-"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def para(self, text, size=8):
        self.set_font("Helvetica", "", size)
        self.multi_cell(0, 4.5, _t(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def table(self, headers, widths, rows, size=7):
        self.set_font("Helvetica", "B", size + 1)
        self.set_fill_color(240, 240, 240)
        for h, w in zip(headers, widths):
            self.cell(w, 6, _t(h), border=1, fill=True)
        self.ln()
        self.set_font("Helvetica", "", size)
        for row in rows:
            if self.get_y() > self.h - 20:
                self.add_page()
            for v, w in zip(row, widths):
                txt = _t(v if v is not None else "-")
                while self.get_string_width(txt) > w - 1.5 and len(txt) > 1:
                    txt = txt[:-1]
                self.cell(w, 5, txt, border=1)
            self.ln()


def _draw_timeline(pdf: _PDF, tl: dict) -> None:
    events = [e for e in tl.get("events", []) if _parse_iso(e.get("start"))]
    if not events:
        pdf.para("No timed clips: nothing to draw.")
        return
    starts = [_parse_iso(e["start"]) for e in events]
    ends = [_parse_iso(e["end"]) or s for e, s in zip(events, starts)]
    t0, t1 = min(starts), max(ends)
    span = max((t1 - t0).total_seconds(), 1.0)
    channels = sorted({str(e["channel"]) for e in events})[:16]
    label_w, row_h = 22, 6
    x0, w = pdf.l_margin + label_w, pdf.epw - label_w
    height = row_h * len(channels) + 12
    if pdf.get_y() + height > pdf.h - 20:
        pdf.add_page()
    y0 = pdf.get_y()

    def x_of(t):
        return x0 + (t - t0).total_seconds() / span * w

    pdf.set_font("Helvetica", "", 7)
    for i, ch in enumerate(channels):
        y = y0 + i * row_h
        pdf.set_fill_color(247, 247, 247)
        pdf.rect(x0, y, w, row_h - 1, style="F")
        pdf.set_xy(pdf.l_margin, y)
        pdf.cell(label_w, row_h - 1, _t(f"Ch {ch}"))
    for gap in tl.get("gaps", []):
        if str(gap["channel"]) in channels:
            i = channels.index(str(gap["channel"]))
            a, b = _parse_iso(gap["from"]), _parse_iso(gap["to"])
            pdf.set_fill_color(250, 205, 205)
            pdf.rect(x_of(a), y0 + i * row_h, max(x_of(b) - x_of(a), 0.4), row_h - 1, style="F")
    for e, s, en in zip(events, starts, ends):
        if str(e["channel"]) not in channels:
            continue
        i = channels.index(str(e["channel"]))
        pdf.set_fill_color(46, 110, 190)
        pdf.rect(x_of(s), y0 + i * row_h + 1, max(x_of(en) - x_of(s), 0.6), row_h - 3, style="F")
    for m in tl.get("motion_events", []):
        if str(m["channel"]) in channels:
            i = channels.index(str(m["channel"]))
            pdf.set_fill_color(240, 150, 30)
            pdf.rect(x_of(_parse_iso(m["start"])), y0 + i * row_h + row_h - 2.5, 1.2, 1.5, style="F")
    y_axis = y0 + len(channels) * row_h + 1
    pdf.set_xy(x0, y_axis)
    pdf.cell(w / 2, 4, _t(t0.isoformat(sep=" ")))
    pdf.cell(w / 2, 4, _t(t1.isoformat(sep=" ")), align="R")
    pdf.set_xy(pdf.l_margin, y_axis + 5)
    pdf.set_font("Helvetica", "I", 7)
    pdf.cell(0, 4, "Blue = recovered footage   Red = recording gap   Orange = motion detected", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)


def write_pdf_report(report: dict, out_path: str, base_dir: Optional[str] = None) -> None:
    pdf = _PDF()
    pdf.case_id = report["case_id"]
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 11, "OmniTrace Forensic Report", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, "Multi-vendor DVR/NVR surveillance evidence analysis", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    integ, dev, tl = report.get("integrity") or {}, report.get("device") or {}, report.get("timeline") or {}
    meta = report.get("case_meta") or {}

    pdf.h1("1. Case summary")
    pdf.kv("Case ID", report["case_id"])
    for k, v in meta.items():
        pdf.kv(str(k).replace("_", " ").title(), v)
    pdf.kv("Examiner", report.get("examiner"))
    pdf.kv("Generated (UTC)", report["generated_at"])
    pdf.kv("Engine version", report.get("engine_version"))
    pdf.kv("Parser used", f"{report['oem']}  (confidence: {report['oem_detection_confidence']})")
    pdf.kv("Recovered clips", report["clip_count"])

    pdf.h1("2. Evidence integrity")
    pdf.kv("Image size (bytes)", integ.get("image_size"))
    pdf.kv("Image MD5", report["image_md5"], mono=True)
    pdf.kv("Image SHA-256", report["image_sha256"], mono=True)
    v = integ.get("verified_after_processing")
    pdf.kv("Unchanged by analysis", {True: "YES - re-hashed after processing, identical",
                                     False: "NO - IMAGE CHANGED DURING PROCESSING", None: "not checked"}[v])
    acq = integ.get("acquisition")
    if acq:
        rec = acq.get("record", {})
        pdf.kv("Acquisition record", "MATCHES image hash" if acq.get("hash_matches_acquisition") else "DOES NOT MATCH image hash")
        pdf.kv("Acquired from", rec.get("source"))
        pdf.kv("Acquired by / when", f"{rec.get('examiner')}  {rec.get('started_utc')}")
        pdf.kv("Bad sectors", rec.get("bad_sector_count"))
    else:
        pdf.kv("Acquisition record", "none supplied (image was not made with acquire.py)")

    pdf.h1("3. Device identification")
    pdf.kv("Vendor", f"{dev.get('vendor') or 'unknown'}  ({dev.get('confidence', '-')})")
    pdf.kv("Model", dev.get("model"))
    pdf.kv("Firmware", dev.get("firmware"))
    pdf.kv("File-system family", dev.get("format_family"))
    if dev.get("note"):
        pdf.para(dev["note"])
    ev = dev.get("evidence") or []
    if ev:
        pdf.table(["Offset", "Kind", "Vendor", "Matched text"], [28, 32, 28, pdf.epw - 88],
                  [[e["offset"], e["kind"], e.get("vendor"), e["match"]] for e in ev[:12]])

    pdf.h1("4. Recovered clips")
    pdf.para(report["timestamps_note"])
    if report.get("export_note"):
        pdf.para(report["export_note"])
    rows = []
    for c in report["clips"][:MAX_PDF_CLIP_ROWS]:
        exp = c.get("export") or {}
        ok = exp.get("dav_sha256_matches_clip", exp.get("raw_sha256_matches_clip"))
        rows.append([c["clip_id"], c.get("channel"), c["status"], c.get("frame_count"), c["length"],
                     (c.get("start_ts") or "unknown")[:19], (c.get("end_ts") or "")[:19], c["sha256"][:20] + "...",
                     {True: "yes", False: "NO", None: "-"}[ok]])
    pdf.table(["Clip ID", "Ch", "Status", "Frames", "Bytes", "Start (DVR clock)", "End", "SHA-256", "Export"],
              [26, 8, 14, 12, 18, 30, 30, 40, 12], rows)
    if report["clip_count"] > MAX_PDF_CLIP_ROWS:
        pdf.para(f"... {report['clip_count'] - MAX_PDF_CLIP_ROWS} more clips: full list with complete hashes in the JSON report.")

    pdf.h1("5. Timeline")
    pdf.kv("Time basis", tl.get("time_basis"))
    pdf.kv("Clock offset applied", f"{tl.get('clock_offset_seconds', 0)} s (DVR time minus true time)")
    pdf.kv("Span", f"{tl.get('earliest')}  ->  {tl.get('latest')}")
    pdf.kv("Timed / untimed clips", f"{tl.get('timed_clip_count', 0)} / {tl.get('untimed_clip_count', 0)}")
    _draw_timeline(pdf, tl)
    chans = tl.get("channels") or {}
    if chans:
        pdf.table(["Channel", "Clips", "First", "Last", "Recorded (s)", "Gaps"], [20, 15, 50, 50, 25, 15],
                  [[ch, s["clip_count"], s["first"][:19], s["last"][:19], s["recorded_seconds"], s["gap_count"]]
                   for ch, s in chans.items()])
    gaps = tl.get("gaps") or []
    if gaps:
        pdf.ln(2)
        pdf.para(f"Recording gaps longer than {tl.get('gap_threshold_seconds')} s (not proof of deletion: "
                 "the recorder may have been off or motion-triggered):")
        pdf.table(["Channel", "From", "To", "Duration (s)"], [20, 55, 55, 30],
                  [[g["channel"], g["from"][:19], g["to"][:19], g["duration_s"]] for g in gaps[:40]])
    inc = tl.get("correlated_motion_incidents") or []
    pdf.ln(2)
    pdf.para(f"Correlated motion incidents (motion on 2+ cameras within {tl.get('correlation_tolerance_seconds')} s): {len(inc)}")
    if inc:
        pdf.table(["Start", "End", "Duration (s)", "Cameras"], [55, 55, 25, 50],
                  [[i["start"][:19], i["end"][:19], i["duration_s"], ", ".join(map(str, i["channels"]))] for i in inc[:30]])

    pdf.h1("6. Video analytics")
    analysed = [(c["clip_id"], c["analytics"]) for c in report["clips"] if c.get("analytics")]
    if not analysed:
        pdf.para("Analytics were not run for this case.")
    else:
        rows = []
        for cid, a in analysed:
            if a.get("error"):
                rows.append([cid, "-", "-", "-", a["error"][:60]])
                continue
            objs = a.get("objects", {})
            obj_txt = ", ".join(f"{k}:{n}" for k, n in sorted(objs.get("counts", {}).items(), key=lambda kv: -kv[1])[:4]) \
                if "counts" in objs else objs.get("status", "-")
            rows.append([cid, a.get("duration_s"), len(a.get("motion_events", [])),
                         a.get("faces", {}).get("frames_with_faces", "-"), obj_txt])
        pdf.table(["Clip", "Duration (s)", "Motion events", "Frames w/ faces", "Objects"], [30, 22, 24, 26, pdf.epw - 102], rows)
        pdf.para("Face detection uses a Haar cascade and is indicative only; every lead must be confirmed by a person.")
        thumbs = [t for _, a in analysed for t in a.get("thumbnails", [])][:6]
        if thumbs and base_dir:
            pdf.ln(2)
            x, y, tw = pdf.l_margin, pdf.get_y(), (pdf.epw - 8) / 3
            row_h = tw * 0.75 + 7          # 4:3 thumbnail + caption + spacing
            if y + row_h > pdf.h - 20:
                pdf.add_page()
                y = pdf.get_y()
            for n, t in enumerate(thumbs):
                p = os.path.join(base_dir, t)
                if not os.path.exists(p):
                    continue
                if n and n % 3 == 0:
                    x, y = pdf.l_margin, y + row_h
                    if y + row_h > pdf.h - 20:
                        pdf.add_page()
                        y = pdf.get_y()
                pdf.image(p, x=x, y=y, w=tw)
                pdf.set_xy(x, y + tw * 0.75 + 0.5)
                pdf.set_font("Helvetica", "", 6)
                pdf.cell(tw, 3, _t(os.path.basename(t)))
                x += tw + 4
            pdf.set_xy(pdf.l_margin, y + row_h)

    pdf.h1("7. Chain of custody")
    cust = report.get("custody") or {}
    ver = cust.get("verification") or {}
    pdf.kv("Hash chain", ("VALID" if ver.get("valid") else "BROKEN: " + "; ".join(ver.get("problems", []))) if ver else "-")
    pdf.kv("Chain head (SHA-256)", ver.get("head_hash"), mono=True)
    pdf.table(["#", "Time (UTC)", "Actor", "Action", "Entry hash"], [8, 42, 42, 38, pdf.epw - 130],
              [[e["seq"], e["timestamp_utc"][:19], e["actor"], e["action"], e["entry_hash"][:24] + "..."]
               for e in cust.get("entries", [])])
    pdf.para("The full log continues in the case's custody file; any later edit to an entry breaks every hash after it.")

    pdf.h1("8. Method and limitations")
    pdf.para(
        "Clip hashes (MD5, SHA-256) are computed over the exact bytes of each clip in the evidence image; the exported "
        ".dav/.es file is those same bytes, so re-hashing it must give the reported SHA-256. MP4 files are playback "
        "copies only. Dahua-family parsing follows published layouts (FFmpeg dhav.c; Rzayeva et al., Information 2025). "
        "Hikvision and other vendors are recovered by vendor-agnostic H.264/H.265 carving; carved segments carry no "
        "timestamps. Clips are labelled 'carved' because file-system indexes are not parsed, so live vs deleted is not "
        "asserted. Parser statistics (rejected candidates) are in the JSON report.")
    pdf.output(out_path)
