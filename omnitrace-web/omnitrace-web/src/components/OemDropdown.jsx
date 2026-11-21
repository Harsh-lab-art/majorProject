import React from 'react';

export const OEM_OPTIONS = ['Hikvision', 'Dahua'];

// Pure function so the form (and tests) can validate without rendering.
export function validateOem(value) {
  if (!value) return 'Select the DVR/NVR vendor before submitting.';
  if (!OEM_OPTIONS.includes(value)) return `Unsupported vendor: ${value}`;
  return null;
}

export default function OemDropdown({ value, onChange, error }) {
  return (
    <label className="field">
      <span>Vendor</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-invalid={Boolean(error)}>
        <option value="">Select vendor...</option>
        {OEM_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
      {error && <small className="error">{error}</small>}
    </label>
  );
}
