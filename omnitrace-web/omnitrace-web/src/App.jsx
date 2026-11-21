import React, { useState } from 'react';
import { Link, Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import { getToken, setToken } from './api.js';
import Login from './pages/Login.jsx';
import CaseList from './pages/CaseList.jsx';
import NewCase from './pages/NewCase.jsx';
import CaseDetail from './pages/CaseDetail.jsx';

function Protected({ authed, children }) {
  return authed ? children : <Navigate to="/login" replace />;
}

export default function App() {
  const [authed, setAuthed] = useState(Boolean(getToken()));
  const navigate = useNavigate();

  const onLogin = (token) => { setToken(token); setAuthed(true); navigate('/'); };
  const onLogout = () => { setToken(null); setAuthed(false); navigate('/login'); };

  return (
    <>
      <header className="topbar">
        <Link to="/" className="brand">OmniTrace</Link>
        {authed && (
          <nav>
            <Link to="/">Cases</Link>
            <Link to="/new">New Case</Link>
            <button className="link" onClick={onLogout}>Log out</button>
          </nav>
        )}
      </header>
      <main className="page">
        <Routes>
          <Route path="/login" element={<Login onLogin={onLogin} />} />
          <Route path="/" element={<Protected authed={authed}><CaseList /></Protected>} />
          <Route path="/new" element={<Protected authed={authed}><NewCase /></Protected>} />
          <Route path="/case/:id" element={<Protected authed={authed}><CaseDetail /></Protected>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </>
  );
}
