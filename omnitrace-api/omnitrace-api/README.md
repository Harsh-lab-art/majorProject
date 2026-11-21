# OmniTrace API (Khushbu's layer)

Express API between the React dashboard and Harsh's Python engine
(design doc Section 7.4). Talks to the engine ONLY over HTTP.

## Run (zero setup)

```bash
npm install
cp .env.example .env
npm start            # http://localhost:4000, in-memory store, mock engine
npm test             # 6 tests
```

Defaults let the frontend team work on day 1 with no Python and no MongoDB:
- `MONGO_URI` empty -> in-memory store (data resets on restart)
- `ENGINE_MODE=mock` -> canned engine response (2 clips, fake hashes)

Switch to real pieces when ready:
- `MONGO_URI=mongodb://localhost:27017/omnitrace` -> MongoDB (UNTESTED here, no mongod in the build sandbox; schema follows ER diagram 7.3. Verify on first run.)
- `ENGINE_MODE=real` + `ENGINE_URL=http://localhost:5000` -> Harsh's Flask engine (tested end to end with synthetic images)

## Endpoints (what the React app calls)

All except auth need header `Authorization: Bearer <token>`.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | /api/auth/register | `{name,email,password}` | `{token,user}` 201 |
| POST | /api/auth/login | `{email,password}` | `{token,user}` |
| POST | /api/acquire | multipart: `image` (file), `oem` (optional), `dvr_tz` (optional, e.g. `+05:30`: the DVR clock's offset from UTC; 400 if malformed) | 202 + the new case with `status: "Processing"`. Poll `GET /api/case/:id` until it becomes `Report Ready` or `Failed` (`error` says why, e.g. unrecognized OEM) |
| GET | /api/cases | - | array of the user's cases, newest first |
| GET | /api/case/:id | - | one case |
| GET | /api/case/:id/clip/:clipId/:fmt | `fmt` = `dav` or `mp4` | the recovered clip as a file (attachment). `dav` is the evidentiary copy: its SHA-256 equals the clip's `sha256`. 404 if the clip has no export (e.g. carved Hikvision segments) |
| GET | /api/report/:id | `?format=json` optional | PDF stream (or JSON) |

Case shape: `_id, userId, filename, oem, oemConfidence, status, hashMD5, hashSHA256, clips[], reportJsonUrl, reportPdfUrl, createdAt`.
`status` is one of `Processing | Recovered | Hashed | Report Ready | Failed` (drives the Status Badge).
Each clip: `clip_id, offset, length, start_ts, end_ts, channel, status, codec, md5, sha256`.

## Known limits (be honest in demos)
- Processing runs in-process in the background: if the API restarts mid-job the case stays "Processing" forever. A real job queue is the next step for production.
- Uploads are streamed to a temp file on disk (deleted after processing), not held in memory.
- `oem` from the form is only the user's selection; the engine's detection overwrites it.
- Auth is prototype-grade: one role, no refresh tokens, default JWT secret unless set.
