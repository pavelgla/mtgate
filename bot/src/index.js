'use strict';

require('dotenv').config();

const express = require('express');
const db = require('./database');
const { createBot } = require('./bot');
const { createResultHandler, successPage, failPage } = require('./robokassa');
const yookassa = require('./yookassa');
const mtgateClient = require('./mtgate-client');
const { startScheduler } = require('./scheduler');

db.initDatabase();

const bot = createBot();

const app = express();
app.use(express.json());
app.use(express.urlencoded({ extended: false }));

app.post('/yookassa/webhook', yookassa.createWebhookHandler(bot, db, mtgateClient));
app.get('/yookassa/return', yookassa.returnPage);

app.post('/robokassa/result', createResultHandler(bot, db, mtgateClient));
app.get('/robokassa/success', successPage);
app.get('/robokassa/fail', failPage);

const PORT = process.env.EXPRESS_PORT || 3001;
app.listen(PORT, () => {
  console.log(`Express server listening on port ${PORT}`);
});

bot.start().then(() => {
  console.log('Bot started');
  startScheduler(bot, db, mtgateClient);
}).catch((err) => {
  console.error('Failed to start bot:', err);
  process.exit(1);
});
