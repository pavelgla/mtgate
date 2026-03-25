'use strict';

const { InlineKeyboard } = require('grammy');

function buyKeyboard() {
  return new InlineKeyboard()
    .text('Купить 1 мес — 100₽', 'buy_1month').row()
    .text('Купить 3 мес — 200₽', 'buy_3month');
}

async function checkExpiringSoon(bot, db) {
  try {
    const subs = db.getExpiringSoon(3);
    for (const sub of subs) {
      try {
        const expires = new Date(sub.expires_at + 'Z');
        const diffMs = expires - Date.now();
        const diffHours = Math.round(diffMs / 3600000);
        const timeStr = diffHours < 24
          ? `${diffHours} ч.`
          : `${Math.ceil(diffMs / 86400000)} дн.`;

        await bot.api.sendMessage(
          sub.telegram_id,
          `⚠️ Ваша подписка заканчивается через ${timeStr}.\n\nПродлите её, чтобы не потерять доступ:`,
          { reply_markup: buyKeyboard() }
        );
        db.markNotified(sub.id);
      } catch (err) {
        console.error(`[scheduler] checkExpiringSoon: failed to notify ${sub.telegram_id}:`, err.message);
      }
    }
  } catch (err) {
    console.error('[scheduler] checkExpiringSoon error:', err.message);
  }
}

async function checkExpired(bot, db, mtgateClient) {
  try {
    const subs = db.getExpired();
    for (const sub of subs) {
      try {
        if (sub.mtgate_username) {
          await mtgateClient.toggleProxyUser(sub.mtgate_username);
        }
        db.expireSubscription(sub.id);
        await bot.api.sendMessage(
          sub.telegram_id,
          '❌ Ваша подписка закончилась. Доступ к прокси приостановлен.\n\nОформите новую подписку:',
          { reply_markup: buyKeyboard() }
        );
      } catch (err) {
        console.error(`[scheduler] checkExpired: failed to process sub ${sub.id}:`, err.message);
      }
    }
  } catch (err) {
    console.error('[scheduler] checkExpired error:', err.message);
  }
}

function startScheduler(bot, db, mtgateClient) {
  // Run immediately on start (catch up if bot was down)
  checkExpiringSoon(bot, db);
  checkExpired(bot, db, mtgateClient);

  // Then on schedule
  setInterval(() => checkExpiringSoon(bot, db), 12 * 60 * 60 * 1000);
  setInterval(() => checkExpired(bot, db, mtgateClient), 60 * 60 * 1000);

  console.log('[scheduler] started');
}

module.exports = { startScheduler };
