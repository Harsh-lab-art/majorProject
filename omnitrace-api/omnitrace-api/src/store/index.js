const { createMemoryStore } = require('./memory');
async function createStore(mongoUri) {
  if (!mongoUri) return createMemoryStore();
  const { createMongoStore } = require('./mongo');
  return createMongoStore(mongoUri);
}
module.exports = { createStore };
