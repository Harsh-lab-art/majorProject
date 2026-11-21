process.env.ENGINE_MODE = 'mock';
const { test, before, after } = require('node:test');
const assert = require('node:assert');
const { createMemoryStore } = require('../src/store/memory');
const { createApp } = require('../src/app');

let server, base;
before(async () => {
  server = createApp(createMemoryStore()).listen(0);
  base = `http://localhost:${server.address().port}`;
});
after(() => server.close());

const j = (path, opts = {}) => fetch(base + path, opts);
const post = (path, body, token) => j(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', ...(token && { Authorization: `Bearer ${token}` }) },
  body: JSON.stringify(body),
});
async function register(email) {
  const res = await post('/api/auth/register', { name: 'T', email, password: 'pw12345' });
  return (await res.json()).token;
}
async function waitForStatus(token, id, want) {
  for (let i = 0; i < 50; i++) {
    const c = await (await j(`/api/case/${id}`, { headers: { Authorization: `Bearer ${token}` } })).json();
    if (c.status === want) return c;
    await new Promise((r) => setTimeout(r, 50));
  }
  throw new Error(`case never reached ${want}`);
}
function uploadForm() {
  const f = new FormData();
  f.append('image', new Blob([Buffer.from('fake image')]), 'disk.img');
  f.append('oem', 'Dahua');
  return f;
}

test('health', async () => {
  assert.strictEqual((await (await j('/health')).json()).status, 'ok');
});

test('register, duplicate rejected, login works, bad password rejected', async () => {
  await register('a@x.com');
  assert.strictEqual((await post('/api/auth/register', { name: 'T', email: 'a@x.com', password: 'p' })).status, 409);
  assert.strictEqual((await post('/api/auth/login', { email: 'a@x.com', password: 'pw12345' })).status, 200);
  assert.strictEqual((await post('/api/auth/login', { email: 'a@x.com', password: 'nope' })).status, 401);
});

test('protected routes need a token', async () => {
  assert.strictEqual((await j('/api/cases')).status, 401);
});

test('acquire -> list -> detail, with hashes and clips stored', async () => {
  const token = await register('b@x.com');
  const res = await j('/api/acquire', { method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: uploadForm() });
  assert.strictEqual(res.status, 202);
  const started = await res.json();
  assert.strictEqual(started.status, 'Processing');
  const c = await waitForStatus(token, started._id, 'Report Ready');
  assert.strictEqual(c.oem, 'Dahua');
  assert.strictEqual(c.clips.length, 2);
  assert.strictEqual(c.hashSHA256.length, 64);

  const list = await (await j('/api/cases', { headers: { Authorization: `Bearer ${token}` } })).json();
  assert.strictEqual(list.length, 1);
  const detail = await j(`/api/case/${c._id}`, { headers: { Authorization: `Bearer ${token}` } });
  assert.strictEqual(detail.status, 200);
});

test("a user cannot read someone else's case", async () => {
  const t1 = await register('c@x.com');
  const t2 = await register('d@x.com');
  const c = await (await j('/api/acquire', { method: 'POST', headers: { Authorization: `Bearer ${t1}` }, body: uploadForm() })).json();
  assert.strictEqual((await j(`/api/case/${c._id}`, { headers: { Authorization: `Bearer ${t2}` } })).status, 404);
});

test('acquire without a file is a 400', async () => {
  const token = await register('e@x.com');
  const res = await j('/api/acquire', { method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: new FormData() });
  assert.strictEqual(res.status, 400);
});

test('a malformed dvr_tz is rejected up front with 400', async () => {
  const token = await register('f@x.com');
  const f = uploadForm();
  f.append('dvr_tz', 'IST');
  const res = await j('/api/acquire', { method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: f });
  assert.strictEqual(res.status, 400);
});

test('a valid dvr_tz is stored on the case', async () => {
  const token = await register('g@x.com');
  const f = uploadForm();
  f.append('dvr_tz', '+05:30');
  const res = await j('/api/acquire', { method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: f });
  assert.strictEqual(res.status, 202);
  assert.strictEqual((await res.json()).dvrTz, '+05:30');
});

test('clip download: unknown clip 404, bad format 400, mock engine has no files (501)', async () => {
  const token = await register('h@x.com');
  const started = await (await j('/api/acquire', { method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: uploadForm() })).json();
  const c = await waitForStatus(token, started._id, 'Report Ready');
  const get = (p) => j(`/api/case/${c._id}/clip/${p}`, { headers: { Authorization: `Bearer ${token}` } });
  assert.strictEqual((await get('nope/mp4')).status, 404);
  assert.strictEqual((await get('dahua-000000/avi')).status, 400);
  assert.strictEqual((await get('dahua-000001/dav')).status, 404);   // carved clip with no export
  assert.strictEqual((await get('dahua-000000/dav')).status, 501);   // mock mode serves no files
  const other = await register('i@x.com');
  assert.strictEqual((await j(`/api/case/${c._id}/clip/dahua-000000/dav`, { headers: { Authorization: `Bearer ${other}` } })).status, 404);
});
