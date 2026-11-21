import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { listCases } from '../api.js';
import CaseCard from '../components/CaseCard.jsx';
import CaseFilterBar, { filterCases } from '../components/CaseFilterBar.jsx';

export default function CaseList() {
  const [cases, setCases] = useState(null);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('All');

  const hasProcessing = (cases || []).some((c) => c.status === 'Processing');

  useEffect(() => {
    let alive = true;
    const load = () => listCases().then((d) => alive && setCases(d)).catch((e) => alive && setError(e.message));
    load();
    // keep refreshing while anything is still processing
    const timer = hasProcessing ? setInterval(load, 3000) : null;
    return () => { alive = false; if (timer) clearInterval(timer); };
  }, [hasProcessing]);

  if (error) return <p className="error">{error}</p>;
  if (!cases) return <p className="muted">Loading cases…</p>;
  const shown = filterCases(cases, { query, status });
  return (
    <>
      <div className="card-row"><h2>Cases</h2><Link to="/new" className="primary-link">+ New case</Link></div>
      <CaseFilterBar query={query} status={status} onQuery={setQuery} onStatus={setStatus} />
      {cases.length === 0 && <p className="muted">No cases yet. Start with “New case”.</p>}
      {cases.length > 0 && shown.length === 0 && <p className="muted">No cases match your filter.</p>}
      {shown.map((c) => <CaseCard key={c._id} c={c} />)}
    </>
  );
}
