import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { getCase, openReportPdf, fetchClipBlob, saveBlob } from '../api.js';
import StatusBadge from '../components/StatusBadge.jsx';
import HashDisplay from '../components/HashDisplay.jsx';
import ClipTable from '../components/ClipTable.jsx';

export default function CaseDetail() {
  const { id } = useParams();
  const [c, setC] = useState(null);
  const [error, setError] = useState('');
  const [playing, setPlaying] = useState(null); // { clipId, url }

  useEffect(() => {
    let alive = true;
    let timer;
    const load = () => getCase(id).then((d) => {
      if (!alive) return;
      setC(d);
      // keep polling until the engine finishes (Report Ready or Failed)
      if (d.status === 'Processing') timer = setTimeout(load, 2000);
    }).catch((e) => alive && setError(e.message));
    load();
    return () => { alive = false; clearTimeout(timer); };
  }, [id]);

  useEffect(() => () => { if (playing) URL.revokeObjectURL(playing.url); }, [playing]);

  const play = async (clip) => {
    try { setPlaying({ clipId: clip.clip_id, url: URL.createObjectURL(await fetchClipBlob(id, clip.clip_id, 'mp4')) }); }
    catch (e) { setError(e.message); }
  };
  const download = async (clip, fmt) => {
    try { saveBlob(await fetchClipBlob(id, clip.clip_id, fmt), `${clip.clip_id}.${fmt}`); }
    catch (e) { setError(e.message); }
  };

  if (error) return <p className="error">{error}</p>;
  if (!c) return <p className="muted">Loading…</p>;

  return (
    <>
      <div className="card-row">
        <h2>{c.filename}</h2>
        <StatusBadge status={c.status} />
      </div>
      <p className="muted">
        Vendor: <strong>{c.oem || 'unknown'}</strong>
        {c.oemConfidence && ` (detection: ${c.oemConfidence})`}
      </p>
      {c.status === 'Processing' && <p className="muted">The engine is detecting, recovering and hashing. This page updates automatically.</p>}
      {c.status === 'Failed' && <p className="error">Processing failed: {c.error}</p>}
      <HashDisplay label="Image MD5" value={c.hashMD5} />
      <HashDisplay label="Image SHA-256" value={c.hashSHA256} />
      <h3>Recovered clips ({(c.clips || []).length})</h3>
      <ClipTable clips={c.clips} onPlay={play} onDownload={download} />
      {playing && (
        <div className="panel" style={{ marginBottom: 16 }}>
          <strong>{playing.clipId}</strong>
          <video src={playing.url} controls style={{ width: '100%', maxHeight: 360, display: 'block', marginTop: 8 }} />
        </div>
      )}
      {c.status === 'Report Ready' && (
        <button className="primary" onClick={() => openReportPdf(c._id).catch((e) => setError(e.message))}>
          Open PDF report
        </button>
      )}
    </>
  );
}
