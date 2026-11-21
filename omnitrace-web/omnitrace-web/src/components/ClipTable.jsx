import React from 'react';

const short = (h) => (h ? `${h.slice(0, 12)}…` : '—');

export default function ClipTable({ clips = [], onPlay, onDownload }) {
  if (!clips.length) return <p className="muted">No clips recovered.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Clip</th><th>Status</th><th>Frames</th><th>Length</th><th>Start</th><th>End</th><th>MD5</th><th>SHA-256</th><th>Files</th></tr>
        </thead>
        <tbody>
          {clips.map((k) => (
            <tr key={k.clip_id}>
              <td>{k.clip_id}</td>
              <td>{k.status}</td>
              <td>{k.frame_count ?? '—'}</td>
              <td>{k.length}</td>
              <td>{k.start_ts || 'unknown'}</td>
              <td>{k.end_ts || '—'}</td>
              <td title={k.md5}><code>{short(k.md5)}</code></td>
              <td title={k.sha256}><code>{short(k.sha256)}</code></td>
              <td>
                {k.export?.mp4 && onPlay && <button type="button" className="link" onClick={() => onPlay(k)}>Play</button>}
                {k.export?.dav && onDownload && <button type="button" className="link" style={{ marginLeft: 8 }} onClick={() => onDownload(k, 'dav')}>.dav</button>}
                {k.export?.dav_sha256_matches_clip && <span className="ok" title="The exported .dav hashes to this clip's SHA-256"> ✓</span>}
                {!k.export && <span className="muted">—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
