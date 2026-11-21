import React from 'react';

// Driven directly by the Case.status enum (design doc 7.3).
export const STATUS_STYLES = {
  Processing: 'badge badge-processing',
  Recovered: 'badge badge-recovered',
  Hashed: 'badge badge-hashed',
  'Report Ready': 'badge badge-ready',
  Failed: 'badge badge-failed',
};

export default function StatusBadge({ status }) {
  const cls = STATUS_STYLES[status] || 'badge';
  return <span className={cls}>{status || 'Unknown'}</span>;
}
