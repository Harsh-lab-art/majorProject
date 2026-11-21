const express = require('express');
const cors = require('cors');

function createApp(store) {
  const app = express();
  app.use(cors());
  app.use(express.json());
  app.get('/health', (_req, res) => res.json({ status: 'ok', store: store.kind }));
  app.use('/api/auth', require('./routes/auth')(store));
  app.use('/api', require('./routes/cases')(store));
  app.use((err, _req, res, _next) => { console.error(err); res.status(500).json({ error: 'internal error' }); });
  return app;
}
module.exports = { createApp };
