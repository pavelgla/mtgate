'use strict';

const { Bot, InlineKeyboard } = require('grammy');
const {
  createUser, getUserByTelegramId, updateUserMtgate,
  createSubscription, getActiveSubscription, createPayment, hasUsedTrial,
} = require('./database');
const { createProxyUser } = require('./mtgate-client');
const { generatePaymentUrl } = require('./robokassa');
const yookassa = require('./yookassa');

const PAYMENT_PROVIDER = process.env.PAYMENT_PROVIDER || 'yookassa';
const SERVER_HOST = process.env.SERVER_HOST || '127.0.0.1';

const PLANS = [
  { id: 'buy_1month', label: 'Купить 1 мес — 100₽', plan: '1month', amount: 100, days: 30 },
  { id: 'buy_3month', label: 'Купить 3 мес — 200₽', plan: '3month', amount: 200, days: 90 },
];

function mainKeyboard() {
  return new InlineKeyboard()
    .text('Попробовать бесплатно (3 дня)', 'trial').row()
    .text('Тарифы', 'plans').row()
    .text('Мой аккаунт', 'account');
}

function plansKeyboard() {
  return new InlineKeyboard()
    .text('Купить 1 мес — 100₽', 'buy_1month').row()
    .text('Купить 3 мес — 200₽', 'buy_3month');
}

function createBot() {
  const bot = new Bot(process.env.BOT_TOKEN);

  bot.command('start', async (ctx) => {
    await ctx.reply(
      'MTGate — быстрый и стабильный прокси для Telegram.\n\nВыберите действие:',
      { reply_markup: mainKeyboard() }
    );
  });

  bot.callbackQuery('trial', async (ctx) => {
    await ctx.answerCallbackQuery();
    const tgId = ctx.from.id;

    let user = getUserByTelegramId(tgId);
    if (!user) user = createUser(tgId, ctx.from.username);

    if (hasUsedTrial(user.id)) {
      return ctx.reply('Пробный период уже использован.', { reply_markup: plansKeyboard() });
    }

    const proxyName = `tg_${tgId}`;
    const proxy = await createProxyUser(proxyName, 3);
    updateUserMtgate(user.id, proxy.name, proxy.secret);
    createSubscription(user.id, 'trial', 3);

    await ctx.reply(
      `Прокси активирован на 3 дня!\n\nВаша ссылка для подключения:\n${proxy.tg_link}\n\nНажмите — прокси подключится автоматически.`
    );
  });

  bot.callbackQuery('plans', async (ctx) => {
    await ctx.answerCallbackQuery();
    await ctx.reply(
      '📋 Тарифы:\n\n• 1 месяц — 100₽\n• 3 месяца — 200₽ (экономия 33%)',
      { reply_markup: plansKeyboard() }
    );
  });

  for (const { id, label, plan, amount } of PLANS) {
    bot.callbackQuery(id, async (ctx) => {
      await ctx.answerCallbackQuery();
      const tgId = ctx.from.id;

      let user = getUserByTelegramId(tgId);
      if (!user) user = createUser(tgId, ctx.from.username);

      const payment = createPayment(user.id, plan, amount);

      let payUrl;
      try {
        if (PAYMENT_PROVIDER === 'yookassa') {
          const returnUrl = `http://${SERVER_HOST}/yookassa/return?telegram_id=${tgId}`;
          const result = await yookassa.createPayment({
            amount,
            description: label,
            returnUrl,
            telegramId: tgId,
            plan,
            paymentDbId: payment.id,
          });
          payUrl = result.confirmationUrl;
        } else {
          payUrl = generatePaymentUrl({
            invId: payment.robokassa_inv_id,
            amount,
            description: label,
            telegramId: tgId,
          });
        }
      } catch (err) {
        console.error('Payment creation error:', err.message);
        return ctx.reply('Ошибка создания платежа, попробуйте позже.');
      }

      await ctx.reply('Для оплаты нажмите кнопку ниже:', {
        reply_markup: new InlineKeyboard().url('Оплатить', payUrl),
      });
    });
  }

  bot.callbackQuery('account', async (ctx) => {
    await ctx.answerCallbackQuery();
    const tgId = ctx.from.id;
    const user = getUserByTelegramId(tgId);

    if (!user) {
      return ctx.reply('Вы не зарегистрированы. Начните с пробного периода!', {
        reply_markup: mainKeyboard(),
      });
    }

    const sub = getActiveSubscription(user.id);
    if (!sub) {
      return ctx.reply('У вас нет активной подписки.', { reply_markup: plansKeyboard() });
    }

    const planNames = { trial: 'Пробный', '1month': '1 месяц', '3month': '3 месяца' };
    const expires = sub.expires_at.replace('T', ' ').slice(0, 16);
    let text = `👤 Аккаунт\n\nТариф: ${planNames[sub.plan] || sub.plan}\nДействует до: ${expires}`;
    if (user.mtgate_username) {
      const proxy = await require('./mtgate-client').getProxyUser(user.mtgate_username).catch(() => null);
      if (proxy && proxy.tg_link) text += `\n\nСсылка:\n${proxy.tg_link}`;
    }

    await ctx.reply(text, {
      reply_markup: new InlineKeyboard().text('Продлить', 'plans').text('Тарифы', 'plans'),
    });
  });

  bot.catch((err) => {
    console.error('Bot error:', err);
    const ctx = err.ctx;
    if (ctx) {
      ctx.reply('Произошла ошибка, попробуйте позже.').catch(() => {});
    }
  });

  return bot;
}

module.exports = { createBot };
