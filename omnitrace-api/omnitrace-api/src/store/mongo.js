// MongoDB store. Schemas mirror the ER diagram in the design doc (Section 7.3).
const mongoose = require('mongoose');

const ClipSchema = new mongoose.Schema({
  clip_id: String, offset: Number, length: Number,
  start_ts: String, end_ts: String, channel: Number,
  status: String, codec: String, md5: String, sha256: String,
}, { _id: false });

const UserSchema = new mongoose.Schema({
  name: String,
  email: { type: String, unique: true },
  passwordHash: String,
  role: { type: String, default: 'investigator' },
});

const CaseSchema = new mongoose.Schema({
  userId: { type: String, index: true },
  oem: String,
  oemConfidence: String,
  filename: String,
  dvrTz: String,
  status: { type: String, enum: ['Processing', 'Recovered', 'Hashed', 'Report Ready', 'Failed'], default: 'Processing' },
  engineCaseId: String,
  hashMD5: String,
  hashSHA256: String,
  clips: [ClipSchema],
  reportJsonUrl: String,
  reportPdfUrl: String,
  error: String,
}, { timestamps: true });

async function createMongoStore(uri) {
  await mongoose.connect(uri);
  const User = mongoose.model('User', UserSchema);
  const Case = mongoose.model('Case', CaseSchema);
  const lean = (d) => (d ? { ...d.toObject(), _id: String(d._id) } : null);
  return {
    kind: 'mongo',
    async createUser(u) { return lean(await User.create(u)); },
    async findUserByEmail(email) { return lean(await User.findOne({ email })); },
    async createCase(c) { return lean(await Case.create(c)); },
    async updateCase(id, patch) { return lean(await Case.findByIdAndUpdate(id, patch, { new: true })); },
    async getCase(id) { return lean(await Case.findById(id)); },
    async listCases(userId) { return (await Case.find({ userId }).sort({ createdAt: -1 })).map(lean); },
  };
}
module.exports = { createMongoStore };
