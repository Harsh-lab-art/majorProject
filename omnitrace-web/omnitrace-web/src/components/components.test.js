import { describe, it, expect } from 'vitest';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import StatusBadge, { STATUS_STYLES } from './StatusBadge.jsx';
import UploadProgressBar from './UploadProgressBar.jsx';
import { validateOem } from './OemDropdown.jsx';
import ClipTable from './ClipTable.jsx';
import CaseCard from './CaseCard.jsx';

const h = React.createElement;

describe('StatusBadge', () => {
  it('has a style for every status the API can return', () => {
    for (const s of ['Processing', 'Recovered', 'Hashed', 'Report Ready', 'Failed']) {
      expect(STATUS_STYLES[s]).toBeTruthy();
      expect(renderToStaticMarkup(h(StatusBadge, { status: s }))).toContain(s);
    }
  });
});

describe('UploadProgressBar', () => {
  it('clamps out-of-range percentages', () => {
    expect(renderToStaticMarkup(h(UploadProgressBar, { percent: 250 }))).toContain('width:100%');
    expect(renderToStaticMarkup(h(UploadProgressBar, { percent: -5 }))).toContain('width:0%');
  });
});

describe('validateOem', () => {
  it('rejects empty and unsupported vendors, accepts known ones', () => {
    expect(validateOem('')).toBeTruthy();
    expect(validateOem('Acme')).toBeTruthy();
    expect(validateOem('Dahua')).toBeNull();
    expect(validateOem('Hikvision')).toBeNull();
  });
});

describe('ClipTable', () => {
  it('shows an empty message and renders rows with unknown timestamps', () => {
    expect(renderToStaticMarkup(h(ClipTable, { clips: [] }))).toContain('No clips recovered');
    const html = renderToStaticMarkup(h(ClipTable, { clips: [{ clip_id: 'c1', status: 'carved', offset: 1, length: 2, start_ts: null, md5: 'a'.repeat(32), sha256: 'b'.repeat(64) }] }));
    expect(html).toContain('unknown');
    expect(html).toContain('aaaaaaaaaaaa…');
  });
});

describe('CaseCard', () => {
  it('links to the case detail page', () => {
    const html = renderToStaticMarkup(h(MemoryRouter, null, h(CaseCard, { c: { _id: 'abc', filename: 'd.img', status: 'Report Ready', oem: 'Dahua', clips: [1, 2] } })));
    expect(html).toContain('href="/case/abc"');
    expect(html).toContain('2 clip(s)');
  });
});

import { filterCases } from './CaseFilterBar.jsx';

describe('filterCases', () => {
  const cases = [
    { _id: '1', filename: 'lobby.img', oem: 'Dahua', status: 'Report Ready', hashSHA256: 'abc123' },
    { _id: '2', filename: 'garage.img', oem: 'Hikvision', status: 'Failed' },
    { _id: '3', filename: 'gate.img', oem: 'Dahua', status: 'Processing' },
  ];
  it('returns everything with no filter', () => {
    expect(filterCases(cases)).toHaveLength(3);
  });
  it('filters by status', () => {
    expect(filterCases(cases, { status: 'Failed' }).map((c) => c._id)).toEqual(['2']);
  });
  it('searches filename, vendor and hash case-insensitively', () => {
    expect(filterCases(cases, { query: 'LOBBY' }).map((c) => c._id)).toEqual(['1']);
    expect(filterCases(cases, { query: 'dahua' }).map((c) => c._id)).toEqual(['1', '3']);
    expect(filterCases(cases, { query: 'ABC1' }).map((c) => c._id)).toEqual(['1']);
  });
  it('combines query and status', () => {
    expect(filterCases(cases, { query: 'dahua', status: 'Processing' }).map((c) => c._id)).toEqual(['3']);
  });
});

import { validateTz } from './validateTz.js';
describe('validateTz', () => {
  it('allows empty, accepts offsets, rejects names', () => {
    expect(validateTz('')).toBeNull();
    expect(validateTz('+05:30')).toBeNull();
    expect(validateTz('-04:00')).toBeNull();
    expect(validateTz('IST')).toBeTruthy();
    expect(validateTz('5:30')).toBeTruthy();
  });
});
