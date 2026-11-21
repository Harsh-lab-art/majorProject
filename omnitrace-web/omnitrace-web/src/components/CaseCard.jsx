import React from 'react';
import { Link } from 'react-router-dom';
import StatusBadge from './StatusBadge.jsx';

export default function CaseCard({ c }) {
  return (
    <Link to={`/case/${c._id}`} className="card">
      <div className="card-row">
        <strong>{c.filename || 'Untitled image'}</strong>
        <StatusBadge status={c.status} />
      </div>
      <div className="muted">
        {c.oem || 'OEM unknown'} · {(c.clips || []).length} clip(s)
        {c.createdAt ? ` · ${new Date(c.createdAt).toLocaleString()}` : ''}
      </div>
    </Link>
  );
}
