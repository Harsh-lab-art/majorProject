require('dotenv').config();

module.exports = {
  port: process.env.PORT || 4000,
  jwtSecret: process.env.JWT_SECRET || 'dev-secret-change-me',
  mongoUri: process.env.MONGO_URI || '',
  engineMode: process.env.ENGINE_MODE || 'mock',
  engineUrl: process.env.ENGINE_URL || 'http://localhost:5000',
};
