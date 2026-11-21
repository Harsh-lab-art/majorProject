const config = require('./config');
const { createStore } = require('./store');
const { createApp } = require('./app');

(async () => {
  const store = await createStore(config.mongoUri);
  createApp(store).listen(config.port, () =>
    console.log(`omnitrace-api on :${config.port} (store=${store.kind}, engine=${config.engineMode})`));
})();
