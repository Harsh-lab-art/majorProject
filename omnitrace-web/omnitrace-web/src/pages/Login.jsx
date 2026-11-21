import React, { useState } from 'react';
import { login, register } from '../api.js';

export default function Login({ onLogin }) {
  const [mode, setMode] = useState('login');
  const [form, setForm] = useState({ name: '', email: '', password: '' });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    setError(''); setBusy(true);
    try {
      const res = mode === 'login' ? await login(form) : await register(form);
      onLogin(res.token);
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }

  return (
    <form className="panel narrow" onSubmit={submit}>
      <h2>{mode === 'login' ? 'Sign in' : 'Create account'}</h2>
      {mode === 'register' && (
        <label className="field"><span>Name</span><input value={form.name} onChange={set('name')} required /></label>
      )}
      <label className="field"><span>Email</span><input type="email" value={form.email} onChange={set('email')} required /></label>
      <label className="field"><span>Password</span><input type="password" value={form.password} onChange={set('password')} required /></label>
      {error && <p className="error">{error}</p>}
      <button className="primary" disabled={busy}>{busy ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Register'}</button>
      <button type="button" className="link" onClick={() => setMode(mode === 'login' ? 'register' : 'login')}>
        {mode === 'login' ? 'Need an account? Register' : 'Have an account? Sign in'}
      </button>
    </form>
  );
}
