// Optional field: empty is fine, otherwise it must look like +05:30 or -04:00.
export function validateTz(value) {
  const v = (value || '').trim();
  if (!v) return null;
  return /^[+-]\d{2}:\d{2}$/.test(v) ? null : 'DVR clock offset must look like +05:30';
}
