import React from 'react';

export default function UploadProgressBar({ percent = 0, label = 'Uploading' }) {
  const p = Math.max(0, Math.min(100, Math.round(percent)));
  return (
    <div className="progress" role="progressbar" aria-valuenow={p} aria-valuemin={0} aria-valuemax={100}>
      <div className="progress-fill" style={{ width: `${p}%` }} />
      <span className="progress-label">{label} {p}%</span>
    </div>
  );
}
