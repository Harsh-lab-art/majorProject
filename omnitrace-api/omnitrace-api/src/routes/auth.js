const express = require('express');
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const config = require('../config');

module.exports = (store) => {
  const r = express.Router();
  const sign = (u) => jwt.sign({ id: u._id, email: u.email }, config.jwtSecret, { expiresIn: '12h' });

  r.post('/register', async (req, res) => {
    const { name, email, password } = req.body || {};
    if (!name || !email || !password) return res.status(400).json({ error: 'name, email, password required' });
    if (await store.findUserByEmail(email)) return res.status(409).json({ error: 'email already registered' });
    const user = await store.createUser({ name, email, passwordHash: await bcrypt.hash(password, 10), role: 'investigator' });
    res.status(201).json({ token: sign(user), user: { id: user._id, name, email } });
  });

  r.post('/login', async (req, res) => {
    const { email, password } = req.body || {};
    const user = email && (await store.findUserByEmail(email));
    if (!user || !(await bcrypt.compare(password || '', user.passwordHash))) {
      return res.status(401).json({ error: 'invalid credentials' });
    }
    res.json({ token: sign(user), user: { id: user._id, name: user.name, email } });
  });
  return r;
};
