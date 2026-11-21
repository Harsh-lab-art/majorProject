// In-memory store: lets the API run with zero setup. Data is lost on restart.
const { randomUUID } = require('crypto');

function createMemoryStore() {
  const users = new Map();
  const cases = new Map();
  return {
    kind: 'memory',
    async createUser(u) { const doc = { _id: randomUUID(), ...u }; users.set(doc._id, doc); return doc; },
    async findUserByEmail(email) { return [...users.values()].find((u) => u.email === email) || null; },
    async createCase(c) {
      const doc = { _id: randomUUID(), createdAt: new Date().toISOString(), clips: [], ...c };
      cases.set(doc._id, doc); return doc;
    },
    async updateCase(id, patch) {
      const doc = cases.get(id); if (!doc) return null;
      Object.assign(doc, patch); return doc;
    },
    async getCase(id) { return cases.get(id) || null; },
    async listCases(userId) {
      return [...cases.values()].filter((c) => c.userId === userId)
        .sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1));
    },
  };
}
module.exports = { createMemoryStore };
