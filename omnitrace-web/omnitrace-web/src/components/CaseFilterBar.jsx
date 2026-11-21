import React from 'react';

export const STATUS_FILTERS = ['All', 'Processing', 'Report Ready', 'Failed'];

// Pure function: easy to test, and CaseList just calls it.
export function filterCases(cases, { query = '', status = 'All' } = {}) {
  const q = query.trim().toLowerCase();
  return cases.filter((c) => {
    if (status !== 'All' && c.status !== status) return false;
    if (!q) return true;
    return [c.filename, c.oem, c.hashSHA256, c.hashMD5]
      .filter(Boolean)
      .some((v) => String(v).toLowerCase().includes(q));
  });
}

export default function CaseFilterBar({ query, status, onQuery, onStatus }) {
  return (
    <div className="filterbar">
      <input placeholder="Search filename, vendor or hash…" value={query} onChange={(e) => onQuery(e.target.value)} />
      <select value={status} onChange={(e) => onStatus(e.target.value)}>
        {STATUS_FILTERS.map((s) => <option key={s} value={s}>{s}</option>)}
      </select>
    </div>
  );
}
