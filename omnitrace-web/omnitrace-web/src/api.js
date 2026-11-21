// All calls to Khushbu's Express API live here. Pages never call fetch directly.
const TOKEN_KEY = 'omnitrace_token';

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setToken = (t) => (t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY));

async function request(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: {
      ...(options.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
      ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
      ...options.headers,
    },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.error || `Request failed (${res.status})`), { status: res.status, data });
  return data;
}

export const register = (body) => request('/api/auth/register', { method: 'POST', body: JSON.stringify(body) });
export const login = (body) => request('/api/auth/login', { method: 'POST', body: JSON.stringify(body) });
export const listCases = () => request('/api/cases');
export const getCase = (id) => request(`/api/case/${id}`);

// fetch() cannot report upload progress, so acquire uses XMLHttpRequest.
export function acquire({ file, oem, dvrTz, onProgress }) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/acquire');
    xhr.setRequestHeader('Authorization', `Bearer ${getToken()}`);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* non-JSON error body */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(Object.assign(new Error(data.error || `Upload failed (${xhr.status})`), { status: xhr.status, data }));
    };
    xhr.onerror = () => reject(new Error('Network error: is the API running on port 4000?'));
    const form = new FormData();
    form.append('image', file);
    form.append('oem', oem);
    if (dvrTz) form.append('dvr_tz', dvrTz);
    xhr.send(form);
  });
}

// PDF needs the auth header, so we fetch it as a blob and open it.
export async function openReportPdf(id) {
  const res = await fetch(`/api/report/${id}`, { headers: { Authorization: `Bearer ${getToken()}` } });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Report unavailable (${res.status})`);
  }
  window.open(URL.createObjectURL(await res.blob()), '_blank');
}

// Clip files need the auth header too, so fetch as a blob. Returns a blob.
export async function fetchClipBlob(caseId, clipId, fmt) {
  const res = await fetch(`/api/case/${caseId}/clip/${clipId}/${fmt}`, { headers: { Authorization: `Bearer ${getToken()}` } });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Download failed (${res.status})`);
  }
  return res.blob();
}

export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
