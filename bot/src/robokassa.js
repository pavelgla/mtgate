'use strict';

const crypto = require('crypto');
const {
  completePayment, getUserByTelegramId, createSubscription, updateUserMtgate,
} = require('./database');
const { createProxyUser, extendProxyUser, toggleProxyUser, getProxyUser } = require('./mtgate-client');

function md5(str) {
  return crypto.createHash('md5').update(str).digest('hex');
}

function generatePaymentUrl({ invId, amount, description, telegramId }) {
  const login = process.env.ROBOKASSA_LOGIN;
  const pass1 = process.env.ROBOKASSA_PASS1;
  const isTest = process.env.ROBOKASSA_TEST_MODE === 'true' ? '1' : '0';

  // Shp_ params in alphabetical order included in signature
  const sig = md5(`${login}:${amount}:${invId}:${pass1}:Shp_telegram_id=${telegramId}`);

  const params = new URLSearchParams({
    MerchantLogin: login,
    OutSum: amount,
    InvId: invId,
    Description: description,
    SignatureValue: sig,
    Shp_telegram_id: telegramId,
  });
  if (isTest === '1') params.set('IsTest', '1');

  return `https://auth.robokassa.ru/Merchant/Index.aspx?${params.toString()}`;
}

function verifyResultSignature({ outSum, invId, signatureValue, shpTelegramId }) {
  const pass2 = process.env.ROBOKASSA_PASS2;
  const expected = md5(`${outSum}:${invId}:${pass2}:Shp_telegram_id=${shpTelegramId}`);
  return expected.toLowerCase() === signatureValue.toLowerCase();
}

function createResultHandler(bot, db, mtgateClient) {
  return async (req, res) => {
    const { OutSum, InvId, SignatureValue, Shp_telegram_id } = req.body;

    if (!verifyResultSignature({
      outSum: OutSum,
      invId: InvId,
      signatureValue: SignatureValue,
      shpTelegramId: Shp_telegram_id,
    })) {
      return res.status(400).send('Invalid signature');
    }

    const payment = completePayment(InvId);

    const amount = parseFloat(OutSum);
    const days = amount >= 200 ? 90 : 30;
    const plan = amount >= 200 ? '3month' : '1month';

    const telegramId = parseInt(Shp_telegram_id, 10);
    const user = getUserByTelegramId(telegramId);
    if (!user) {
      return res.status(400).send('User not found');
    }

    let tgLink;
    try {
      if (user.mtgate_username) {
        const proxyName = user.mtgate_username;
        await extendProxyUser(proxyName, days);
        const proxy = await getProxyUser(proxyName);
        // Re-enable if disabled
        if (proxy && proxy.enabled === false) {
          await toggleProxyUser(proxyName);
        }
        tgLink = proxy ? proxy.tg_link : null;
      } else {
        const proxyName = `tg_${telegramId}`;
        const proxy = await createProxyUser(proxyName, days);
        updateUserMtgate(user.id, proxy.name, proxy.secret);
        tgLink = proxy.tg_link;
      }
    } catch (err) {
      console.error('MTGate error on payment result:', err);
    }

    const sub = createSubscription(user.id, plan, days);
    const expiresStr = sub.expires_at.replace('T', ' ').slice(0, 10);

    const msg = tgLink
      ? `Оплата получена! Подписка до ${expiresStr}.\n\nСсылка для подключения:\n${tgLink}`
      : `Оплата получена! Подписка до ${expiresStr}.`;

    await bot.api.sendMessage(telegramId, msg).catch((e) => {
      console.error('Failed to send payment confirmation:', e);
    });

    res.send(`OK${InvId}`);
  };
}

function successPage(req, res) {
  res.send('<html><body><p>Оплата прошла успешно! Вернитесь в Telegram-бот для получения ссылки.</p></body></html>');
}

function failPage(req, res) {
  res.send('<html><body><p>Оплата не завершена. Вернитесь в бот и попробуйте снова.</p></body></html>');
}

module.exports = { generatePaymentUrl, verifyResultSignature, createResultHandler, successPage, failPage };
