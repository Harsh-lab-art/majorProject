import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { acquire } from '../api.js';
import OemDropdown, { validateOem } from '../components/OemDropdown.jsx';
import { validateTz } from '../components/validateTz.js';
import UploadProgressBar from '../components/UploadProgressBar.jsx';

export default function NewCase() {
  const navigate = useNavigate();
  const [file, setFile] = useState(null);
  const [oem, setOem] = useState('');
  const [oemError, setOemError] = useState('');
  const [dvrTz, setDvrTz] = useState('');
  const [error, setError] = useState('');
  const [percent, setPercent] = useState(0);
  const [phase, setPhase] = useState('idle'); // idle | uploading

  async function submit(e) {
    e.preventDefault();
    setError('');
    const oemProblem = validateOem(oem);
    setOemError(oemProblem || '');
    if (oemProblem) return;
    const tzProblem = validateTz(dvrTz);
    if (tzProblem) return setError(tzProblem);
    if (!file) return setError('Choose a raw disk image file first.');

    setPhase('uploading'); setPercent(0);
    try {
      const c = await acquire({
        file, oem, dvrTz: dvrTz.trim(),
        onProgress: setPercent,
      });
      navigate(`/case/${c._id}`);
    } catch (err) {
      setError(err.message);
      setPhase('idle');
    }
  }

  const busy = phase !== 'idle';
  return (
    <form className="panel narrow" onSubmit={submit}>
      <h2>New case</h2>
      <OemDropdown value={oem} onChange={setOem} error={oemError} />
      <label className="field">
        <span>DVR clock offset from UTC (optional, e.g. +05:30)</span>
        <input value={dvrTz} onChange={(e) => setDvrTz(e.target.value)} placeholder="+05:30" disabled={busy} />
        <small className="muted">The DVR stores local time with no timezone. Without this, clip times are shown as the DVR's own clock.</small>
      </label>
      <label className="field">
        <span>Raw disk image</span>
        <input type="file" onChange={(e) => setFile(e.target.files[0] || null)} disabled={busy} />
      </label>
      {phase === 'uploading' && <UploadProgressBar percent={percent} />}
      {error && <p className="error">{error}</p>}
      <button className="primary" disabled={busy}>{busy ? 'Working…' : 'Start analysis'}</button>
    </form>
  );
}
