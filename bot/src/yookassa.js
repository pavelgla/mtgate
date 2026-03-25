'use strict';

const crypto = require('crypto');
const {
  completePayment, getUserByTelegramId, createSubscription, updateUserMtgate,
} = require('./database');
const { createProxyUser, extendProxyUser, toggleProxyUser, getProxyUser } = require('./mtgate-client');

const YOOKASSA_API_URL = 'https://api.yookassa.ru/v3';

function authHeader() {
  const shopId = process.env.YOOKASSA_SHOP_ID;
  const secretKey = process.env.YOOKASSA_SECRET_KEY;
  return 'Basic ' + Buffer.from(`${shopId}:${secretKey}`).toString('base64');
}

async function createPayment({ amount, description, returnUrl, telegramId, plan, paymentDbId }) {
  const res = await fetch(YOOKASSA_API_URL + '/payments', {
    method: 'POST',
    headers: {
      'Authorization': authHeader(),
      'Idempotence-Key': crypto.randomUUID(),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      amount: {
        value: amount.toFixed(2),
        currency: 'RUB',
      },
      capture: true,
      confirmation: {
        type: 'redirect',
        return_url: returnUrl,
      },
      description,
      metadata: {
        telegram_id: String(telegramId),
        plan,
        payment_db_id: String(paymentDbId),
      },
    }),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    const err = new Error(`YooKassa createPayment ${res.status}: ${text}`);
    console.error(err.message);
    throw err;
  }

  const data = await res.json();
  return {
    paymentId: data.id,
    confirmationUrl: data.confirmation.confirmation_url,
    status: data.status,
  };
}

async function getPayment(paymentId) {
  const res = await fetch(YOOKASSA_API_URL + '/payments/' + paymentId, {
    headers: {
      'Authorization': authHeader(),
      'Content-Type': 'application/json',
    },
  });

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    const err = new Error(`YooKassa getPayment ${res.status}: ${text}`);
    console.error(err.message);
    throw err;
  }

  return res.json();
}

function createWebhookHandler(bot, db, mtgateClient) {
  return async (req, res) => {
    try {
      const { type, event, object } = req.body || {};

      if (type !== 'notification') {
        return res.status(200).send('OK');
      }

      if (event === 'payment.succeeded') {
        const { metadata, amount } = object;
        const telegramId = parseInt(metadata.telegram_id, 10);
        const plan = metadata.plan;
        const paymentDbId = parseInt(metadata.payment_db_id, 10);

        db.completePayment(paymentDbId);

        const days = plan === '3month' ? 90 : plan === '1month' ? 30 : 0;

        const user = db.getUserByTelegramId(telegramId);
        if (!user) {
          console.error(`YooKassa webhook: user not found for telegram_id=${telegramId}`);
          return res.status(200).send('OK');
        }

        let tgLink;
        try {
          if (user.mtgate_username) {
            await mtgateClient.extendProxyUser(user.mtgate_username, days);
            const proxy = await mtgateClient.getProxyUser(user.mtgate_username);
            if (proxy && proxy.enabled === false) {
              await mtgateClient.toggleProxyUser(user.mtgate_username);
            }
            tgLink = proxy ? proxy.tg_link : null;
          } else {
            const proxyName = `tg_${telegramId}`;
            const proxy = await mtgateClient.createProxyUser(proxyName, days);
            db.updateUserMtgate(user.id, proxy.name, proxy.secret);
            tgLink = proxy.tg_link;
          }
        } catch (err) {
          console.error('YooKassa webhook: MTGate error:', err.message);
        }

        const sub = db.createSubscription(user.id, plan, days);
        const expiresStr = sub.expires_at.replace('T', ' ').slice(0, 10);

        const msg = tgLink
          ? `Оплата получена! Подписка активна до ${expiresStr}.\n\nВаша ссылка для подключения:\n${tgLink}`
          : `Оплата получена! Подписка активна до ${expiresStr}.`;

        await bot.api.sendMessage(telegramId, msg).catch((e) => {
          console.error('YooKassa webhook: failed to send message:', e.message);
        });

      } else if (event === 'payment.canceled') {
        const { metadata } = object;
        const telegramId = parseInt(metadata.telegram_id, 10);
        const paymentDbId = parseInt(metadata.payment_db_id, 10);

        try {
          db.failPayment(paymentDbId);
        } catch (err) {
          console.error('YooKassa webhook: failed to mark payment as failed:', err.message);
        }

        await bot.api.sendMessage(
          telegramId,
          'Платёж отменён. Попробуйте снова: /start'
        ).catch((e) => {
          console.error('YooKassa webhook: failed to send cancel message:', e.message);
        });
      }

      res.status(200).send('OK');
    } catch (err) {
      console.error('YooKassa webhook error:', err.message);
      res.status(200).send('OK');
    }
  };
}

function returnPage(req, res) {
  const botUsername = process.env.BOT_USERNAME || 'BOT_USERNAME';
  res.send(`<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Оплата — MTGate</title>
<style>
  body { font-family: system-ui, sans-serif; background: #050d1a; color: #e2e8f0;
         display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }
  .card { text-align: center; padding: 2.5rem 2rem; max-width: 360px; }
  h1 { font-size: 1.4rem; margin-bottom: 0.75rem; color: #fff; }
  p { color: #64748b; line-height: 1.6; margin-bottom: 1.75rem; }
  a { display: inline-block; background: #3b82f6; color: #fff; font-weight: 600;
      padding: 0.75rem 1.75rem; border-radius: 8px; text-decoration: none; }
  a:hover { background: #60a5fa; }
</style>
</head>
<body>
<div class="card">
  <h1>Спасибо!</h1>
  <p>Вернитесь в Telegram-бот.<br>Статус оплаты обновится автоматически.</p>
  <a href="https://t.me/${botUsername}">Открыть бот</a>
</div>
</body>
</html>`);
}

module.exports = { createPayment, getPayment, createWebhookHandler, returnPage };
