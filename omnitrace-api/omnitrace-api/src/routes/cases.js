const express = require('express');
const fs = require('fs');
const os = require('os');
const multer = require('multer');
const { requireAuth } = require('../middleware/auth');
const engine = require('../engineClient');

const upload = multer({ storage: multer.diskStorage({ destination: os.tmpdir() }), limits: { fileSize: 2 * 1024 * 1024 * 1024 } });

module.exports = (store) => {
  const r = express.Router();
  r.use(requireAuth);

  // Accepts the upload, returns immediately with status "Processing" (202), and runs the
  // engine in the background. The frontend polls GET /api/case/:id until status changes.
  r.post('/acquire', upload.single('image'), async (req, res) => {
    if (!req.file) return res.status(400).json({ error: "multipart field 'image' is required" });
    const dvrTz = (req.body.dvr_tz || '').trim() || null;
    if (dvrTz && !/^[+-]\d{2}:\d{2}$/.test(dvrTz)) {
      fs.unlink(req.file.path, () => {});
      return res.status(400).json({ error: 'dvr_tz must look like +05:30' });
    }
    const selectedOem = req.body.oem || null; // user's selection; engine's detection is authoritative
    const c = await store.createCase({
      userId: req.user.id, filename: req.file.originalname, oem: selectedOem, dvrTz, status: 'Processing',
    });
    res.status(202).json(c);
    runJob(store, c._id, req.file, dvrTz);
  });

  r.get('/cases', async (req, res) => res.json(await store.listCases(req.user.id)));

  r.get('/case/:id', async (req, res) => {
    const c = await store.getCase(req.params.id);
    if (!c || c.userId !== req.user.id) return res.status(404).json({ error: 'case not found' });
    res.json(c);
  });

  // Recovered clip as a file: fmt = 'dav' (evidentiary copy, SHA-256 equals the clip hash) or 'mp4' (playback remux).
  r.get('/case/:id/clip/:clipId/:fmt', async (req, res) => {
    const c = await store.getCase(req.params.id);
    if (!c || c.userId !== req.user.id) return res.status(404).json({ error: 'case not found' });
    const { clipId, fmt } = req.params;
    if (!['dav', 'mp4'].includes(fmt)) return res.status(400).json({ error: "format must be 'dav' or 'mp4'" });
    const clip = (c.clips || []).find((k) => k.clip_id === clipId);
    const path = clip && clip.export && clip.export[fmt];
    if (!path) return res.status(404).json({ error: `no ${fmt} export for this clip` });
    const upstream = await engine.fetchReportFile(`/report/${path}`);
    if (!upstream) return res.status(501).json({ error: 'no clip files in mock engine mode' });
    if (!upstream.ok) return res.status(502).json({ error: 'engine could not serve the file' });
    res.set('Content-Type', fmt === 'mp4' ? 'video/mp4' : 'application/octet-stream');
    res.set('Content-Disposition', `attachment; filename="${clipId}.${fmt}"`);
    res.send(Buffer.from(await upstream.arrayBuffer()));
  });

  // Streams the engine's PDF (or JSON with ?format=json) through to the browser.
  r.get('/report/:id', async (req, res) => {
    const c = await store.getCase(req.params.id);
    if (!c || c.userId !== req.user.id) return res.status(404).json({ error: 'case not found' });
    const wantJson = req.query.format === 'json';
    const upstream = await engine.fetchReportFile(wantJson ? c.reportJsonUrl : c.reportPdfUrl);
    if (!upstream) return res.status(501).json({ error: 'no report file in mock engine mode', case: c });
    res.set('Content-Type', upstream.headers.get('content-type') || 'application/octet-stream');
    res.send(Buffer.from(await upstream.arrayBuffer()));
  });
  return r;
};

async function runJob(store, caseId, file, dvrTz) {
  try {
    const { summary, report } = await engine.processImage(file, { dvrTz });
    await store.updateCase(caseId, {
      oem: report.oem, oemConfidence: report.oem_detection_confidence,
      engineCaseId: summary.case_id, hashMD5: report.image_md5, hashSHA256: report.image_sha256,
      clips: report.clips, reportJsonUrl: summary.report_json_url, reportPdfUrl: summary.report_pdf_url,
      status: 'Report Ready',
    });
  } catch (err) {
    await store.updateCase(caseId, { status: 'Failed', error: err.message });
  } finally {
    fs.unlink(file.path, () => {});
  }
}
