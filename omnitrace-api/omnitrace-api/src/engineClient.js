// The ONLY place that talks to Harsh's Python engine. Never import Python
// code from Node; always go over HTTP (see engine README for the contract).
const fs = require('fs');
const config = require('./config');

const MOCK_REPORT = {
  case_id: 'mock-engine-case', oem: 'Dahua', oem_detection_confidence: 'high',
  image_md5: 'd41d8cd98f00b204e9800998ecf8427e',
  image_sha256: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
  clip_count: 2,
  clips: [
    { clip_id: 'dahua-000000', offset: 512, length: 9000, start_ts: '2026-11-01T10:00:00+00:00', end_ts: null, channel: 0, status: 'carved', codec: null, frame_count: 40, validated: true, md5: 'a'.repeat(32), sha256: 'b'.repeat(64),
      export: { dav: 'mock-engine-case/dahua-000000.dav', mp4: 'mock-engine-case/dahua-000000.mp4', dav_sha256_matches_clip: true } },
    { clip_id: 'dahua-000001', offset: 9600, length: 4200, start_ts: null, end_ts: null, channel: null, status: 'carved', codec: 'H264', md5: 'c'.repeat(32), sha256: 'd'.repeat(64) },
  ],
};

async function processImage(file, { dvrTz } = {}) {
  if (config.engineMode === 'mock') {
    return {
      summary: { case_id: 'mock-engine-case', oem: MOCK_REPORT.oem, oem_confidence: 'high', clip_count: 2,
        report_json_url: '/report/mock-engine-case.json', report_pdf_url: '/report/mock-engine-case.pdf' },
      report: MOCK_REPORT,
    };
  }
  const form = new FormData();
  form.append('image', await fs.openAsBlob(file.path), file.originalname);
  if (dvrTz) form.append('dvr_tz', dvrTz);
  const res = await fetch(`${config.engineUrl}/process`, { method: 'POST', body: form });
  const summary = await res.json();
  if (!res.ok) { const e = new Error(summary.error || 'engine error'); e.status = res.status; throw e; }
  const report = await (await fetch(`${config.engineUrl}${summary.report_json_url}`)).json();
  return { summary, report };
}

async function fetchReportFile(url) {
  if (config.engineMode === 'mock') return null;
  return fetch(`${config.engineUrl}${url}`);
}

module.exports = { processImage, fetchReportFile };
