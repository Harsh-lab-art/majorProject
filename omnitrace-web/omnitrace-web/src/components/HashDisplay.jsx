import React, { useState } from 'react';

export default function HashDisplay({ label, value }) {
  const [copied, setCopied] = useState(false);
  if (!value) return null;
  const copy = async () => {
    try { await navigator.clipboard.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 1500); }
    catch { /* clipboard blocked: user can still select the text */ }
  };
  return (
    <div className="hash">
      <span className="hash-label">{label}</span>
      <code>{value}</code>
      <button type="button" className="link" onClick={copy}>{copied ? 'Copied' : 'Copy'}</button>
    </div>
  );
}
