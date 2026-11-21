# OmniTrace — Core Forensic Engine (Harsh's piece)

This is Section 7.6 of the design doc, built: OEM detection → per-OEM
parser plugin → shared recovery/carving → hashing → report. See the
doc for the diagrams; this README is just setup + the API contract.

## Engine modules (v0.3.0)

| Problem-statement module | File | What it does | Status |
|---|---|---|---|
| Acquisition | `acquire.py` | Read-only imaging of a disk/file, MD5+SHA-256 during copy, verification re-hash, bad sectors zero-filled + listed, never overwrites, `.acquisition.json` record. E01 to raw via libewf-python (optional) | Tested on files + simulated bad sectors; **not yet run on a physical DVR disk** |
| Device identification | `identify.py` | Vendor / model / firmware from model-number patterns + brand strings (Hikvision, Dahua, CP Plus, Uniview, TP-Link, Matrix, Honeywell, Godrej), plus file-system family. Every hit has a byte offset as evidence | Patterns are public catalogue naming, not documented on-disk fields: confirm against the device label |
| FS & format parsing | `parsers/` | Dahua DHAV (published layout), Hikvision (signature only, carving), **Generic** fallback for any other vendor whose disk holds H.264/H.265 | Unchanged honesty rules below |
| Recovery | `recovery.py` | Vendor-agnostic H.264/H.265 carving; carved segments are now exported as `.es` (bytes == hashed bytes) | |
| Integrity | `hasher.py`, `pipeline.py` | Image hashed before AND after processing (`verified_after_processing`), checked against the acquisition record if present | |
| Chain of custody | `custody.py` | Hash-chained, append-only `<case>.custody.jsonl`: every pipeline step + any event the API appends; `verify()` pinpoints edits/deletions | |
| Timeline | `timeline.py` | UTC conversion (`dvr_tz`) + clock-drift correction (`clock_offset_seconds`), per-channel recording gaps, concurrent-recording windows, **cross-camera correlated motion incidents** | |
| ML / analytics | `analytics.py` | Motion (OpenCV MOG2), faces (Haar cascade, indicative only), objects (YOLOv8 ONNX via OpenCV DNN if `OMNITRACE_YOLO_ONNX` is set), motion thumbnails | Object detection needs the model file |
| Reporting | `report.py` | JSON (all old keys kept) + 8-section PDF: integrity, device, clips, drawn timeline chart, analytics thumbnails, custody table, limitations | |

## CLI (no web stack needed)

```bash
python tools/make_demo_image.py                      # demo disk: 3 cameras, real H.264, staged events (SYNTHETIC)
python cli.py analyze sample_data/demo_cpplus.img --out reports --tz +05:30 --analytics \
       --examiner "SI Sharma" --case-ref FIR-123/2026 [--clock-offset 120] [--force-oem Generic]
python cli.py acquire --source \\.\PhysicalDrive2 --out case.img --examiner "SI Sharma"   # behind a write-blocker
python cli.py identify case.img
python cli.py custody reports/<case_id>.custody.jsonl
python cli.py analytics clip.mp4
```

The demo generator and MP4 export need ffmpeg: on PATH, `OMNITRACE_FFMPEG=...`, or `pip install imageio-ffmpeg`.

## Setup

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Run the tests (no real hardware needed)

```bash
pytest tests/ -v
```

This generates synthetic Dahua/Hikvision/unknown-vendor images on the fly and
runs the full pipeline against them (84 tests: 77 pass, 7 FFmpeg cross-checks skip unless
`ffmpeg` + `ffprobe` are on PATH). These are NOT real DVR images — see "Honest build status"
below for exactly what's real vs. placeholder.

## Run the server

```bash
python app.py
# -> http://localhost:5000
```

Try it manually:

```bash
curl -F "image=@sample_data/synthetic_dahua.img" -F "dvr_tz=+05:30" http://localhost:5000/process
```

(Run `python tests/make_synthetic_images.py` first if `sample_data/`
is empty.)

## API contract — for Khushbu's Node/Express layer

This is the ONLY thing the Node API should ever talk to. Never import
Python modules directly from Node; always go over HTTP.

| Method | Path | Body | Returns |
|---|---|---|---|
| `POST` | `/process` | multipart form: `image` = raw disk image file; optional `dvr_tz` (e.g. `+05:30`), `clock_offset_seconds`, `examiner`, `case_ref`, `agency`, `notes`, `analytics=1`, `force_oem` (`Dahua`/`Hikvision`/`Generic`) | `{case_id, oem, oem_confidence, clip_count, report_json_url, report_pdf_url, custody_url, device, integrity_ok}` (200), `{error}` (400 bad params, 422 OEM unrecognized) |
| `GET` | `/report/<case_id>.json` | — | the full JSON report (clips, hashes, device, integrity, timeline, analytics, custody) |
| `GET` | `/report/<case_id>.pdf` | — | the court-style PDF |
| `GET` | `/report/<case_id>/<file>` | — | exported clip (`.dav`, `.es`, `.mp4`) or analytics thumbnail (`.jpg`) |
| `GET` | `/custody/<case_id>` | — | `{entries, verification: {valid, head_hash, problems}}` |
| `POST` | `/custody/<case_id>` | JSON `{actor, action, details?}` | appended entry (201). The API should call this on every view/download |
| `GET` | `/capabilities` | — | parsers, vendors identified, analytics availability |
| `GET` | `/health` | — | `{status: "ok", engine_version}` |

Suggested flow for Khushbu's `/api/acquire` route: receive the upload
from the React frontend → forward the file to this engine's
`/process` → store the returned `case_id`/`oem`/`report_*_url` fields
in the `cases` MongoDB collection (see design doc Section 7.3) → return
the case record to the frontend.

`/process` runs synchronously and will block until done. Fine for
Month 1 test images (seconds). Before testing with real multi-GB DVR
images, this should move to a background job + polling — flag that to
me before Month 2 if it becomes a bottleneck.

## Calibration status (read before demoing to anyone)

Nothing here has been run on a real DVR disk yet. What changed: the Dahua parser now follows
**published** layouts instead of my earlier guess (which was wrong: field order, and the timestamp
is bit-packed local time, not Unix seconds).

| Piece | Basis | Status |
|---|---|---|
| Dahua frame header field ORDER (type, subtype, channel, frame no., frame length, date) | FFmpeg `libavformat/dhav.c` + MDPI *Information* 2025, 16(11):983 (they agree) | Implemented, **not confirmed on our hardware** |
| Dahua date bit-packing (sec/min/hour/day/month/year-2000) | FFmpeg `dhav.c` `get_timeinfo` | Implemented, not confirmed on hardware. It is the DVR's LOCAL clock: you must supply `dvr_tz` |
| Dahua footer `dhav` at (frame start + length - 8), 32-byte overhead | MDPI paper only | Implemented as a validity check; paper is the only source |
| Header checksum / footer size+checksum | MDPI paper | **Not verified** |
| Dahua detection: `DHFS4.1` at offsets 512/1024/2048, or >=3 `DHAV` magics | MDPI paper / FFmpeg | Implemented |
| Hikvision detection: `HIKVISION@HANGZHOU` in first 4 KB | MDPI paper + 2 independent secondary sources | Implemented, not seen on hardware |
| Hikvision frame/file-table layout | MDPI paper prints one, single source, no hex dumps | **Deliberately NOT implemented.** Needs Han & Jeong 2015 (Springer, ask your college library) or a real image |
| Frame-to-segment grouping | Frame-number step <=2 (paper), fixed 10 s gap (ours) | Our own rule, tune on real data |

What the engine does NOT know: whether a frame is "live" or "deleted" - that needs the DHFS index,
which is not parsed. So Dahua clips are labelled `carved`, never `live`. The accuracy harness judges
by time window, so it is unaffected.

Validation: a frame is reported only if header magic, sane length, decodable date AND footer magic
all hold. Failures are counted in the report's `parser_stats` (e.g. `rejected_no_footer`), not hidden.
Raw NAL carving (Hikvision) requires >=3 NAL starts in a segment, because a lone spurious start code
turns up roughly 6 times per GB of random-looking data.

Known issues / limits:
- A Dahua clip is a SEGMENT of frames; frames of other channels interleave on disk, so a clip is a
  list of byte extents, and its hashes cover only its own frames (tested).
- Extents are kept in memory as Python lists: fine for hundreds of MB, heavy for multi-hundred-GB disks.
- Hashing reads the disk again after parsing. Budget time for big images.
- `/process` is synchronous in the engine (the API wraps it in a background job).
- Synthetic tests follow the same published layout the parser implements, so they prove the logic and
  the spec match, not that real firmware follows the spec.

## Exporting and playing recovered clips

For every validated Dahua clip the engine writes `<data_dir>/<case_id>/<clip_id>.dav` (the clip's own
frames, concatenated: a valid DHAV stream) and, if `ffmpeg` is installed, an `.mp4` remux
(`-c copy`, no re-encoding). The report lists them under each clip's `export`. Served at
`/report/<case_id>/<clip_id>.dav|.mp4`. Export is skipped (with a note in the report) if the clips total
more than `OMNITRACE_EXPORT_MAX_GB` (default 2).

**Integrity:** the `.dav` is exactly the bytes that were hashed, so its SHA-256 equals the clip's reported
SHA-256. The report records `dav_sha256_matches_clip`; anyone can re-hash the file and check. The MP4 is a
convenience copy, not the evidence.

## Independent check against FFmpeg (tests/test_ffmpeg_crosscheck.py)

`tests/make_real_dhav.py` wraps REAL H.264 (made by libx264) in DHAV frames laid out as FFmpeg's `dhav.c`
reads them. FFmpeg, an independent implementation, then has to agree with us:
- the exported clip is read as format `dhav`, H.264, 320x240, with the same packet count as our frame count;
- every frame decodes with zero errors;
- FFmpeg's first/last packet timestamps equal our decoded `start_ts`/`end_ts`;
- the MP4 remux has the same number of frames.

**Negative control (in the suite):** the same stream written with my first, wrong field order still gets the
same packet count from FFmpeg (it resyncs on the `DHAV` magic) but a timestamp off by decades. So the
timestamp comparison is what has teeth, not the frame count. Tests skip if ffmpeg/dhav is missing.

**What this does NOT prove:** that a given real DVR writes DHAV this way. It proves our parser and FFmpeg's
reader agree on the published layout. The first real image is still the test that matters.

## Sources
- FFmpeg `libavformat/dhav.c`: https://github.com/FFmpeg/FFmpeg/blob/master/libavformat/dhav.c
- Rzayeva et al., *Information* 2025, 16(11), 983: https://doi.org/10.3390/info16110983
- Han, Jeong, Lee, "Analysis of the HIKVISION DVR File System", ICDF2C 2015 (not yet read: paywalled)

## Adding a new OEM (e.g. CP Plus, Month 2)

1. Create `parsers/cp_plus.py` implementing `BaseParser` (see
   `parsers/base.py`).
2. Add it to the list in `detector.build_default_registry()`.

Nothing else changes — that's the whole point of the plugin boundary
in Section 7.6 of the design doc.


## Measuring accuracy (once Manish has a real image)

```bash
# 1. Manish fills tools/ground_truth_template.csv (see his guide), then:
python tools/convert_truth.py --csv ground_truth.csv --tz +05:30 --out truth.json
# 2. Run the real image through the engine, then score it:
python evaluate.py --report <case_id>.json --truth truth.json --tolerance 2   # add --dvr-tz +05:30 if the report has no offset
```

It prints recovery rate, temporal accuracy, false-positive rate, and how many recovered clips
could not be checked (no timestamp). Definitions are at the top of `evaluate.py`.

**Do not compare our numbers to the MDPI paper's 91.8% / 96.7% without saying our definitions
differ.** Ours: recovery = presence of >=1 recovered clip in each deleted clip's time window, not
completeness of the footage. The honest framing for judges is "measured on N real units with our
own ground-truth log", not "matches the published figure".

`pytest tests/test_evaluate.py` checks the harness MATH against a truth built from what the
synthetic generator really wrote. It says nothing about accuracy on real footage.

## Real-disk readiness

- Fixed: nothing reads a whole image into memory any more (`scan.py` streams in 16 MB chunks;
  verified: a 600 MB image processed at ~115 MB peak, and 26 tests prove chunked scans equal
  whole-file scans at every chunk size, including matches straddling chunk boundaries).
- Clip granularity is solved for Dahua by segment grouping (see Calibration status); the grouping
  thresholds still need tuning on a real image.
- Hashing the whole image + each clip reads the disk twice; budget time for big images.
