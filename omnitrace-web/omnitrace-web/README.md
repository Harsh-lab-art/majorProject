# OmniTrace Web (Khushbu + Thakre)

React (Vite) dashboard. Design doc Sections 7.4 and 7.5.

## Run

```bash
npm install
npm run dev      # http://localhost:5173
npm test         # component tests
```

Needs the API running on port 4000 (`npm start` in omnitrace-api). Vite proxies `/api` to it.
Day-1 setup with no Python or MongoDB: leave the API on its defaults (mock engine, in-memory store).

## Who owns what

**Thakre** - isolated components in `src/components/` (each is small, takes props, has no API calls):
- `StatusBadge` (driven by the Case.status enum)
- `UploadProgressBar`
- `OemDropdown` (+ `validateOem`)
- `CaseCard`
- `HashDisplay` (copy button)
- `ClipTable` (now has Play / .dav buttons; a green tick means the exported .dav hashes to the clip's SHA-256)
- `CaseFilterBar`, `validateTz`

Tests for them: `src/components/components.test.js`. Add a test whenever you change one.

**Khushbu** - screens in `src/pages/` plus `src/api.js` and `src/App.jsx` (routing, auth). Pages call `api.js`, never `fetch` directly.

## Status
- Verified end to end in headless Chromium against the real API + real engine (synthetic images): register -> vendor validation -> upload -> 5 clips + hashes -> PDF request -> case list.
- NOT done: the PDF opening in a new tab was not visually verified (the endpoint returns a valid PDF, confirmed with curl); MP4 playback was confirmed in headless Chromium only (other browsers/codecs untested); the table can overflow sideways on narrow screens; no mobile layout pass; tokens live in localStorage (prototype-grade).
